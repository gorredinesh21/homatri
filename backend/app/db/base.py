"""SQLAlchemy declarative base + portable column types.

Portability: the same models run on SQLite (dev/tests, no install) and
PostgreSQL (deploy). JSONB maps to JSONB on Postgres and JSON on SQLite.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import JSONB as _PG_JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Portable JSON: JSONB on Postgres, JSON on SQLite.
JSONB = JSON().with_variant(_PG_JSONB, "postgresql")

# Timezone-aware timestamp: TIMESTAMPTZ on Postgres, TEXT/naive on SQLite.
TS = DateTime(timezone=True)

# Explicit constraint naming (clean Alembic autogenerate).
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """created_at / updated_at, portable across SQLite & Postgres."""

    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TS, server_default=func.now(), onupdate=func.now(), nullable=False
    )
