"""Brand-to-Branch entity resolution.

Turns noisy OpenStreetMap POI names / brand tags ("Andok's Litson Manok - Makati Ave",
"Jollibee Taft", "24 Chicken Leon Guinto") into canonical `brand_slug` values used by
`nutrition_catalog` ("andoks", "jollibee", "24 chicken").

Resolution order (first hit wins):
  1. OSM `brand` tag, then `name` — longest alias/slug that is a token *prefix* of the name.
  2. Contiguous token *containment* for multi-token or long aliases ("SM Megamall Jollibee").
  3. Trigram similarity (same algorithm as PostgreSQL pg_trgm) for typos ("Jolibee Taft").
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_APOSTROPHES = re.compile(r"[’'`´]")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
# Branch suffixes are usually separated by a dash, pipe, slash, '@' or a parenthesis.
_BRANCH_SEPARATOR = re.compile(r"\s+[-–—|/@]\s*|\s*[|(\[]|\s*[–—]\s*")

# alias (normalized) -> canonical slug. Only aliases whose target exists in the catalog are used.
BRAND_ALIASES: dict[str, str] = {
    "andoks litson manok": "andoks",
    "andoks litson": "andoks",
    "kenny rogers": "kenny rogers roasters",
    "kenny roger s": "kenny rogers roasters",
    "kenny rogers roaster": "kenny rogers roasters",
    "seven eleven": "7 eleven",
    "711": "7 eleven",
    "7eleven": "7 eleven",
    "mang inasal chicken": "mang inasal",
    "manginasal": "mang inasal",
    "24chicken": "24 chicken",
    "chow king": "chowking",
    "jolibee": "jollibee",
    "subway sandwiches": "subway",
    "uncle john": "uncle johns",
    "ministop uncle johns": "uncle johns",
}

MatchMethod = Literal["prefix", "contains", "trigram"]
MatchSource = Literal["brand_tag", "name"]


@dataclass(frozen=True, slots=True)
class BrandMatch:
    slug: str
    method: MatchMethod
    source: MatchSource
    score: float


def normalize_brand(text: str | None) -> str:
    """Lowercase, strip accents/apostrophes/punctuation, collapse whitespace.

    >>> normalize_brand("Andok's Litson-Manok")
    'andoks litson manok'
    """
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = ascii_only.lower().replace("&", " and ")
    lowered = _APOSTROPHES.sub("", lowered)
    return _NON_ALNUM.sub(" ", lowered).strip()


def strip_branch_suffix(text: str | None) -> str:
    """Drop the branch/location part: "Jollibee - Taft Ave" -> "Jollibee"."""
    if not text:
        return ""
    head = _BRANCH_SEPARATOR.split(text, maxsplit=1)[0].strip()
    return head or text.strip()


def trigrams(text: str) -> set[str]:
    """pg_trgm-compatible trigram set: each word padded with two leading and one trailing space."""
    grams: set[str] = set()
    for word in normalize_brand(text).split():
        padded = f"  {word} "
        grams.update(padded[i : i + 3] for i in range(len(padded) - 2))
    return grams


def trigram_similarity(a: str, b: str) -> float:
    """Jaccard similarity of trigram sets — identical to pg_trgm's `similarity()`."""
    ta, tb = trigrams(a), trigrams(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


class BrandMatcher:
    def __init__(
        self,
        brand_slugs: Iterable[str],
        aliases: Mapping[str, str] | None = None,
        *,
        trigram_threshold: float = 0.55,
    ) -> None:
        self.slugs: frozenset[str] = frozenset(s for s in (normalize_brand(x) for x in brand_slugs) if s)
        self.trigram_threshold = trigram_threshold
        alias_map = BRAND_ALIASES if aliases is None else aliases
        candidates: dict[tuple[str, ...], str] = {tuple(s.split()): s for s in self.slugs}
        for alias, target in alias_map.items():
            target_norm = normalize_brand(target)
            alias_norm = normalize_brand(alias)
            if target_norm in self.slugs and alias_norm:
                candidates.setdefault(tuple(alias_norm.split()), target_norm)
        # Longest candidates first so "kenny rogers roasters" beats "kenny rogers".
        self._candidates: list[tuple[tuple[str, ...], str]] = sorted(
            candidates.items(), key=lambda kv: (-len(kv[0]), -len(" ".join(kv[0])))
        )

    def __len__(self) -> int:
        return len(self.slugs)

    def match(self, name: str | None, brand_tag: str | None = None) -> BrandMatch | None:
        sources: list[tuple[MatchSource, str]] = [
            (src, raw) for src, raw in (("brand_tag", brand_tag), ("name", name)) if raw and raw.strip()
        ]
        if not sources or not self._candidates:
            return None

        for src, raw in sources:
            for text in dict.fromkeys((normalize_brand(strip_branch_suffix(raw)), normalize_brand(raw))):
                hit = self._prefix(text)
                if hit:
                    return BrandMatch(hit, "prefix", src, 1.0)

        for src, raw in sources:
            hit = self._contains(normalize_brand(raw))
            if hit:
                return BrandMatch(hit, "contains", src, 0.9)

        best: BrandMatch | None = None
        for src, raw in sources:
            tokens = normalize_brand(strip_branch_suffix(raw)).split()
            if not tokens:
                continue
            for cand_tokens, slug in self._candidates:
                window = " ".join(tokens[: len(cand_tokens)])
                score = trigram_similarity(window, " ".join(cand_tokens))
                if score >= self.trigram_threshold and (best is None or score > best.score):
                    best = BrandMatch(slug, "trigram", src, round(score, 4))
        return best

    def find_in_text(self, text: str) -> str | None:
        """Find a brand mentioned anywhere in free text (used by the query parser)."""
        tokens = normalize_brand(text).split()
        for cand_tokens, slug in self._candidates:
            n = len(cand_tokens)
            for i in range(len(tokens) - n + 1):
                if tuple(tokens[i : i + n]) == cand_tokens:
                    return slug
        return None

    def resolve_slug(self, value: str | None) -> str | None:
        """Map an LLM-provided brand string to a known slug, or None if it isn't known."""
        if not value:
            return None
        hit = self.match(value)
        return hit.slug if hit else None

    def _prefix(self, text: str) -> str | None:
        tokens = tuple(text.split())
        for cand_tokens, slug in self._candidates:
            if tokens[: len(cand_tokens)] == cand_tokens:
                return slug
        return None

    def _contains(self, text: str) -> str | None:
        tokens = text.split()
        for cand_tokens, slug in self._candidates:
            # Single short tokens ("bo", "cafe") would cause false positives mid-name.
            if len(cand_tokens) == 1 and len(cand_tokens[0]) < 6:
                continue
            n = len(cand_tokens)
            for i in range(1, len(tokens) - n + 1):
                if tuple(tokens[i : i + n]) == cand_tokens:
                    return slug
        return None


async def load_brand_matcher(session: AsyncSession, *, include_pending: bool = False) -> BrandMatcher:
    from app.models import NutritionCatalog

    query = select(NutritionCatalog.brand_slug).distinct()
    if not include_pending:
        query = query.where(NutritionCatalog.is_verified.is_(True))
    rows = await session.execute(query)
    return BrandMatcher(rows.scalars().all())


class BrandRegistry:
    """In-memory cache of the current BrandMatcher so request parsing needs no DB round trip."""

    def __init__(self, matcher: BrandMatcher | None = None, *, max_age_s: float = 300.0, include_pending: bool = False) -> None:
        self._matcher = matcher or BrandMatcher([])
        self.max_age_s = max_age_s
        self.include_pending = include_pending
        self._loaded_at = time.monotonic() if matcher is not None else float("-inf")

    @property
    def matcher(self) -> BrandMatcher:
        return self._matcher

    async def refresh(self, session: AsyncSession) -> BrandMatcher:
        self._matcher = await load_brand_matcher(session, include_pending=self.include_pending)
        self._loaded_at = time.monotonic()
        return self._matcher

    async def refresh_if_stale(self, session: AsyncSession) -> BrandMatcher:
        """Pick up brands added by `python -m app.ingest load` without restarting the API."""
        if time.monotonic() - self._loaded_at > self.max_age_s:
            await self.refresh(session)
        return self._matcher
