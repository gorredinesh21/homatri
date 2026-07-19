"""Typo/phonetic-tolerant mapping of free-text item names to menu records.

Uses RapidFuzz token-set ratio so noisy inputs like "paaaneer batter musala"
resolve to "Paneer Butter Masala" and "jeer rice" to "Jeera Rice". Returns the
matched menu row (dict-like) and a confidence score.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence

from rapidfuzz import fuzz

_THRESHOLD = 55  # below this we treat the item as unrecognized
# Strip articles, number-words and common filler so noisy phrases like
# "one dal fry along" reduce to "dal fry" before fuzzy matching.
_STRIP_RE = re.compile(
    r"\b(a|an|the|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"along|also|some|please|of|and|with|plz)\b|[0-9]+"
)


@dataclass
class MenuMatch:
    matched: bool
    menu_item: Any | None
    score: float
    query: str


def _name_of(item: Any) -> str:
    return item["name"] if isinstance(item, dict) else getattr(item, "name")


def _score(query: str, choice: str) -> float:
    # Length-aware blend: ratio + token_sort catch typos ("dar fry"->"Dal Fry")
    # and word reordering, while token_set handles extra words. We deliberately
    # avoid partial_ratio/WRatio here — they inflate scores for short garbage
    # tokens (e.g. "coke") that alias onto substrings of real menu names.
    return max(
        fuzz.ratio(query, choice),
        fuzz.token_sort_ratio(query, choice),
        fuzz.token_set_ratio(query, choice),
    )


def match_item(query: str, menu_items: Sequence[Any]) -> MenuMatch:
    query = _STRIP_RE.sub("", (query or "")).strip()
    if not query or not menu_items:
        return MenuMatch(False, None, 0.0, query)
    best_idx, best_score = -1, -1.0
    for i, m in enumerate(menu_items):
        s = _score(query, _name_of(m))
        if s > best_score:
            best_idx, best_score = i, s
    if best_score < _THRESHOLD:
        return MenuMatch(False, None, float(best_score), query)
    return MenuMatch(True, menu_items[best_idx], float(best_score), query)
