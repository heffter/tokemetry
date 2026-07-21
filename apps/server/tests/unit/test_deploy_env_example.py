"""The Compose env template documents every variable the Compose file needs.

``docs/deployment/server.md`` tells operators to ``cp .env.example .env`` before
``docker compose up``. If a ``${VAR}`` referenced by ``deploy/docker-compose.yml``
were missing from ``deploy/.env.example``, that first deploy would come up with
an unset (or wrongly defaulted) value. This test keeps the template complete.
"""

import re
from pathlib import Path

_DEPLOY = Path(__file__).parents[4] / "deploy"
_COMPOSE = _DEPLOY / "docker-compose.yml"
_ENV_EXAMPLE = _DEPLOY / ".env.example"

# Variables Compose interpolates from the environment: ${NAME}, ${NAME:-x},
# ${NAME:?x}. Names are upper snake case by convention.
_VAR_REF = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)")
# Assignments in the env template: NAME=value at the start of a line.
_ENV_DEF = re.compile(r"^([A-Z_][A-Z0-9_]*)=", re.MULTILINE)

# Compose sets these from other variables rather than reading them from .env,
# so they are not expected to be defined in the template.
_COMPOSED = frozenset({"TOKEMETRY_DATABASE_URL"})


def test_env_example_exists() -> None:
    assert _ENV_EXAMPLE.is_file(), f"{_ENV_EXAMPLE} is missing"


def test_env_example_documents_every_compose_variable() -> None:
    referenced = set(_VAR_REF.findall(_COMPOSE.read_text(encoding="utf-8"))) - _COMPOSED
    defined = set(_ENV_DEF.findall(_ENV_EXAMPLE.read_text(encoding="utf-8")))
    missing = referenced - defined
    assert not missing, f"deploy/.env.example is missing: {sorted(missing)}"


def test_required_secrets_are_present() -> None:
    text = _ENV_EXAMPLE.read_text(encoding="utf-8")
    # The two variables Compose marks required (${VAR:?...}) must be present so
    # the documented deploy path does not fail on an unset value.
    for required in ("POSTGRES_PASSWORD", "TOKEMETRY_API_BOOTSTRAP_TOKEN"):
        assert re.search(rf"^{required}=", text, re.MULTILINE), f"{required} not in template"
