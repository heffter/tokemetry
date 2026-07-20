"""Generate the tokemetry OpenAPI spec for client code generation (Task 65).

Emits ``packages/clients/openapi.json`` from the FastAPI app, augmented with the
published usage-event and limit-snapshot wire schemas (``UsageEventV2``,
``LimitSnapshotV2``) under ``components.schemas``. The ingest endpoints validate
their bodies manually, so FastAPI does not emit those input schemas; injecting
them here makes the spec a single source for the generated clients.

Run: ``python packages/clients/generate_openapi.py`` (or ``just gen-openapi``).
The generated clients are regenerated from the committed openapi.json, and a
drift check re-runs this and fails if the committed spec is stale.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

os.environ.setdefault("TOKEMETRY_DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from tokemetry_core.usage_v2 import (  # noqa: E402
    LimitSnapshotV2,
    usage_event_json_schema,
)
from tokemetry_server.app import create_app  # noqa: E402

_OUT = Path(__file__).with_name("openapi.json")


def _defs_to_components(schema: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    """Lift a JSON-Schema ``$defs`` block into shared ``components.schemas``.

    Pydantic emits nested models under ``$defs`` and references them as
    ``#/$defs/Name``; OpenAPI clients expect ``#/components/schemas/Name``. Move
    each def into components and rewrite the refs.
    """
    # Rewrite every ``#/$defs/`` ref -- in the top-level schema AND inside the
    # nested defs -- before lifting, so a lifted def's internal refs also point
    # at components (e.g. SourceRef.type -> SourceType).
    rewritten: dict[str, Any] = json.loads(
        json.dumps(schema).replace("#/$defs/", "#/components/schemas/")
    )
    for name, definition in rewritten.pop("$defs", {}).items():
        components.setdefault(name, definition)
    return rewritten


def build_spec() -> dict[str, Any]:
    """Build the augmented OpenAPI spec."""
    spec: dict[str, Any] = create_app().openapi()
    components: dict[str, Any] = spec.setdefault("components", {}).setdefault(
        "schemas", {}
    )
    components["UsageEventV2"] = _defs_to_components(
        usage_event_json_schema(), components
    )
    components["LimitSnapshotV2"] = _defs_to_components(
        LimitSnapshotV2.model_json_schema(), components
    )
    return spec


def main() -> None:
    """Write the spec with a trailing newline for a stable diff."""
    spec = build_spec()
    _OUT.write_text(
        json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {_OUT} ({len(spec['paths'])} paths)")


if __name__ == "__main__":
    main()
