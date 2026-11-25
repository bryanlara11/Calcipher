"""Audit the active catalog against the operator's selected Instagram account.

Snapshots and excluded rows are retained. This never changes human approval flags.
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import html
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select, text
from app.models import NutritionCatalog

ACCOUNT = "anongcaloriesnito"

def _snapshot(value: str | None, digest: str | None, root: Path) -> bytes | None:
    if not value:
        return None
    path = Path(value)
    if not path.is_file():
        path = root / value
    if not path.is_file():
        return None
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("Snapshot hash mismatch")
    return raw


def _post_id(url: str) -> str | None:
    source = urlparse(url)
    match = re.fullmatch(r"/(?:anongcaloriesnito/)?(?:p|reel)/([^/]+)/?", source.path)
    return match[1] if source.scheme == "https" and source.hostname in {"instagram.com", "www.instagram.com"} and match else None


def allowed_candidate(row: dict, snapshot_root: Path) -> bool:
    post = _post_id(row.get("source_post_url", ""))
    if not post:
        return False
    raw = _snapshot(row.get("snapshot_path"), row.get("snapshot_sha256"), snapshot_root)
    if raw is None:
        return False
    origin = row.get("brand_origin")
    if origin:
        # A brand must first be observed on the selected account. Nutrition may then
        # come from a separately retained official menu or labelled secondary source.
        from app.services.brand_matcher import normalize_brand
        if (origin.get("account") != ACCOUNT or _post_id(origin.get("post_url", "")) != post
                or normalize_brand(origin.get("brand_name", "")) != normalize_brand(row.get("brand_name", ""))):
            return False
        proof = _snapshot(origin.get("snapshot_path"), origin.get("snapshot_sha256"), snapshot_root)
        if proof is None or not row.get("nutrition_source_url") or not row.get("field_sources", {}).get("calories"):
            return False
        visible = proof.decode("utf-8")
        return (origin["post_url"] in visible and ACCOUNT in visible
                and origin["brand_name"].casefold() in visible.casefold())
    visible = html.unescape(re.sub(r"<[^>]+>", " ", raw.decode("utf-8")))
    return bool(re.search(r"Credit:\s*@anongcaloriesnito\b", visible, re.I))

async def audit(files: list[Path], snapshot_root: Path, *, apply: bool = False) -> dict:
    from app.db.session import get_engine, get_sessionmaker, init_db
    from app.db.maintenance import rematch_branches
    from app.services.brand_matcher import load_brand_matcher
    candidates = {}
    for path in files:
        for row in json.loads(path.read_text()):
            key = row["record_id"]
            if key in candidates:
                raise ValueError(f"Duplicate source record ID: {key}")
            candidates[key] = row
    eligibility = {key: allowed_candidate(row, snapshot_root) for key, row in candidates.items()}
    await init_db(get_engine())
    active = []; archived = []
    async with get_sessionmaker()() as session:
        records = (await session.execute(select(NutritionCatalog))).scalars().all()
        for record in records:
            key = record.provenance.get("record_id")
            evidence = candidates.get(key)
            allowed = bool(evidence and eligibility[key] and record.source_post_url == evidence.get("source_post_url"))
            (active if allowed else archived).append(key or str(record.id))
            if apply:
                metadata = {"source_account": ACCOUNT if allowed else None,
                            "source_scope": "allowed_account" if allowed else "archived_outside_selected_account",
                            "source_account_evidence_url": (evidence.get("brand_origin", {}).get("post_url") or evidence.get("secondary_source_url")) if evidence else None}
                await session.execute(text("UPDATE nutrition_catalog SET is_active=:active, provenance=provenance || CAST(:metadata AS jsonb) WHERE id=:id"),
                                      {"id": record.id, "active": allowed, "metadata": json.dumps(metadata)})
        if apply:
            await rematch_branches(session, await load_brand_matcher(session, include_pending=True), commit=False)
            await session.commit()
    return {"account": ACCOUNT, "active_count": len(active), "archived_count": len(archived),
            "active_ids": active, "archived_ids": archived, "applied": apply}

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--candidates", type=Path, nargs="+", required=True)
    p.add_argument("--snapshot-root", type=Path, required=True)
    p.add_argument("--apply", action="store_true")
    a = p.parse_args()
    print(json.dumps(asyncio.run(audit(a.candidates, a.snapshot_root, apply=a.apply)), indent=2))

if __name__ == "__main__": main()
