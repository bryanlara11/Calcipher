"""Source-backed candidate preparation. Approval comes from an explicit human receipt."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

CLASSIFICATIONS = {"official", "community_reported", "estimated"}


def _http(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and not parsed.username


def validate_provenance(metadata: dict, verified: bool, calories=None) -> list[str]:
    errors = []
    if not metadata:
        return errors  # existing manually reviewed CSVs remain readable
    bounds = metadata.get("calorie_bounds")
    if bounds is not None:
        if (not isinstance(bounds, list) or len(bounds) != 2
                or not all(type(n) in (int, float) and 0 <= n <= 10000 for n in bounds)
                or bounds[0] > bounds[1] or math.ceil(bounds[1]) != calories):
            errors.append("calorie_bounds must be ordered source values matching the whole-kcal search ceiling")
    if metadata.get("source_classification") not in CLASSIFICATIONS:
        errors.append("source_classification must be official, community_reported or estimated")
    try:
        date.fromisoformat(metadata.get("collected_at", ""))
    except (ValueError, TypeError):
        errors.append("collected_at must be an ISO collection date")
    if not metadata.get("permission_reference"):
        errors.append("permission_reference is required for collected records")
    if not isinstance(metadata.get("field_sources"), dict) or not all(_http(v) for v in metadata["field_sources"].values()):
        errors.append("field_sources must map fields to HTTP(S) source URLs")
    if metadata.get("secondary_source_url") and not _http(metadata["secondary_source_url"]):
        errors.append("secondary_source_url must be HTTP(S)")
    if metadata.get("nutrition_source_url") and not _http(metadata["nutrition_source_url"]):
        errors.append("nutrition_source_url must be HTTP(S)")
    if verified and metadata.get("record_id"):
        approval = metadata.get("approval", {})
        if not isinstance(approval, dict) or not approval.get("reviewed_by") or not approval.get("reference"):
            errors.append("collected records require a named human approval reference")
        else:
            try:
                stamp = datetime.fromisoformat(approval.get("reviewed_at", "").replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    raise ValueError("timezone required")
            except (ValueError, TypeError):
                errors.append("approval reviewed_at must include an ISO timestamp with timezone")
            if not re.fullmatch(r"[a-f0-9]{64}", approval.get("dataset_sha256", "")):
                errors.append("approval must identify the reviewed dataset SHA-256")
    return errors


def prepare_review(candidates_path: Path, output: Path, receipt_path: Path | None = None) -> dict:
    from app.ingest import REVIEW_COLUMNS
    from app.services.brand_matcher import normalize_brand

    raw = candidates_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    candidates = json.loads(raw)
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("candidates must be a nonempty array")
    ids = [r.get("record_id") for r in candidates]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("candidate record IDs must be unique and nonempty")
    receipt = {}
    approved = set()
    if receipt_path:
        receipt = json.loads(receipt_path.read_text())
        if receipt.get("dataset_sha256") != digest:
            raise ValueError("approval receipt does not match the reviewed dataset")
        selected = receipt.get("approved_record_ids")
        if not isinstance(selected, list) or len(set(selected)) != len(selected) or not set(selected) <= set(ids):
            raise ValueError("approval receipt contains duplicate or unknown record IDs")
        approved = set(selected)
    rows = []
    for candidate in candidates:
        record_id = candidate["record_id"]
        metadata = {key: candidate.get(key) for key in (
            "record_id", "source_classification", "collected_at", "permission_reference",
            "secondary_source_url", "field_sources", "snapshot_sha256", "notes", "source_account", "brand_origin", "nutrition_source_url")}
        metadata["collection_method"] = "authorized_source_transcription"
        if record_id in approved:
            metadata["approval"] = {
                "reviewed_by": receipt.get("reviewed_by"), "reviewed_at": receipt.get("reviewed_at"),
                "reference": receipt.get("reference"), "dataset_sha256": digest,
            }
        if "calorie_bounds" in candidate:
            metadata["calorie_bounds"] = candidate["calorie_bounds"]
        errors = validate_provenance(metadata, record_id in approved, candidate.get("calories"))
        if errors:
            raise ValueError(f"{record_id}: " + "; ".join(errors))
        if not _http(candidate.get("source_post_url")):
            raise ValueError(f"{record_id}: original source URL is required")
        # This pilot never converts a historical/photo price into a current branch price.
        if candidate.get("price_php") is not None:
            raise ValueError(f"{record_id}: price evidence requires a separate checked-price review")
        row = {key: candidate.get(key, "") for key in (
            "source_post_url", "brand_name", "food_item", "calories", "protein_g", "carbs_g", "fat_g", "serving_size")}
        row.update(action="load", verified="yes" if record_id in approved else "no",
                   shortcode=record_id, brand_slug=normalize_brand(candidate["brand_name"]),
                   is_suspect="no", warnings=";".join([candidate.get("notes", "")]),
                   price_php="", price_source_url="", source_images=";".join(candidate.get("source_images", [])),
                   provenance_json=json.dumps(metadata, ensure_ascii=False, sort_keys=True))
        rows.append(row)
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, REVIEW_COLUMNS, extrasaction="ignore")
    writer.writeheader(); writer.writerows(rows)
    content = stream.getvalue().encode("utf-8-sig")
    if output.exists() and output.read_bytes() != content:
        raise FileExistsError(f"{output} already contains different review data; choose a new output file")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_bytes(content); temporary.replace(output)
    return {"records": len(rows), "approved": len(approved), "pending": len(rows) - len(approved),
            "dataset_sha256": digest, "output": str(output)}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--candidates", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--approval-receipt", type=Path)
    args = p.parse_args()
    print(json.dumps(prepare_review(args.candidates, args.output, args.approval_receipt), indent=2))


if __name__ == "__main__":
    main()
