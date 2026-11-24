"""Bounded collection of explicitly selected, permission-authorized worththecals entries.

No Instagram private endpoints, login cookies, browser impersonation or automatic approval.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import time
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser
from zoneinfo import ZoneInfo

import httpx

USER_AGENT = "CalCipher-Authorized-Research/1.0"
BASE = "https://worththecals.com"


class CollectionStopped(RuntimeError):
    pass


class _Facts(HTMLParser):
    def __init__(self):
        super().__init__(); self.kind = None; self.parts = []; self.label = None; self.values = {}
    def handle_starttag(self, tag, attrs):
        if tag in {"dt", "dd"}: self.kind = tag; self.parts = []
    def handle_data(self, data):
        if self.kind: self.parts.append(data)
    def handle_endtag(self, tag):
        if tag != self.kind: return
        value = "".join(self.parts).strip()
        if tag == "dt": self.label = value
        elif self.label: self.values[self.label] = value
        self.kind = None


def _number(value, unit):
    if value is None: return None
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*" + re.escape(unit), str(value))
    if not match: raise ValueError(f"Unsupported source amount/unit: {value!r}")
    return float(match[1])


def _safe_url(url: str) -> bool:
    u = urlparse(url)
    return (u.scheme == "https" and u.hostname == "worththecals.com" and not u.username
            and u.path.startswith("/calories/") and not u.query and not u.fragment)


def parse_entry(document: str, url: str, *, brand: str, record_id: str, collected_at: str,
                permission_reference: str) -> dict:
    """Cross-check published HTML fields against its public MenuItem JSON-LD."""
    if not _safe_url(url): raise ValueError("Only explicit worththecals calorie pages are supported")
    objects = []
    for raw in re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', document, re.S):
        obj = json.loads(raw); objects.extend(obj.get("@graph", [obj]))
    item = next((o for o in objects if o.get("@type") == "MenuItem"), None)
    page = next((o for o in objects if o.get("@type") == "WebPage"), {})
    crumbs = next((o for o in objects if o.get("@type") == "BreadcrumbList"), {})
    if not item or "Philippines" not in page.get("description", ""):
        raise ValueError("Missing Philippine MenuItem evidence; do not bypass a blocked page")
    name = crumbs.get("itemListElement", [{}])[-1].get("name")
    if not name: raise ValueError("Missing explicit item name")
    from app.services.brand_matcher import normalize_brand
    trail = crumbs.get("itemListElement", [])
    if len(trail) < 3:
        raise ValueError("A restaurant breadcrumb is required")
    source_brand = normalize_brand(trail[-2].get("name", "").removesuffix(" calories"))
    expected = normalize_brand(brand)
    if source_brand != expected and not source_brand.startswith(expected + " "):
        raise ValueError("Selected brand disagrees with the published restaurant breadcrumb")
    nutrition = item.get("nutrition", {}); calories = _number(nutrition.get("calories"), "calories")
    if calories is None or not calories.is_integer() or not 0 <= calories <= 10000:
        raise ValueError("Published whole-number calories are required")
    shown = re.search(r'<strong[^>]*>Calories</strong>\s*<strong[^>]*>(.*?)</strong>', document, re.S)
    if not shown or float(re.sub(r'<[^>]*>|<!--.*?-->', '', shown[1]).strip()) != calories:
        raise ValueError("Visible calories and structured data disagree")
    facts = _Facts(); facts.feed(document)
    macros = {}
    for key, ld, label in [("protein_g", "proteinContent", "Protein"),
                           ("carbs_g", "carbohydrateContent", "Total carbohydrates"),
                           ("fat_g", "fatContent", "Total fat")]:
        value = _number(nutrition.get(ld), "g")
        displayed = facts.values.get(label)
        if value is not None and (displayed is None or _number(displayed, "g") != value):
            raise ValueError(f"Visible {label} and structured data disagree")
        if value is None and displayed not in (None, "Not published"):
            raise ValueError(f"Visible {label} is absent from structured data; manual review required")
        macros[key] = value
    description = item.get("description", "")
    classification = "community_reported" if "community-logged" in description else "official" if "brand-published" in description else "unknown"
    serving = nutrition.get("servingSize")
    if serving in (None, "", "1 listed serving", "serving size unavailable"): serving = None
    original = page.get("citation")
    if not isinstance(original, str) or urlparse(original).scheme not in {"https", "http"}:
        raise ValueError("An original source link is required")
    return dict(record_id=record_id, brand_name=brand, food_item=name, calories=int(calories), **macros,
                serving_size=serving, price_php=None, price_source_url=None, source_post_url=original,
                secondary_source_url=url, source_classification=classification, collected_at=collected_at,
                permission_reference=permission_reference, verification_status="pending", reviewed_by=None,
                reviewed_at=None, raw_nutrition=nutrition, source_images=[], source_listing_updated_at=page.get("dateModified"),
                notes="Collected from the visible Philippine entry. Original serving and values require human review; no current PHP price is established.",
                field_sources={k: url for k in ["food_item", "calories", "protein_g", "carbs_g", "fat_g", "serving_size"]})


def collect_entries(entries: list[dict], output: Path, *, permission_reference: str,
                    client: httpx.Client | None = None, delay_s: float = 1.0) -> dict:
    if not permission_reference.strip(): raise ValueError("Documented permission reference is required")
    if not 1 <= len(entries) <= 50: raise ValueError("Select 1..50 explicit item pages per reviewed batch")
    urls = [e["url"] for e in entries]
    if len(set(urls)) != len(urls) or not all(_safe_url(u) for u in urls):
        raise ValueError("Use unique, explicit, supported item URLs")
    if output.exists(): raise FileExistsError("Choose a new output path; reviewed snapshots are never replaced")
    owned = client is None
    client = client or httpx.Client(timeout=20, follow_redirects=False, headers={"User-Agent": USER_AGENT})
    snapshots = output.parent / (output.stem + "-snapshots"); snapshots.mkdir(parents=True, exist_ok=True)
    rows = []
    try:
        robots = client.get(BASE + "/robots.txt")
        policy = RobotFileParser()
        if robots.status_code == 200: policy.parse(robots.text.splitlines())
        elif robots.status_code == 404: policy.parse([])
        else: raise CollectionStopped(f"robots.txt returned {robots.status_code}; collection stopped")
        for e in entries:
            url = e["url"]
            if not policy.can_fetch(USER_AGENT, url): raise CollectionStopped("robots.txt disallows this item; use an authorized export")
            if delay_s: time.sleep(delay_s)
            response = client.get(url)
            if response.status_code != 200:
                raise CollectionStopped(f"HTTP {response.status_code}; stop rather than bypass or retry platform restrictions")
            row = parse_entry(response.text, url, brand=e["brand_name"], record_id=e["record_id"],
                              collected_at=datetime.now(ZoneInfo("Asia/Manila")).date().isoformat(),
                              permission_reference=permission_reference)
            digest = hashlib.sha256(response.content).hexdigest()
            snapshot = snapshots / f"{hashlib.sha256(url.encode()).hexdigest()[:16]}.html"
            snapshot.write_bytes(response.content)
            row.update(snapshot_path=str(snapshot), snapshot_sha256=digest)
            rows.append(row)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text(json.dumps(rows, ensure_ascii=False, indent=2)); temporary.replace(output)
    finally:
        if owned: client.close()
    return {"collected": len(rows), "approved": 0, "output": str(output)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--urls-file", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--permission-reference", required=True)
    a = p.parse_args()
    print(json.dumps(collect_entries(json.loads(a.urls_file.read_text()), a.output,
                                     permission_reference=a.permission_reference), indent=2))


if __name__ == "__main__": main()
