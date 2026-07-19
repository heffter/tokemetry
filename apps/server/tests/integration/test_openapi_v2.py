"""OpenAPI publication and the v2 usage-event JSON schema endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient

#: The v2 paths the OpenAPI document must describe (FR-INGEST-011/012).
_EXPECTED_V2_PATHS = {
    "/api/v2/ingest/events",
    "/api/v2/ingest/validate",
    "/api/v2/ingest/limits",
    "/api/v2/ingest/aggregates",
    "/api/v2/schemas/usage-event",
    "/api/v2/ready",
    "/api/v2/providers",
    "/api/v2/models",
    "/api/v2/sources",
    "/api/v2/sources/{source_id}",
    "/api/v2/sources/{source_id}/revoke",
}


def test_openapi_describes_v2_paths(client: TestClient) -> None:
    document = client.get("/openapi.json").json()
    assert set(document["paths"]) >= _EXPECTED_V2_PATHS
    # Response schemas are published as reusable components.
    schemas = document["components"]["schemas"]
    assert "IngestEventsResponse" in schemas
    assert "ValidateResponse" in schemas
    # Version negotiation is documented in the API description (FR-INGEST-011).
    assert "v1" in document["info"]["description"]
    assert "v2" in document["info"]["description"]


def test_usage_event_schema_endpoint(client: TestClient, auth: dict[str, str]) -> None:
    response = client.get("/api/v2/schemas/usage-event", headers=auth)
    assert response.status_code == 200
    schema = response.json()
    assert schema["title"] == "UsageEventV2"
    assert schema["x-tokemetry-schema-version"] == 2
    assert "event_id" in schema["properties"]
    assert "schema_version" in schema["required"]


def test_usage_event_schema_requires_auth(client: TestClient) -> None:
    assert client.get("/api/v2/schemas/usage-event").status_code == 401
