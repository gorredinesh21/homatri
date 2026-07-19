"""Text embedding service using AWS Bedrock Titan Embeddings (with deterministic fallback).

Uses ``BedrockEmbeddings`` from ``langchain_aws`` (model: ``amazon.titan-embed-text-v2:0``)
normalized to 384 dimensions to align with pgvector schemas. Falls back gracefully to an
offline token-bucket embedder if Bedrock is disabled or unreachable.
"""
from __future__ import annotations

import hashlib
import math
import re

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("embeddings")

DIM = 384
_TOKEN_RE = re.compile(r"[a-z0-9]+")

_bedrock_embedder = None
_bedrock_init_attempted = False


def _get_bedrock_embedder():
    global _bedrock_embedder, _bedrock_init_attempted
    if not _bedrock_init_attempted:
        _bedrock_init_attempted = True
        try:
            from langchain_aws import BedrockEmbeddings

            _bedrock_embedder = BedrockEmbeddings(
                model_id=settings.bedrock_embedding_model_id,
                region_name=settings.aws_region,
            )
            log.info("BedrockEmbeddings initialized (%s)", settings.bedrock_embedding_model_id)
        except Exception as e:  # noqa: BLE001
            log.warning("BedrockEmbeddings init failed, falling back offline: %s", e)
            _bedrock_embedder = None
    return _bedrock_embedder


def _offline_embed(text: str) -> list[float]:
    vec = [0.0] * DIM
    toks = _TOKEN_RE.findall(text.lower())
    for tok in toks:
        h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16) % DIM
        vec[h] += 1.0
        for i in range(len(tok) - 2):
            h3g = int(hashlib.md5(("3g:" + tok[i : i + 3]).encode("utf-8")).hexdigest(), 16) % DIM
            vec[h3g] += 0.5
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return vec
    return [v / norm for v in vec]


def embed(text: str) -> list[float]:
    """Return a 384-dimensional embedding vector for text using Bedrock (or offline)."""
    if settings.llm_enabled:
        embedder = _get_bedrock_embedder()
        if embedder is not None:
            try:
                v = embedder.embed_query(text)
                if len(v) >= DIM:
                    v = v[:DIM]
                    norm = math.sqrt(sum(x * x for x in v))
                    if norm > 0:
                        return [x / norm for x in v]
                    return v
            except Exception as e:  # noqa: BLE001
                log.warning("Bedrock embed query failed, using offline embedder: %s", e)
    return _offline_embed(text)


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))
