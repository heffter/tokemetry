"""Regenerate the OpenAPI spec and both generated clients (Task 65).

The committed ``openapi.json`` is the single source of truth: the TypeScript
types (openapi-typescript) and the Python models (datamodel-code-generator) are
both derived from it. Run this after any change to the server's public schema,
then commit the regenerated artifacts.

Usage (from the repo root, inside the uv-managed environment)::

    uv run python packages/clients/codegen.py            # regenerate all
    uv run python packages/clients/codegen.py --check     # fail on drift (CI)

``--check`` regenerates every artifact and runs ``git diff --exit-code``; a
non-zero exit means a committed artifact is stale versus the current server
schema. Wire that into CI so a schema change without regenerated clients fails.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_OPENAPI = "packages/clients/openapi.json"
_PY_MODELS = "packages/clients/python/src/tokemetry_client/models.py"
_TS_TYPES = "packages/clients/typescript/src/generated.ts"
_TS_DIR = _ROOT / "packages" / "clients" / "typescript"

# --enum-field-as-literal keeps enum fields as string literals so their defaults
# round-trip through model_dump without pydantic serializer warnings.
_DATAMODEL_CODEGEN = [
    "--from",
    "datamodel-code-generator>=0.25",
    "datamodel-codegen",
    "--input",
    _OPENAPI,
    "--input-file-type",
    "openapi",
    "--output",
    _PY_MODELS,
    "--output-model-type",
    "pydantic_v2.BaseModel",
    "--target-python-version",
    "3.12",
    "--use-schema-description",
    "--use-annotated",
    "--disable-timestamp",
    "--enum-field-as-literal",
    "all",
    "--formatters",
    "black",
]


def _exe(name: str) -> str:
    """Resolve an executable to its full path (handles npm.cmd/git.exe on Windows)."""
    resolved = shutil.which(name)
    if resolved is None:
        raise SystemExit(f"required tool not found on PATH: {name}")
    return resolved


def _run(cmd: list[str], *, cwd: Path) -> None:
    print(f"$ {' '.join(cmd)}")
    subprocess.run(cmd, cwd=cwd, check=True)


def main() -> int:
    """Regenerate the spec and both clients; with --check, fail on any drift."""
    check = "--check" in sys.argv[1:]
    # 1. OpenAPI spec from the FastAPI app (this interpreter has the workspace).
    _run([sys.executable, "packages/clients/generate_openapi.py"], cwd=_ROOT)
    # 2. Python pydantic v2 models.
    _run([_exe("uvx"), *_DATAMODEL_CODEGEN], cwd=_ROOT)
    # 3. TypeScript types.
    _run([_exe("npm"), "run", "generate"], cwd=_TS_DIR)
    if check:
        _run(
            [_exe("git"), "diff", "--exit-code", "--", _OPENAPI, _PY_MODELS, _TS_TYPES],
            cwd=_ROOT,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
