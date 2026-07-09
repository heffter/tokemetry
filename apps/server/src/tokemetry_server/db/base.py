"""Declarative base and cross-dialect column types.

The schema targets Postgres in production but must also run on SQLite for
development and tests, so JSON columns use a variant that becomes ``JSONB``
on Postgres and generic ``JSON`` elsewhere. A constraint naming convention
is set so Alembic autogenerate and downgrades produce stable names.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeEngine

#: JSON column type: JSONB on Postgres (indexable, typed), JSON elsewhere.
JSONType: TypeEngine[Any] = JSON().with_variant(JSONB(), "postgresql")

#: Stable constraint/index names keep migrations deterministic across DBs.
_NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base carrying the shared metadata and naming convention."""

    metadata = MetaData(naming_convention=_NAMING_CONVENTION)
