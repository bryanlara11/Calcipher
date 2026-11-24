"""Synthetic regression fixtures; these tests are not evidence of live collection or human review."""
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select

from app.collect_sources import CollectionStopped, collect_entries, parse_entry
from app.ingest import load_reviewed, read_reviewed
from app.models import NutritionCatalog
from app.schemas import SearchConstraints
from app.services.brand_matcher import load_brand_matcher
from app.services.embedder import HashingEmbedder
from app.services.hybrid_rag import brand_items, nearby_branches, retrieve
from app.services.source_review import prepare_review
from tests.conftest import UPM_LAT, UPM_LNG, add_branch, add_item
from tests.test_ingest_pipeline import _use_test_db


def candidate(**changes):
    row = dict(record_id="TEST-001", brand_name="Synthetic Chain", food_item="Synthetic bowl", calories=420,
               protein_g=30, carbs_g=None, fat_g=None, serving_size=None, price_php=None,
               source_post_url="https://example.org/synthetic-original", secondary_source_url="https://example.org/synthetic-entry",
               source_classification="community_reported", collected_at="2026-11-25",
               permission_reference="synthetic-permission", field_sources={"calories":"https://example.org/synthetic-original"},
               source_images=["SYNTHETIC_2.png"])
    return {**row, **changes}


def files(tmp_path, rows=None, approved=None):
    source = tmp_path / "candidates.json"; source.write_text(json.dumps(rows or [candidate()]))
    receipt = None
    if approved is not None:
        receipt = tmp_path / "receipt.json"
        receipt.write_text(json.dumps(dict(dataset_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                                         approved_record_ids=approved, reviewed_by="synthetic reviewer",
                                         reviewed_at="2026-11-25T00:00:00+08:00", reference="synthetic-test-only")))
    return source, tmp_path / "review.csv", receipt


def test_preparation_never_approves_automatically_and_preserves_unknowns(tmp_path):
    source, output, _ = files(tmp_path)
    summary = prepare_review(source, output)
    reviewed = read_reviewed(output)
    assert summary["approved"] == 0 and summary["pending"] == 1
    assert reviewed.verified == [False] and not reviewed.errors
    item = reviewed.items[0]
    assert item.serving_size is None and item.price_php is None and item.carbs_g is None
    assert item.provenance["source_classification"] == "community_reported"
    assert item.source_images == ["SYNTHETIC_2.png"]


def test_receipt_approves_only_selected_ids(tmp_path):
    source, output, receipt = files(tmp_path, [candidate(), candidate(record_id="TEST-002", food_item="Other bowl")], ["TEST-002"])
    prepare_review(source, output, receipt)
    reviewed = read_reviewed(output)
    assert reviewed.verified == [False, True] and not reviewed.errors
    assert reviewed.items[1].provenance["approval"]["reviewed_by"] == "synthetic reviewer"
    assert "approval" not in reviewed.items[0].provenance


@pytest.mark.parametrize("mutation", ["source_changed", "unknown_id", "missing_reviewer"])
def test_invalid_approval_receipt_is_rejected(tmp_path, mutation):
    source, output, receipt = files(tmp_path, approved=["TEST-001"])
    data = json.loads(receipt.read_text())
    if mutation == "source_changed": source.write_text(source.read_text() + " ")
    if mutation == "unknown_id": data["approved_record_ids"] = ["NOT-IN-DATASET"]
    if mutation == "missing_reviewer": data["reviewed_by"] = ""
    receipt.write_text(json.dumps(data))
    with pytest.raises(ValueError): prepare_review(source, output, receipt)
    assert not output.exists()


def test_existing_review_file_is_not_silently_replaced(tmp_path):
    source, output, _ = files(tmp_path); output.write_text("human-owned correction")
    with pytest.raises(FileExistsError): prepare_review(source, output)
    assert output.read_text() == "human-owned correction"


def test_historical_or_unchecked_prices_do_not_become_budget_prices(tmp_path):
    source, output, _ = files(tmp_path, [candidate(price_php=45)])
    with pytest.raises(ValueError, match="separate checked-price review"): prepare_review(source, output)


@pytest.mark.db
async def test_persistent_import_approval_gate_sources_and_null_duplicate_key(tmp_path, db_session, monkeypatch, pg_url):
    _use_test_db(monkeypatch, pg_url)
    source, pending, _ = files(tmp_path)
    await add_branch(db_session, osm_id=501, poi_name="Synthetic Chain - Test", lat=UPM_LAT, lng=UPM_LNG, slug="synthetic chain")
    first = await load_reviewed(prepare_and_return(source, pending))
    assert first["written"] == 1
    # A repeated pending import with NULL serving is still a single row.
    await load_reviewed(pending)
    assert (await db_session.execute(select(func.count()).select_from(NutritionCatalog))).scalar_one() == 1
    query = dict(lat=UPM_LAT, lng=UPM_LNG, constraints=SearchConstraints(max_calories=420, max_distance_km=1), query_vec=HashingEmbedder().embed_one("bowl"))
    assert (await retrieve(db_session, **query)).items == []
    assert await brand_items(db_session, "synthetic chain") == []
    assert len(await load_brand_matcher(db_session)) == 0
    pins = await nearby_branches(db_session, lat=UPM_LAT, lng=UPM_LNG, radius_km=1, include_unmatched=True)
    assert pins[0].has_calorie_data is False
    await db_session.commit()
    _, approved_path, receipt = files(tmp_path, approved=["TEST-001"])
    approved_path = tmp_path / "approved.csv"
    prepare_review(source, approved_path, receipt)
    await load_reviewed(approved_path)
    result = await retrieve(db_session, **query)
    assert len(result.items) == 1 and result.items[0].is_verified
    item = result.items[0]
    assert item.source_post_url == "https://example.org/synthetic-original"
    assert item.source_classification == "community_reported" and str(item.collected_at) == "2026-11-25"
    assert item.serving_size is None and item.price_php is None
    assert "approval" not in item.model_dump()  # operator metadata is not a public API field
    assert (await nearby_branches(db_session, lat=UPM_LAT, lng=UPM_LNG, radius_km=1, include_unmatched=True))[0].has_calorie_data
    assert (await brand_items(db_session, "synthetic chain"))[0].source_images == ["SYNTHETIC_2.png"]
    # Exact caps, missing prices and unknown macros never produce invented matches.
    assert not (await retrieve(db_session, **{**query, "constraints":SearchConstraints(max_calories=419,max_distance_km=1)})).items
    assert not (await retrieve(db_session, **{**query, "constraints":SearchConstraints(max_price_php=250,max_distance_km=1)})).items


def prepare_and_return(source, output):
    prepare_review(source, output); return output


def _page(protein="20 g", visible="20 g", calories="400", serving="1 listed serving"):
    graph = {"@graph":[{"@type":"WebPage","description":"Synthetic item in the Philippines", "citation":"https://example.org/synthetic-original"},
              {"@type":"BreadcrumbList","itemListElement":[{"name":"Home"},{"name":"Synthetic Chain calories"},{"name":"Synthetic bowl"}]},
              {"@type":"MenuItem","description":"A community-logged synthetic fixture", "nutrition":{"calories":calories+" calories", "proteinContent":protein,"servingSize":serving}}]}
    return '<script type="application/ld+json">'+json.dumps(graph)+'</script><strong>Calories</strong><strong>'+calories+'</strong><dl><dt>Protein</dt><dd>'+visible+'</dd><dt>Total carbohydrates</dt><dd>Not published</dd><dt>Total fat</dt><dd>Not published</dd></dl>'


def test_secondary_parser_keeps_source_labels_and_missing_values():
    row = parse_entry(_page(), "https://worththecals.com/calories/test", brand="Synthetic Chain", record_id="T", collected_at="2026-11-25",permission_reference="synthetic")
    assert row["protein_g"] == 20 and row["carbs_g"] is None and row["serving_size"] is None
    assert row["verification_status"] == "pending" and row["price_php"] is None
    assert row["source_classification"] == "community_reported"


@pytest.mark.parametrize("doc", [_page(protein="20 kg"),_page(visible="25 g")])
def test_secondary_parser_rejects_ambiguous_units_or_hidden_disagreement(doc):
    with pytest.raises(ValueError): parse_entry(doc,"https://worththecals.com/calories/test",brand="Synthetic",record_id="T",collected_at="2026-11-25",permission_reference="synthetic")


def test_secondary_collection_stops_on_restrictions_without_retry(tmp_path):
    calls=[]
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200,text="User-agent: *\nAllow: /\n") if request.url.path == "/robots.txt" else httpx.Response(429,text="Rate limited")
    client=httpx.Client(transport=httpx.MockTransport(handler))
    output=tmp_path/'candidates.json'
    with pytest.raises(CollectionStopped,match="HTTP 429"):
        collect_entries([dict(record_id="T",brand_name="Synthetic",url="https://worththecals.com/calories/test")],output,permission_reference="synthetic",client=client,delay_s=0)
    assert len(calls)==2 and not output.exists()


def test_collector_requires_authorization_reference_and_safe_urls(tmp_path):
    with pytest.raises(ValueError,match="permission"):
        collect_entries([],tmp_path/'x',permission_reference="")
    with pytest.raises(ValueError):
        collect_entries([dict(url="https://example.org/private",brand_name="X",record_id="X")],tmp_path/'x',permission_reference="synthetic")


def test_assisted_regions_keep_item_and_slide_association_without_approval(tmp_path):
    from PIL import Image
    from app.ocr_regions import extract_regions
    Image.new("RGB",(200,100),"white").save(tmp_path/'synthetic-2.png')
    Image.new("RGB",(200,100),"white").save(tmp_path/'synthetic-3.png')
    regions=[dict(region_id="a",image="synthetic-2.png",bbox=[0,0,.5,1],item_hint="Synthetic A",source_url="https://example.org/synthetic/2"),
             dict(region_id="b",image="synthetic-3.png",bbox=[.5,0,1,1],item_hint="Synthetic B",source_url="https://example.org/synthetic/3")]
    texts=iter(["Synthetic A 110 kcal", "Synthetic B 210 kcal"])
    rows=extract_regions(tmp_path,regions,ocr_fn=lambda image:next(texts))
    assert rows[0]["source_image"]=="synthetic-2.png" and rows[0]["calorie_candidates"]==[110]
    assert rows[1]["source_image"]=="synthetic-3.png" and rows[1]["calorie_candidates"]==[210]
    assert all(r["verification_status"]=="pending" for r in rows)


def test_assisted_region_rejects_paths_outside_source_folder(tmp_path):
    from app.ocr_regions import extract_regions
    with pytest.raises(ValueError,match="inside raw_dir"):
        extract_regions(tmp_path,[dict(region_id="a",image="../private.png",bbox=[0,0,1,1],item_hint="X",source_url="https://example.org")],ocr_fn=lambda _:"")


def test_multiple_size_columns_require_human_association():
    from app.services.brand_matcher import BrandMatcher
    from app.services.ingestion_ocr import parse_nutrition_text
    items=parse_nutrition_text("Synthetic Chain", "Small Medium Large\nLatte 62 kcal 93 kcal 123 kcal",BrandMatcher(["synthetic chain"]))
    assert items and all(i.is_suspect and "table_columns_need_review" in i.warnings for i in items)


def test_instaloader_metadata_failure_stops_instead_of_crashing(tmp_path,monkeypatch):
    import instaloader
    from app.ingest import DataDirs,scrape
    calls=[]
    class Profile:
        def get_posts(self):
            calls.append(1)
            raise instaloader.exceptions.BadResponseException("Synthetic metadata refusal")
            yield
    monkeypatch.setattr(instaloader.Profile,"from_username",lambda *_:Profile())
    result=scrape(DataDirs(tmp_path),profile="synthetic",max_new=1,login=None,sleep_s=0)
    assert result.new_posts==0 and "BadResponseException" in result.stopped_early
    assert len(calls)==1


def test_source_parser_rejects_cross_restaurant_misattribution():
    with pytest.raises(ValueError,match="brand disagrees"):
        parse_entry(_page(),"https://worththecals.com/calories/test",brand="Unrelated Restaurant",record_id="T",collected_at="2026-11-25",permission_reference="synthetic")


@pytest.mark.db
async def test_pending_catalog_browsing_links_branches_without_approving_recommendations(db_session):
    from app.db.maintenance import rematch_branches
    await add_item(db_session, brand_name="Synthetic Pending", brand_slug="synthetic pending",
                   food_item="Pending bowl", calories=420, verified=False)
    await add_branch(db_session, osm_id=901, poi_name="Synthetic Pending - Taft", lat=UPM_LAT, lng=UPM_LNG, slug=None)
    await add_branch(db_session, osm_id=902, poi_name="Unrelated Shop", lat=UPM_LAT, lng=UPM_LNG, slug=None)
    assert len(await load_brand_matcher(db_session)) == 0
    await rematch_branches(db_session, await load_brand_matcher(db_session, include_pending=True))
    options = dict(lat=UPM_LAT, lng=UPM_LNG, radius_km=1, include_unmatched=False)
    assert await nearby_branches(db_session, **options) == []
    for places in [None, [dict(place_id="synthetic-place", poi_name="Synthetic Pending - Test", latitude=UPM_LAT,
                               longitude=UPM_LNG, matched_brand_slug="synthetic pending", attributions=[])]]:
        pins = await nearby_branches(db_session, **options, places=places, include_pending=True)
        assert len(pins) == 1 and pins[0].has_catalog_data
        assert not pins[0].has_calorie_data and pins[0].item_count == 0
        assert pins[0].pending_item_count == pins[0].catalog_item_count == 1
        assert pins[0].min_calories is None
        found = await retrieve(db_session, lat=UPM_LAT, lng=UPM_LNG, places=places,
                               constraints=SearchConstraints(max_distance_km=1), query_vec=HashingEmbedder().embed_one("bowl"))
        assert found.items == []
    assert await brand_items(db_session, "synthetic pending") == []
    browsing = await brand_items(db_session, "synthetic pending", include_pending=True)
    assert len(browsing) == 1 and not browsing[0].is_verified
    assert (await db_session.execute(select(NutritionCatalog.is_verified))).scalar_one() is False


@pytest.mark.db
async def test_chat_catalog_previews_obey_brand_nutrition_location_and_budget_without_approval(db_session):
    from app.services.brand_matcher import BrandRegistry
    from tests.test_hybrid_rag import make_service
    from app.schemas import MealFilters
    from app.security import MacroPhysicsError
    await add_item(db_session, brand_name="KFC", brand_slug="kfc", food_item="Pending chicken",
                   calories=420, protein_g=30, verified=False)
    await add_item(db_session, brand_name="KFC", brand_slug="kfc", food_item="Above calorie cap",
                   calories=700, protein_g=35, verified=False)
    await add_item(db_session, brand_name="Other", brand_slug="other", food_item="Different brand",
                   calories=350, protein_g=25, verified=False)
    await add_branch(db_session, osm_id=911, poi_name="KFC Test", lat=UPM_LAT, lng=UPM_LNG, slug="kfc")
    await add_branch(db_session, osm_id=912, poi_name="Other Test", lat=UPM_LAT, lng=UPM_LNG, slug="other")
    service = make_service()
    service.brands = BrandRegistry(await load_brand_matcher(db_session, include_pending=True))
    response = await service.chat(db_session, message="KFC under 500 kcal at least 20g protein within 1 km", lat=UPM_LAT, lng=UPM_LNG)
    assert response.results == [] and len(response.catalog_matches) == 1
    preview = response.catalog_matches[0]
    assert preview.food_item == "Pending chicken" and not preview.is_verified
    assert preview.calories <= 500 and preview.protein_g >= 20 and preview.distance_km <= 1
    assert "pending review" in response.answer and not response.relaxation.applied
    assert response.inspector.catalog_candidate_count == 1
    selected = await service.chat(db_session, message="KFC under 800 kcal", lat=UPM_LAT, lng=UPM_LNG,
                                  filters=MealFilters(max_calories=500, min_protein_g=20))
    assert selected.inspector.constraints.max_calories == 500
    assert len(selected.catalog_matches) == 1 and selected.catalog_matches[0].calories <= 500
    with pytest.raises(MacroPhysicsError):
        await service.chat(db_session, message="KFC", lat=UPM_LAT, lng=UPM_LNG,
                           filters=MealFilters(max_calories=100, min_protein_g=100))
    for query in ["KFC under PHP250", "KFC under 400 kcal"]:
        empty = await service.chat(db_session, message=query, lat=UPM_LAT, lng=UPM_LNG)
        assert not empty.results and not empty.catalog_matches
    await add_item(db_session, brand_name="KFC", brand_slug="kfc", food_item="Reviewed chicken",
                   calories=430, protein_g=30, verified=True)
    approved = await service.chat(db_session, message="KFC under 500 kcal", lat=UPM_LAT, lng=UPM_LNG)
    assert len(approved.results) == 1 and approved.results[0].is_verified
    assert approved.catalog_matches == []


@pytest.mark.db
async def test_home_catalog_summary_reports_counts_without_private_provenance(db_session):
    from app.main import create_app
    from app.db.session import get_session
    await add_item(db_session, brand_name="Synthetic pending", brand_slug="pending", food_item="Pending bowl", calories=420, verified=False)
    await add_item(db_session, brand_name="Synthetic checked", brand_slug="checked", food_item="Checked bowl", calories=450, verified=True)
    app = create_app()
    async def session_override():
        yield db_session
    app.dependency_overrides[get_session] = session_override
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/catalog-summary")
    assert response.status_code == 200
    assert response.json() == {"records": 2, "brands": 2, "reviewed": 1, "pending": 1}


def test_account_scope_requires_original_instagram_credit_and_snapshot_integrity(tmp_path):
    from app.services.source_scope import allowed_candidate
    snapshot = tmp_path / "source.html"
    raw = b"<p>Credit: @anongcaloriesnito</p>"
    snapshot.write_bytes(raw)
    row = {"record_id": "SYNTHETIC", "source_post_url": "https://www.instagram.com/p/synthetic/",
           "snapshot_path": str(snapshot), "snapshot_sha256": hashlib.sha256(raw).hexdigest()}
    assert allowed_candidate(row, tmp_path)
    assert not allowed_candidate({**row, "source_post_url": "https://example.test/brand-table"}, tmp_path)
    snapshot.write_bytes(b"<p>Credit: @someone_else</p>")
    with pytest.raises(ValueError, match="hash mismatch"):
        allowed_candidate(row, tmp_path)
    row["snapshot_sha256"] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    assert not allowed_candidate(row, tmp_path)


@pytest.mark.db
async def test_archived_source_records_are_excluded_without_deleting_review_state(db_session):
    from sqlalchemy import update
    await add_item(db_session, brand_name="Synthetic Archived", brand_slug="archived", food_item="Archived bowl", calories=400, verified=True)
    await add_branch(db_session, osm_id=9991, poi_name="Synthetic Archived", lat=UPM_LAT, lng=UPM_LNG, slug="archived")
    await db_session.execute(update(NutritionCatalog).values(is_active=False))
    await db_session.commit()
    assert await brand_items(db_session, "archived", include_pending=True) == []
    assert await nearby_branches(db_session, lat=UPM_LAT, lng=UPM_LNG, radius_km=1, include_unmatched=False, include_pending=True) == []
    assert len(await load_brand_matcher(db_session, include_pending=True)) == 0
    assert len(await load_brand_matcher(db_session, include_pending=True, include_archived=True)) == 1
    result = await retrieve(db_session, lat=UPM_LAT, lng=UPM_LNG, constraints=SearchConstraints(), query_vec=HashingEmbedder().embed_one("bowl"))
    assert result.items == []
    assert (await db_session.execute(select(NutritionCatalog.is_verified))).scalar_one() is True


def test_collector_does_not_import_records_outside_selected_account(tmp_path):
    def handler(request):
        return httpx.Response(200, text="User-agent: *\nAllow: /\n" if request.url.path == "/robots.txt" else _page())
    output = tmp_path / "outside.json"
    with pytest.raises(CollectionStopped, match="allowed Instagram account"):
        collect_entries([{"record_id":"SYNTHETIC","brand_name":"Synthetic Chain","url":"https://worththecals.com/calories/test"}],
                        output, permission_reference="synthetic", client=httpx.Client(transport=httpx.MockTransport(handler)), delay_s=0)
    assert not output.exists()
