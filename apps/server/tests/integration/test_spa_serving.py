"""Test that the server serves the dashboard SPA when configured."""

from pathlib import Path

from fastapi.testclient import TestClient
from tokemetry_server.app import create_app
from tokemetry_server.config import Settings


def test_serves_index_when_static_dir_set(tmp_path: Path) -> None:
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<h1>tokemetry</h1>", encoding="utf-8")
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'spa.db'}",
        static_dir=static,
    )

    with TestClient(create_app(settings=settings)) as client:
        # API still takes precedence.
        assert client.get("/api/v1/health").json() == {"status": "ok"}
        # Root serves the SPA index.
        root = client.get("/")
        assert root.status_code == 200
        assert "tokemetry" in root.text


def test_api_only_when_static_dir_unset(tmp_path: Path) -> None:
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'spa.db'}")
    with TestClient(create_app(settings=settings)) as client:
        assert client.get("/api/v1/health").status_code == 200
        # No SPA mounted -> root is not found.
        assert client.get("/").status_code == 404
