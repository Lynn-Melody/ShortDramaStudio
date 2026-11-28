from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from django.core.management import call_command
from django.db import OperationalError, connection
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.renderers import OpenApiYamlRenderer


CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "contracts"


def test_public_health_and_admin(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert client.get("/admin/login/").status_code == 200


def test_readiness_when_database_is_unavailable(client):
    with patch.object(connection, "cursor", side_effect=OperationalError):
        response = client.get("/internal/v1/health")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


@pytest.mark.django_db(transaction=True)
def test_readiness_and_worker_use_postgresql_18(client):
    with connection.cursor() as cursor:
        cursor.execute("SHOW server_version_num")
        assert int(cursor.fetchone()[0]) // 10000 == 18
    assert client.get("/internal/v1/health").json() == {"status": "ok"}
    call_command("run_scheduler", check=True)


@pytest.mark.parametrize(("kind", "paths"), [
    ("public", {"/api/v1/health"}),
    ("internal", {
        "/internal/v1/health",
        "/internal/v1/executions/{executionId}/accept",
        "/internal/v1/executions/{executionId}/status",
        "/internal/v1/executions/{executionId}/uploads",
        "/internal/v1/executions/{executionId}/result",
        "/internal/v1/executions/{executionId}/provider-jobs/{stepKey}",
        "/internal/v1/executions/{executionId}/provider-check",
    }),
])
def test_openapi_contract_is_current(kind, paths):
    schema = SchemaGenerator(urlconf=f"studio.{kind}_urls").get_schema(public=True)
    committed = (CONTRACTS_DIR / f"{kind}.yaml").read_bytes().replace(b"\r\n", b"\n")
    assert committed == OpenApiYamlRenderer().render(schema)
    assert yaml.safe_load(committed)["openapi"] == "3.1.0"
    assert set(schema["paths"]) == paths
