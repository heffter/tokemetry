"""ORM round-trip and constraint tests for the registry tables.

Exercised against every supported engine via the ``migrated_engine`` fixture
(SQLite always, Postgres when ``TOKEMETRY_TEST_POSTGRES_URL`` is set) so the
providers/models/model_aliases schema behaves identically on both.
"""

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from tokemetry_server.db.models import Model, ModelAlias, Provider

_TS = datetime(2026, 7, 18, 12, 0, 0, tzinfo=UTC)


def test_provider_round_trip(migrated_engine: sa.Engine) -> None:
    with Session(migrated_engine) as session:
        session.add(
            Provider(
                id="anthropic",
                display_name="Anthropic",
                aliases=["claude", "claude-code"],
                pricing_strategy="anthropic",
                limit_semantics="anthropic_oauth_windows",
                supported_dimensions=["machine", "model", "project", "session"],
                registered=True,
                created_at=_TS,
                updated_at=_TS,
            )
        )
        session.commit()

    with Session(migrated_engine) as session:
        row = session.get(Provider, "anthropic")
        assert row is not None
        assert row.display_name == "Anthropic"
        assert row.aliases == ["claude", "claude-code"]
        assert row.supported_dimensions == ["machine", "model", "project", "session"]
        assert row.registered is True


def test_provider_defaults_applied(migrated_engine: sa.Engine) -> None:
    """Column defaults fill in the optional registry metadata."""
    with Session(migrated_engine) as session:
        session.add(
            Provider(id="mistral", display_name="Mistral", created_at=_TS, updated_at=_TS)
        )
        session.commit()

    with Session(migrated_engine) as session:
        row = session.get(Provider, "mistral")
        assert row is not None
        assert row.aliases == []
        assert row.pricing_strategy == ""
        assert row.limit_semantics == "none"
        assert row.supported_dimensions == []
        assert row.registered is True


def test_model_round_trip(migrated_engine: sa.Engine) -> None:
    with Session(migrated_engine) as session:
        session.add(
            Model(
                provider="anthropic",
                native_model_id="claude-opus-4-6",
                lifecycle="active",
                capabilities={"vision": True, "max_output": 64000},
                first_seen=_TS,
                last_seen=_TS,
            )
        )
        session.commit()

    with Session(migrated_engine) as session:
        row = session.get(Model, ("anthropic", "claude-opus-4-6"))
        assert row is not None
        assert row.lifecycle == "active"
        assert row.capabilities["vision"] is True
        assert row.capabilities["max_output"] == 64000


def test_model_composite_pk_rejects_duplicate(migrated_engine: sa.Engine) -> None:
    with Session(migrated_engine) as session:
        session.add(
            Model(provider="anthropic", native_model_id="m1", lifecycle="active", capabilities={})
        )
        session.commit()

    with Session(migrated_engine) as session:
        session.add(
            Model(
                provider="anthropic", native_model_id="m1", lifecycle="deprecated", capabilities={}
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_same_native_id_different_provider_allowed(migrated_engine: sa.Engine) -> None:
    """The provider is part of the model grain, so ids may repeat per provider."""
    with Session(migrated_engine) as session:
        session.add(
            Model(
                provider="anthropic",
                native_model_id="shared",
                lifecycle="active",
                capabilities={},
            )
        )
        session.add(
            Model(
                provider="openai",
                native_model_id="shared",
                lifecycle="active",
                capabilities={},
            )
        )
        session.commit()

    with Session(migrated_engine) as session:
        count = session.scalar(sa.select(sa.func.count()).select_from(Model))
        assert count == 2


def test_model_alias_round_trip(migrated_engine: sa.Engine) -> None:
    with Session(migrated_engine) as session:
        session.add(
            ModelAlias(
                provider="anthropic",
                alias="opus",
                native_model_id="claude-opus-4-6",
                rule_version=1,
            )
        )
        session.commit()

    with Session(migrated_engine) as session:
        row = session.execute(
            sa.select(ModelAlias).where(ModelAlias.alias == "opus")
        ).scalar_one()
        assert row.native_model_id == "claude-opus-4-6"
        assert row.rule_version == 1


def test_duplicate_alias_rejected(migrated_engine: sa.Engine) -> None:
    """The (provider, alias) grain must be unique (FR-MODEL-009)."""
    with Session(migrated_engine) as session:
        session.add(
            ModelAlias(provider="anthropic", alias="opus", native_model_id="a", rule_version=1)
        )
        session.commit()

    with Session(migrated_engine) as session:
        session.add(
            ModelAlias(provider="anthropic", alias="opus", native_model_id="b", rule_version=1)
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_same_alias_different_provider_allowed(migrated_engine: sa.Engine) -> None:
    with Session(migrated_engine) as session:
        session.add(
            ModelAlias(provider="anthropic", alias="fast", native_model_id="haiku", rule_version=1)
        )
        session.add(
            ModelAlias(provider="openai", alias="fast", native_model_id="gpt-fast", rule_version=1)
        )
        session.commit()

    with Session(migrated_engine) as session:
        count = session.scalar(
            sa.select(sa.func.count()).select_from(ModelAlias).where(ModelAlias.alias == "fast")
        )
        assert count == 2
