"""Offline-first deterministic text embedder.

Maps text into a fixed 384-d space via hashed token bucketing with sub-token
n-grams, then L2-normalizes. No model download, no network, fully reproducible
— ideal for a host-agnostic demo. Because it's normalized, dot-product ==
cosine similarity. The interface (``embed`` -> list[float] of DIM) is what the
RAG layer depends on, so this can later be swapped for a real sentence encoder
without touching callers.
"""
from __future__ import annotations

import hashlib
import math
import re

DIM = 384
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _bucket(token: str, salt: str = "") -> int:
    h = hashlib.md5((salt + token).encode("utf-8")).hexdigest()
    return int(h, 16) % DIM


def embed(text: str) -> list[float]:
    vec = [0.0] * DIM
    toks = _tokens(text)
    for tok in toks:
        vec[_bucket(tok)] += 1.0
        # character trigrams add fuzziness (typo tolerance in retrieval)
        for i in range(len(tok) - 2):
            vec[_bucket(tok[i : i + 3], salt="3g:")] += 0.5
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return vec
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))
