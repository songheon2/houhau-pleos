from fastapi.testclient import TestClient
import pytest

from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as value:
        yield value


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok", "service": "houhau-api"}


def test_no_starter_or_ai_routes(client):
    """Only the route-service API is served: no record store, no generative AI endpoint."""
    paths = {getattr(route, "path", "") for route in app.routes}
    assert not any(p.startswith(("/api/records", "/api/ai", "/api/team")) for p in paths)
    assert "/api/route/reroute/explain" not in paths


def test_windows_active_dependencies_are_pinned():
    """Audit Windows markers even when this test runs on Linux."""
    from importlib.metadata import PackageNotFoundError, distribution
    from pathlib import Path
    import re
    from packaging.markers import default_environment
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    lock = (Path(__file__).resolve().parents[1] / "requirements.local.lock").read_text()
    pins = {canonicalize_name(match.group(1)): match.group(2)
            for match in re.finditer(r'^([A-Za-z0-9_-]+)(?:\[[^]]+\])?==([^ ;\\\n]+)', lock, re.M)}
    assert pins["colorama"] == "0.4.6"
    assert 'colorama==0.4.6 ; sys_platform == "win32"' in lock
    assert "sha256:4f1d9991f5acc0ca119f9d443620b77f9d6b33703e51011c16baf57afb285fc6" in lock
    environment = default_environment()
    environment.update(sys_platform="win32", os_name="nt", platform_system="Windows",
                       platform_machine="AMD64", python_version="3.12", python_full_version="3.12.10")
    for name in pins:
        try:
            requires = distribution(name).requires or []
        except PackageNotFoundError:
            # Colorama is absent on Linux and has no dependencies in this pin.
            # uvloop is intentionally absent on Windows.
            assert name in {"colorama", "uvloop"}, name
            continue
        extras = ["", "standard"] if name == "uvicorn" else [""]
        for raw in requires:
            requirement = Requirement(raw)
            if requirement.marker and not any(
                    requirement.marker.evaluate(dict(environment, extra=extra)) for extra in extras):
                continue
            dep = canonicalize_name(requirement.name)
            assert dep in pins, f"Windows-only dependency missing from lock: {name} -> {raw}"
            assert requirement.specifier.contains(pins[dep]), f"Incompatible pin: {raw}"
