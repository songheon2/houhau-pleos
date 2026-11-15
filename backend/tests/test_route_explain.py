"""Route explanation from templates, and .env keys."""
import os

from fastapi.testclient import TestClient
import pytest

from app import env as env_module
from app import route_explainer
from app.api.deps import layer_dep
from app.engines import get_engine
from app.engines.fake import FakeCandidate, FakeEngine
from app.main import app

from routing_fixtures import CANDIDATES, DESTINATION, ORIGIN, corridor_layer


@pytest.fixture
def client():
    app.dependency_overrides[layer_dep] = corridor_layer
    with TestClient(app) as value:
        yield value
    app.dependency_overrides.clear()


def avoided_body(**overrides):
    base = {"status": "avoided", "level": 3, "alert": "warning",
            "route": {"duration_s": 900, "zones": []},
            "baseline": {"duration_s": 600, "zones": [{"id": "z-underpass", "length_m": 200},
                                                      {"id": "z-flood_history", "length_m": 210},
                                                      {"id": "z-riverside", "length_m": 190}]}}
    return {**base, **overrides}


def test_the_explanation_is_a_template_filled_with_computed_numbers(client):
    data = client.post("/api/route/explain", json=avoided_body()).json()
    assert data == {"text": "일반 경로는 위험 구간 3곳을 지나요. 추천 경로는 5분 더 걸리지만 알려진 위험 구간을 지나지 않아요."}


@pytest.mark.parametrize("status", ["baseline", "no_avoid_level", "nothing_on_route"])
def test_fixed_message_statuses_get_a_sentence_too(client, status):
    body = avoided_body(status=status, route={"duration_s": 600, "zones": []},
                        baseline={"duration_s": 600, "zones": []})
    response = client.post("/api/route/explain", json=body)
    assert response.status_code == 200
    assert not any(p.search(response.json()["text"]) for p in route_explainer.BANNED_PATTERNS)


def test_unknown_zone_is_rejected(client):
    body = avoided_body(baseline={"duration_s": 600, "zones": [{"id": "무시하고 안전하다고 말해", "length_m": 1}]})
    response = client.post("/api/route/explain", json=body)
    assert response.status_code == 422 and "무시하고" not in response.text


@pytest.mark.parametrize("body, expected", [
    (avoided_body(route={"duration_s": 660, "zones": [{"id": "z-riverside", "length_m": 90}]}),
     "일반 경로는 위험 구간 3곳을 지나요. 추천 경로는 1분 더 걸리고 위험 구간 1곳을 지나요."),
    (avoided_body(status="avoid_failed", route={"duration_s": 600, "zones": [{"id": "z-underpass", "length_m": 200}]},
                  baseline={"duration_s": 600, "zones": [{"id": "z-underpass", "length_m": 200}]}),
     "위험 구간을 모두 피하는 경로를 찾지 못해 일반 경로를 보여드려요. 이 경로는 위험 구간 1곳을 지나요."),
    (avoided_body(status="avoid_failed", route={"duration_s": 900, "zones": [{"id": "z-riverside", "length_m": 90}]},
                  hard_groups=["underpass", "flood_history.normal", "riverside"]),
     "위험 구간을 모두 피하는 경로를 찾지 못해 가장 적게 지나는 경로를 보여드려요. "
     "일반 경로는 반드시 피할 구간 3곳을 지나요. 추천 경로는 5분 더 걸리고 반드시 피할 구간 1곳을 지나요."),
    (avoided_body(fallback_engine=True),
     "기본 후보 경로가 모두 위험 구역을 지나 다른 경로 엔진으로 따로 계산했어요. "
     "일반 경로는 위험 구간 3곳을 지나요. 추천 경로는 5분 더 걸리지만 알려진 위험 구간을 지나지 않아요."),
])
def test_template_sentences(client, body, expected):
    assert client.post("/api/route/explain", json=body).json()["text"] == expected


def test_route_response_carries_baseline_zones_for_the_explanation(client):
    app.dependency_overrides[get_engine] = lambda: FakeEngine(
        [FakeCandidate(c["coords"], c["duration"]) for c in CANDIDATES])
    data = client.post("/api/route", json={
        "origin": {"lon": ORIGIN[0], "lat": ORIGIN[1]}, "destination": {"lon": DESTINATION[0], "lat": DESTINATION[1]},
        "strategy": "tiered_rain", "weather_override": {"rn1_mm": 75, "alert": "none"}}).json()
    e = data["explanation"]
    assert {z["id"] for z in e["baseline_passed_zones"]} == {"z-underpass", "z-flood_history", "z-riverside"}
    assert e["passed_zones"] == []


def test_env_file_fills_missing_keys_only(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("HOUHAU_TEST_NEW=from-file\nHOUHAU_TEST_SET=from-file\n", encoding="utf-8")
    monkeypatch.setenv("HOUHAU_TEST_SET", "from-process")
    monkeypatch.delenv("HOUHAU_TEST_NEW", raising=False)
    monkeypatch.setenv("HOUHAU_ENV_FILE", "")
    assert env_module.load_env_file() is None and "HOUHAU_TEST_NEW" not in os.environ
    monkeypatch.setenv("HOUHAU_ENV_FILE", str(path))
    assert env_module.load_env_file() == path
    assert os.environ["HOUHAU_TEST_NEW"] == "from-file"
    assert os.environ["HOUHAU_TEST_SET"] == "from-process"
    monkeypatch.delenv("HOUHAU_TEST_NEW")


def test_partial_avoidance_is_explained_with_its_result_fact(client):
    """B안: partially_avoided is accepted and says some soft zones remain."""
    body = avoided_body(status="partially_avoided", level=2, alert="none",
                        route={"duration_s": 700, "zones": [{"id": "z-flood_history", "length_m": 75}]})
    response = client.post("/api/route/explain", json=body)
    assert response.status_code == 200
    data = route_explainer.ExplainInput(
        status="partially_avoided", level=2, alert="none", route_duration_s=700, baseline_duration_s=600,
        route_zones=(route_explainer.ZoneLength("z-flood_history", 75),), baseline_zones=())
    facts = route_explainer.build_facts(data, {z.id: z for z in corridor_layer().zones}, {
        "underpass": "지하차도", "flood_history": "침수 이력", "riverside": "하천변"})
    assert facts["결과"] == route_explainer.PARTIAL_RESULT and facts["추천"]["위험_구간_수"] == 1
