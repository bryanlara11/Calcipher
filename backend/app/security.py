"""Input sanitization, prompt sandboxing, macro-physics validation and rate limiting."""

from __future__ import annotations

import html
import re
import threading
import time
import unicodedata
from collections import deque
from dataclasses import dataclass, field

from app.schemas import MAX_QUERY_LENGTH, SearchConstraints

KCAL_PER_GRAM_PROTEIN = 4
KCAL_PER_GRAM_CARBS = 4
KCAL_PER_GRAM_FAT = 9

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f​-‏ -‮⁠-⁤﻿]")
_WHITESPACE = re.compile(r"\s+")

_INJECTION_PATTERNS: dict[str, re.Pattern[str]] = {
    "override_instructions": re.compile(
        r"\b(ignore|disregard|forget|override)\b.{0,40}\b(instructions?|rules|prompts?|above|previous|prior)\b",
        re.I,
    ),
    "system_prompt_probe": re.compile(r"\b(system|developer)\s*(prompt|message|instructions?)\b", re.I),
    "role_hijack": re.compile(r"\b(you are now|act as|pretend to be|roleplay as|new persona)\b", re.I),
    "sandbox_escape": re.compile(r"</?\s*(user_query|retrieved_records|system)\s*>", re.I),
    "sql_keywords": re.compile(
        r"(;\s*(drop|delete|update|insert|alter|truncate)\b|\bunion\s+select\b|--\s*$|/\*)", re.I
    ),
}


@dataclass(frozen=True, slots=True)
class SanitizedText:
    text: str
    flags: list[str] = field(default_factory=list)
    truncated: bool = False


def sanitize_user_text(raw: str, *, max_length: int = MAX_QUERY_LENGTH) -> SanitizedText:
    """Normalise and neutralise user text before it reaches the parser or an LLM prompt.

    - NFKC normalisation (folds full-width / look-alike characters)
    - strips control and zero-width/bidi characters, collapses whitespace
    - truncates to `max_length`
    - HTML-escapes `<`, `>` and `&` so the text cannot close the `<user_query>` sandbox tag
    Suspicious patterns are *flagged* (for logs and the inspector), not silently removed.
    """
    normalized = unicodedata.normalize("NFKC", raw or "")
    flags = [name for name, pattern in _INJECTION_PATTERNS.items() if pattern.search(normalized)]
    cleaned = _WHITESPACE.sub(" ", _CONTROL_CHARS.sub(" ", normalized)).strip()
    truncated = len(cleaned) > max_length
    cleaned = cleaned[:max_length]
    return SanitizedText(text=html.escape(cleaned, quote=False), flags=flags, truncated=truncated)


def unescape_for_parsing(text: str) -> str:
    """The regex parser works on plain text; escaping is only for prompt embedding."""
    return html.unescape(text)


def wrap_user_query(sanitized: str) -> str:
    return f"<user_query>\n{sanitized}\n</user_query>"


class MacroPhysicsError(ValueError):
    """The request is mathematically impossible (protein energy alone exceeds the calorie cap)."""

    code = "macro_physics_violation"


def validate_macro_physics(constraints: SearchConstraints) -> None:
    c = constraints
    for label, low, high, unit in (("calorie", c.min_calories, c.max_calories, " kcal"),
                                   ("protein", c.min_protein_g, c.max_protein_g, " g"),
                                   ("carbs", c.min_carbs_g, c.max_carbs_g, " g"),
                                   ("fat", c.min_fat_g, c.max_fat_g, " g")):
        if low is not None and high is not None and low > high:
            raise MacroPhysicsError(f"Impossible request: the {label} minimum ({low:g}{unit}) is above "
                                    f"the maximum ({high:g}{unit}). Swap or adjust the limits.")
    max_cal = constraints.max_calories
    min_protein = constraints.min_protein_g
    if max_cal is not None:
        floor_kcal = ((c.min_protein_g or 0) * KCAL_PER_GRAM_PROTEIN + (c.min_carbs_g or 0) * KCAL_PER_GRAM_CARBS
                      + (c.min_fat_g or 0) * KCAL_PER_GRAM_FAT)
        if floor_kcal > max_cal and (c.min_carbs_g or c.min_fat_g):
            raise MacroPhysicsError(f"Impossible request: the minimum protein, carbs and fat add up to "
                                    f"{floor_kcal:g} kcal, more than the {max_cal} kcal limit.")
    if max_cal is None or min_protein is None:
        return
    protein_kcal = min_protein * KCAL_PER_GRAM_PROTEIN
    if protein_kcal > max_cal:
        raise MacroPhysicsError(
            f"Impossible request: {min_protein:g} g of protein alone is {protein_kcal:g} kcal, "
            f"which exceeds the {max_cal} kcal limit. Raise the calorie limit to at least "
            f"{protein_kcal:g} kcal or lower the protein target to {max_cal / KCAL_PER_GRAM_PROTEIN:g} g."
        )


class SlidingWindowRateLimiter:
    """Per-key sliding-window limiter (single process). Use Redis for multi-worker deployments."""

    def __init__(self, limit: int, window_s: float = 60.0, max_keys: int = 10_000) -> None:
        self.limit = limit
        self.window_s = window_s
        self.max_keys = max_keys
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, now: float | None = None) -> tuple[bool, float]:
        """Return (allowed, retry_after_seconds)."""
        now = time.monotonic() if now is None else now
        cutoff = now - self.window_s
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                if len(self._hits) >= self.max_keys:
                    self._evict(cutoff)
                hits = self._hits[key] = deque()
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                return False, max(0.0, hits[0] + self.window_s - now)
            hits.append(now)
            return True, 0.0

    def _evict(self, cutoff: float) -> None:
        stale = [k for k, d in self._hits.items() if not d or d[-1] <= cutoff]
        for k in stale:
            del self._hits[k]
        if len(self._hits) >= self.max_keys:  # still full: drop the oldest-touched half
            for k in sorted(self._hits, key=lambda k: self._hits[k][-1])[: self.max_keys // 2]:
                del self._hits[k]
