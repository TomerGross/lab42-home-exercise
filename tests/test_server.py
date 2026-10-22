import socket

import pytest
from fastapi.testclient import TestClient

from service_monitor.server import create_app, get_manager
from tests.conftest import sqlite_url
from tests.helpers import closed_port

SERVICE_A = {"name": "Service A", "host": "127.0.0.1", "port": 8001}
SERVICE_B = {"name": "Service B", "host": "127.0.0.1", "port": 8002}
PAYLOAD = {"services": [SERVICE_A, SERVICE_B]}


@pytest.fixture
def client(tmp_path):
    app = create_app(sqlite_url(tmp_path / "api.db"), check_timeout_seconds=0.5)
    with TestClient(app) as client:
        yield client


def add_service(client, name: str, port: int):
    client.post("/services", json={"services": [{"name": name, "host": "127.0.0.1", "port": port}]})


def test_add_services_returns_201_with_unknown_status(client):
    response = client.post("/services", json=PAYLOAD)

    assert response.status_code == 201
    assert [service["status"] for service in response.json()] == ["UNKNOWN", "UNKNOWN"]


def test_list_services_returns_fields_in_assignment_order(client):
    client.post("/services", json=PAYLOAD)

    services = client.get("/services").json()

    assert [service["name"] for service in services] == ["Service A", "Service B"]
    assert list(services[0]) == [
        "name", "host", "port", "status", "last_checked", "response_time_ms", "failure_reason",
    ]


def test_get_service_with_space_in_name(client):
    client.post("/services", json=PAYLOAD)

    response = client.get("/services/Service%20A")

    assert response.status_code == 200
    assert response.json()["port"] == 8001


@pytest.mark.parametrize(
    ("method", "path"),
    [("get", "/services/missing"), ("post", "/services/missing/check"), ("delete", "/services/missing")],
)
def test_unknown_service_returns_404(client, method, path):
    response = client.request(method, path)

    assert response.status_code == 404
    assert response.json()["error"] == "unknown_service"


def test_delete_service_returns_204_and_service_is_gone(client):
    client.post("/services", json=PAYLOAD)

    response = client.delete("/services/Service%20A")

    assert response.status_code == 204
    assert response.content == b""
    assert client.get("/services/Service%20A").status_code == 404


def test_duplicate_returns_409_and_saves_nothing(client):
    client.post("/services", json=PAYLOAD)
    new_service = {"name": "New", "host": "127.0.0.1", "port": 9000}

    response = client.post("/services", json={"services": [new_service, SERVICE_A]})

    assert response.status_code == 409
    assert response.json()["error"] == "duplicate_service"
    assert len(client.get("/services").json()) == 2


def test_invalid_service_returns_422_with_field_details(client):
    bad_service = {"name": "X", "host": "http://x.com", "port": 70000}

    response = client.post("/services", json={"services": [bad_service]})

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"
    fields = {problem["field"] for problem in response.json()["detail"]}
    assert fields == {"services.0.host", "services.0.port"}


@pytest.mark.parametrize("body", [{"services": []}, {}, {"wrong": []}], ids=["empty", "no-body", "wrong-key"])
def test_missing_or_empty_services_returns_422(client, body):
    response = client.post("/services", json=body)

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"


def test_malformed_json_returns_422(client):
    response = client.post("/services", content="{not json", headers={"Content-Type": "application/json"})

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"


def test_check_reachable_service_is_up(client):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        add_service(client, "Live", listener.getsockname()[1])

        service = client.post("/services/Live/check").json()

    assert service["status"] == "UP"
    assert service["response_time_ms"] >= 0
    assert service["last_checked"].endswith("Z")


def test_check_unreachable_service_is_down_but_returns_200(client):
    add_service(client, "Dead", closed_port())

    response = client.post("/services/Dead/check")

    # On Windows a refused localhost connection can exceed the 0.5s timeout, so only DOWN is asserted.
    assert response.status_code == 200
    assert response.json()["status"] == "DOWN"
    assert response.json()["failure_reason"] is not None


@pytest.mark.parametrize(
    ("method", "path", "status_code", "error"),
    [("get", "/nope", 404, "not_found"), ("delete", "/services", 405, "method_not_allowed")],
)
def test_http_errors_use_the_error_shape(client, method, path, status_code, error):
    response = client.request(method, path)

    assert response.status_code == status_code
    assert response.json()["error"] == error


def test_unexpected_error_returns_500_and_server_keeps_running(tmp_path):
    def broken_manager():
        raise RuntimeError("boom")

    app = create_app(sqlite_url(tmp_path / "api.db"))
    app.dependency_overrides[get_manager] = broken_manager
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/services")
        assert response.status_code == 500
        assert response.json()["error"] == "internal_error"

        app.dependency_overrides.clear()
        assert client.get("/services").status_code == 200


def test_data_survives_an_app_restart(tmp_path):
    url = sqlite_url(tmp_path / "restart.db")
    with TestClient(create_app(url)) as client:
        client.post("/services", json=PAYLOAD)

    with TestClient(create_app(url)) as client:
        assert len(client.get("/services").json()) == 2
