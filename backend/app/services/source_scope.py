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

def allowed_candidate(row: dict, snapshot_root: Path) -> bool:
    source = urlparse(row.get("source_post_url", ""))
    if source.hostname not in {"instagram.com", "www.instagram.com"} or not re.match(r"^/(p|reel)/[^/]+", source.path):
        return False
    value = row.get("snapshot_path")
    if not value:
        return False
    path = Path(value)
    if not path.is_file():
        path = snapshot_root / value
    if not path.is_file():
        return False
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != row.get("snapshot_sha256"):
        raise ValueError(f"Snapshot hash mismatch for {row.get('record_id')}")
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
                            "source_account_evidence_url": evidence.get("secondary_source_url") if evidence else None}
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
