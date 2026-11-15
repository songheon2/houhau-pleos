"""Rain gets worse during a drive: re-check, change request, template explanation."""
from fastapi.testclient import TestClient
import pytest

from app import route_explainer
from app.api.deps import layer_dep
from app.api.explain import zone_labels
from app.config import get_config, rain_level
from app.engines import get_engine
from app.engines.base import EngineUnavailable
from app.engines.fake import FakeCandidate, FakeEngine
from app.engines.valhalla import build_request
from app.main import app
from app.route_explainer import RerouteInput, ZoneLength, reroute_facts, reroute_template

from routing_fixtures import CANDIDATES, DESTINATION, ORIGIN, corridor_layer, detour

STRAIGHT = [list(ORIGIN), list(DESTINATION)]


def fake_engine(**kwargs):
    return FakeEngine([FakeCandidate(c["coords"], c["duration"]) for c in CANDIDATES], **kwargs)


@pytest.fixture
def setup():
    state = {"engine": fake_engine()}
    app.dependency_overrides[get_engine] = lambda: state["engine"]
    app.dependency_overrides[layer_dep] = corridor_layer
    with TestClient(app) as client:
        yield client, state
    app.dependency_overrides.clear()


def body(rn1=75, remaining=None, **overrides):
    base = {"position": {"lon": ORIGIN[0], "lat": ORIGIN[1]}, "destination": {"lon": DESTINATION[0], "lat": DESTINATION[1]},
            "heading": 90, "remaining": remaining or STRAIGHT, "remaining_duration_s": 600,
            "previous": {"rn1_mm": 0, "alert": "none"}, "weather_override": {"rn1_mm": rn1, "alert": "none"}}
    return {**base, **overrides}


def test_worse_rain_asks_to_change_a_route_that_passes_avoided_zones(setup):
    client, state = setup
    data = client.post("/api/route/reroute", json=body()).json()
    assert data["action"] == "recommend" and data["status"] == "avoided"
    assert data["previous_level"] == 0 and data["weather"]["level"] == 3
    assert len(data["current"]["hard_zone_ids"]) == 3 and data["candidate"]["hard_zone_ids"] == []
    assert data["avoided_zone_ids"] == ["z-flood_history", "z-riverside", "z-underpass"]
    assert data["route"]["duration"] == 900 and data["current"]["duration_s"] == 600
    assert data["text"] == ("시간당 강수량이 0mm에서 75mm로 늘어 강수 단계가 0단계에서 3단계로 올랐어요. "
                            "지금 경로는 남은 길에서 반드시 피할 구간 3곳을 지나요. "
                            "변경 경로는 5분 더 걸리지만 알려진 위험 구간을 지나지 않아요.")
    assert all(call["heading"] == 90 for call in state["engine"].calls)  # the car's direction reaches the engine


def test_the_rules_of_the_level_apply_whatever_algorithm_drew_the_route(setup):
    # A route from baseline or rerank is judged the same way: the API takes no algorithm at all.
    client, _ = setup
    data = client.post("/api/route/reroute", json=body()).json()
    assert "underpass" in data["hard_groups"] and data["action"] == "recommend"
    assert client.post("/api/route/reroute", json=body(strategy="baseline")).status_code == 422


def test_only_zones_of_the_new_level_count(setup):
    # Level 1 avoids underpasses and high flood zones. This remainder passes only a "normal" flood zone.
    client, _ = setup
    remaining = [[126.84, 35.15], [126.86, 35.15]]
    data = client.post("/api/route/reroute", json=body(rn1=35, remaining=remaining)).json()
    assert data["action"] == "keep"
    assert data["current"]["hard_zone_ids"] == data["current"]["soft_zone_ids"] == []
    assert [z["id"] for z in data["current"]["zones"]] == ["z-flood_history"]
    assert "피할 구간이 없어" in data["text"]


def test_no_better_when_the_candidate_passes_the_same_zones(setup):
    client, state = setup
    state["engine"] = FakeEngine([FakeCandidate(CANDIDATES[0]["coords"], 600.0)])
    data = client.post("/api/route/reroute", json=body()).json()
    assert data["action"] == "no_better"
    assert len(data["candidate"]["hard_zone_ids"]) == 3
    assert "더 나은 경로를 찾지 못해" in data["text"]


def test_when_nothing_avoids_every_zone_the_route_passing_the_fewest_is_offered(setup):
    # Level 3 must avoid all three zones, but the only detour still passes two of them.
    client, state = setup
    state["engine"] = FakeEngine([FakeCandidate(CANDIDATES[0]["coords"], 600.0), FakeCandidate(detour(126.83), 700.0)])
    data = client.post("/api/route/reroute", json=body()).json()
    assert data["action"] == "recommend" and data["weather"]["level"] == 3
    assert len(data["current"]["hard_zone_ids"]) == 3 and len(data["candidate"]["hard_zone_ids"]) == 2  # still level 3's rules
    assert "반드시 피할 구간 3곳" in data["text"] and "반드시 피할 구간 2곳을 지나요" in data["text"]


def test_a_car_already_inside_a_zone_still_gets_a_route_out(setup):
    # The position is inside the underpass zone: excluding it would leave no start at all.
    client, state = setup
    start = (126.82, 35.15)
    state["engine"] = FakeEngine([FakeCandidate([start, DESTINATION], 600.0),
                                  FakeCandidate([start, (126.83, 35.15), (126.83, 35.16), (126.89, 35.16),
                                                 (126.89, 35.15), DESTINATION], 800.0)])
    data = client.post("/api/route/reroute", json=body(position={"lon": start[0], "lat": start[1]},
                                                       remaining=[list(start), list(DESTINATION)])).json()
    assert data["action"] == "recommend" and data["candidate"]["hard_zone_ids"] == ["z-underpass"]
    assert len(data["current"]["hard_zone_ids"]) == 3


def test_a_detour_past_soft_zones_must_be_worth_its_time(setup):
    # Level 2: the "normal" flood zone is soft. A 1 hour detour to skip it costs more than its penalty.
    client, state = setup
    remaining = [[126.84, 35.15], [126.86, 35.15]]
    state["engine"] = FakeEngine([FakeCandidate(detour(126.86), 3600.0)])
    data = client.post("/api/route/reroute", json=body(rn1=55, remaining=remaining, remaining_duration_s=60)).json()
    assert data["current"]["soft_zone_ids"] == ["z-flood_history"] and data["candidate"]["soft_zone_ids"] == []
    assert data["action"] == "no_better"
    state["engine"] = FakeEngine([FakeCandidate(detour(126.86), 70.0)])
    data = client.post("/api/route/reroute", json=body(rn1=55, remaining=remaining, remaining_duration_s=60)).json()
    assert data["action"] == "recommend"


def test_the_template_names_an_alert_and_a_faster_route(setup):
    client, _ = setup
    data = client.post("/api/route/reroute", json=body(weather_override={"rn1_mm": 0, "alert": "warning"},
                                                       remaining_duration_s=1200)).json()
    assert data["weather"]["level"] == 2 and data["action"] == "recommend"
    assert data["text"].startswith("호우경보가 내려져 강수 단계가 0단계에서 2단계로 올랐어요.")
    assert "덜 걸리" in data["text"]


def test_engine_down_and_bad_input(setup):
    client, state = setup
    state["engine"] = fake_engine(error=EngineUnavailable())
    assert client.post("/api/route/reroute", json=body()).status_code == 503
    state["engine"] = fake_engine()
    assert client.post("/api/route/reroute", json=body(remaining=[[126.8, 35.15]])).status_code == 422
    outside = body(position={"lon": 127.5, "lat": 36.0})
    assert client.post("/api/route/reroute", json=outside).status_code == 422


def test_valhalla_request_carries_the_heading_only_when_given():
    assert "heading" not in build_request(ORIGIN, DESTINATION, (), 0)["locations"][0]
    assert build_request(ORIGIN, DESTINATION, (), 0, heading=-90.4)["locations"][0]["heading"] == 270


def explain_body(**overrides):
    base = {"action": "recommend", "previous": {"rn1_mm": 0}, "now": {"rn1_mm": 75},
            "hard_groups": ["underpass", "flood_history.high", "flood_history.normal", "flood_history.raised", "riverside"],
            "current": {"duration_s": 600, "zones": [{"id": "z-underpass", "length_m": 200},
                                                     {"id": "z-flood_history", "length_m": 210},
                                                     {"id": "z-riverside", "length_m": 190}]},
            "candidate": {"duration_s": 900, "zones": []}}
    return {**base, **overrides}


def template(**overrides):
    """The change-request sentence for these facts, as /api/route/reroute writes it."""
    b = explain_body(**overrides)
    config = get_config()
    rules = config.rain_levels.thresholds_mm, config.alert_min_level.model_dump()

    def level(rain):
        return rain_level(rain["rn1_mm"], rain.get("alert", "none"), *rules)

    def zones(side):
        return tuple(ZoneLength(z["id"], z["length_m"]) for z in side["zones"])

    data = RerouteInput(action=b["action"], previous_rn1_mm=b["previous"]["rn1_mm"],
                        previous_alert=b["previous"].get("alert", "none"), previous_level=level(b["previous"]),
                        rn1_mm=b["now"]["rn1_mm"], alert=b["now"].get("alert", "none"), level=level(b["now"]),
                        current_duration_s=b["current"]["duration_s"], candidate_duration_s=b["candidate"]["duration_s"],
                        current_zones=zones(b["current"]), candidate_zones=zones(b["candidate"]),
                        hard_groups=tuple(b["hard_groups"]), soft_groups=tuple(b.get("soft_groups", [])))
    labels, tier_labels = zone_labels(config)
    return reroute_template(reroute_facts(data, {z.id: z for z in corridor_layer().zones}, labels, tier_labels))


def test_change_request_template_uses_the_computed_numbers():
    text = template()
    assert text.startswith("시간당 강수량이") and "5분 더 걸" in text
    assert not any(p.search(text) for p in route_explainer.BANNED_PATTERNS)


FLOOD_RIVER = ["flood_history.normal", "riverside"]


@pytest.mark.parametrize("hard, soft, candidate, phrase", [
    (FLOOD_RIVER + ["underpass"], [], {"duration_s": 900, "zones": []}, "알려진 위험 구간을 지나지 않아요."),
    (FLOOD_RIVER + ["underpass"], [], {"duration_s": 600, "zones": [{"id": "z-riverside", "length_m": 90}]},
     "소요시간이 비슷하고 반드시 피할 구간 1곳을 지나요."),
    (["underpass"], ["riverside"], {"duration_s": 900, "zones": [{"id": "z-flood_history", "length_m": 90}]},
     "그 밖의 위험 구간 1곳은 지나요."),
    (["underpass"], ["riverside"], {"duration_s": 900, "zones": [{"id": "z-riverside", "length_m": 90}]},
     "5분 더 걸리고 반드시 피할 구간은 지나지 않지만 되도록 피할 구간 1곳은 지나요."),
])
def test_templates_never_overstate_the_new_route(hard, soft, candidate, phrase):
    text = template(hard_groups=hard, soft_groups=soft, candidate=candidate)
    assert phrase in text
    assert not any(p.search(text) for p in route_explainer.BANNED_PATTERNS)


def test_added_time_matches_the_seconds_shown_on_screen():
    # 866 s and 1191 s round to 14 and 20 minutes, but the difference is 5 minutes 25 seconds.
    text = template(current={"duration_s": 866, "zones": [{"id": "z-riverside", "length_m": 90}]},
                    candidate={"duration_s": 1191, "zones": []})
    assert "5분 더 걸리지만" in text


def test_no_claim_of_rising_rain_when_the_level_did_not_rise():
    text = template(previous={"rn1_mm": 75}, now={"rn1_mm": 0}, action="keep")
    assert text.startswith("강수 정보를 다시 확인했어요.") and "올랐어요" not in text


def test_the_explain_endpoint_is_gone(setup):
    """The reason comes with /api/route/reroute itself; there is no separate wording call."""
    client, _ = setup
    assert client.post("/api/route/reroute/explain", json=explain_body()).status_code in (404, 405)
