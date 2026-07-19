"""Portable embedding column.

Uses native ``pgvector`` on Postgres (so we get real ``<=>`` cosine search and
ANN indexes) and falls back to JSON on SQLite (used by the unit-test suite).
"""
from __future__ import annotations

from sqlalchemy import Float, JSON
from sqlalchemy.types import TypeDecorator

try:
    from pgvector.sqlalchemy import Vector  # type: ignore
    _HAS_PGVECTOR = True
except Exception:  # pragma: no cover
    _HAS_PGVECTOR = False


class EmbeddingVector(TypeDecorator):
    """Store a fixed-dimension float vector, dialect-aware.

    The nested ``Comparator`` proxies pgvector's distance operators so ORM
    expressions like ``col.cosine_distance(vec)`` (rendered as ``<=>`` on
    Postgres) work even though we wrap ``Vector`` in a TypeDecorator for
    SQLite portability.
    """

    impl = JSON
    cache_ok = True

    class Comparator(TypeDecorator.Comparator):
        def cosine_distance(self, other):
            return self.op("<=>", return_type=Float)(other)

        def l2_distance(self, other):
            return self.op("<->", return_type=Float)(other)

        def max_inner_product(self, other):
            return self.op("<#>", return_type=Float)(other)

    comparator_factory = Comparator

    def __init__(self, dim: int, *args, **kwargs):
        self.dim = dim
        super().__init__(*args, **kwargs)

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql" and _HAS_PGVECTOR:
            return dialect.type_descriptor(Vector(self.dim))
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return list(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return list(value)
