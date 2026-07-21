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


def _spa_client(tmp_path: Path) -> TestClient:
    """A TestClient serving a minimal built SPA (index.html + one asset)."""
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text(
        "<!doctype html><title>tokemetry</title>", encoding="utf-8"
    )
    (static / "assets" / "index-abc.js").write_text("export{}", encoding="utf-8")
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'spa.db'}",
        static_dir=static,
    )
    return TestClient(create_app(settings=settings))


def test_deep_route_falls_back_to_index(tmp_path: Path) -> None:
    """A history-mode deep link resolves to the SPA shell, not a 404.

    Without the fallback, refreshing or bookmarking any non-root route returns
    ``{"detail":"Not Found"}`` and the dashboard never boots.
    """
    with _spa_client(tmp_path) as client:
        for route in ("/trends", "/costs", "/sessions", "/data-quality"):
            resp = client.get(route)
            assert resp.status_code == 200, route
            assert "tokemetry" in resp.text, route


def test_existing_asset_is_served(tmp_path: Path) -> None:
    with _spa_client(tmp_path) as client:
        resp = client.get("/assets/index-abc.js")
        assert resp.status_code == 200
        assert "export" in resp.text


def test_missing_asset_stays_404(tmp_path: Path) -> None:
    """A missing build chunk must error, not silently return the SPA shell."""
    with _spa_client(tmp_path) as client:
        resp = client.get("/assets/index-does-not-exist.js")
        assert resp.status_code == 404
        assert "tokemetry" not in resp.text


def test_unknown_api_route_stays_json_404(tmp_path: Path) -> None:
    """An unmatched API path keeps its JSON 404 rather than the SPA shell."""
    with _spa_client(tmp_path) as client:
        resp = client.get("/api/v1/does-not-exist")
        assert resp.status_code == 404
        assert "tokemetry" not in resp.text
