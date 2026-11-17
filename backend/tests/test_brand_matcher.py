import pytest

from app.services.brand_matcher import (
    BrandMatcher,
    normalize_brand,
    strip_branch_suffix,
    trigram_similarity,
)


@pytest.mark.parametrize(
    ("osm_name", "expected"),
    [
        ("Andok's Litson Manok - Makati Ave", "andoks"),
        ("Andok's - Pedro Gil", "andoks"),
        ("ANDOK’S", "andoks"),  # curly apostrophe, upper case
        ("Jollibee Taft", "jollibee"),
        ("Jollibee - Taft Avenue (UN)", "jollibee"),
        ("24 Chicken Leon Guinto", "24 chicken"),
        ("Mang Inasal–Robinsons Place Manila", "mang inasal"),
        ("Kenny Rogers Roasters (Greenbelt)", "kenny rogers roasters"),
        ("Kenny Rogers", "kenny rogers roasters"),
        ("7-Eleven Padre Faura", "7 eleven"),
        ("Seven Eleven", "7 eleven"),
        ("Uncle John's @ Ministop Taft", "uncle johns"),
        ("Chowking UN Avenue", "chowking"),
        ("Subway", "subway"),
        ("SM Megamall Jollibee", "jollibee"),  # brand not first: containment
    ],
)
def test_noisy_osm_names_resolve_to_canonical_slug(matcher, osm_name, expected):
    match = matcher.match(osm_name)
    assert match is not None, osm_name
    assert match.slug == expected


@pytest.mark.parametrize(
    "osm_name",
    [
        "McDonald's UN Avenue",
        "Starbucks Padre Faura",
        "Jolly Hotdog Stand",  # shares a prefix with Jollibee, must not match
        "Mang Kanor's Grill",  # shares "mang" with Mang Inasal
        "24 Hours Carinderia",  # shares "24" with 24 Chicken
        "",
        "   ",
    ],
)
def test_unrelated_restaurants_stay_unmatched(matcher, osm_name):
    assert matcher.match(osm_name) is None


def test_none_inputs_are_safe(matcher):
    assert matcher.match(None) is None
    assert matcher.match(None, None) is None


@pytest.mark.parametrize(
    ("typo", "expected"),
    [("Jollibe Taft", "jollibee"), ("Mang Inasall - Taft", "mang inasal")],
)
def test_typos_resolve_through_trigram_similarity(matcher, typo, expected):
    match = matcher.match(typo)
    assert match is not None
    assert match.slug == expected
    assert match.method == "trigram"
    assert match.score >= matcher.trigram_threshold


def test_brand_tag_takes_priority_over_name(matcher):
    match = matcher.match("Food Court Stall 12", brand_tag="Jollibee")
    assert match is not None and match.slug == "jollibee" and match.source == "brand_tag"


def test_name_used_when_brand_tag_is_unknown(matcher):
    match = matcher.match("Jollibee Taft", brand_tag="Some Unknown Brand")
    assert match is not None and match.slug == "jollibee" and match.source == "name"


def test_longest_candidate_wins():
    m = BrandMatcher(["kenny rogers roasters", "kenny"], aliases={})
    assert m.match("Kenny Rogers Roasters Greenbelt").slug == "kenny rogers roasters"


def test_aliases_only_apply_to_known_slugs():
    m = BrandMatcher(["jollibee"])  # "kenny rogers" alias target not in catalog
    assert m.match("Kenny Rogers Greenbelt") is None


def test_empty_catalog_matches_nothing():
    assert BrandMatcher([]).match("Jollibee Taft") is None


def test_normalize_and_strip():
    assert normalize_brand("Andok's Litson-Manok") == "andoks litson manok"
    assert normalize_brand("Café Ñora & Co.") == "cafe nora and co"
    assert normalize_brand(None) == ""
    assert strip_branch_suffix("Jollibee - Taft Ave") == "Jollibee"
    assert strip_branch_suffix("Kenny Rogers Roasters (Greenbelt)") == "Kenny Rogers Roasters"
    assert strip_branch_suffix("7-Eleven") == "7-Eleven"  # an in-word hyphen is not a separator


def test_trigram_similarity_matches_pg_trgm_reference():
    # PostgreSQL docs: SELECT similarity('word', 'two words') -> 0.36363637
    assert trigram_similarity("word", "two words") == pytest.approx(4 / 11)
    assert trigram_similarity("jollibee", "jollibee") == 1.0
    assert trigram_similarity("", "jollibee") == 0.0


def test_find_in_text(matcher):
    assert matcher.find_in_text("anything at mang inasal under 500 cal") == "mang inasal"
    assert matcher.find_in_text("Kenny Rogers Roasters chicken please") == "kenny rogers roasters"
    assert matcher.find_in_text("cheap sisig near me") is None
