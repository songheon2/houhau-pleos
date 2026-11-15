"""Sentences explaining a route choice and a change request, filled from templates.

The route logic has already chosen the route and computed every number. This module turns those
facts into short Korean sentences with fixed templates, so the wording never goes beyond what
was computed and needs no outside service:

- build_facts() / reroute_facts(): finished facts only (no coordinates). Zone names and sources
  come from the server's risk layer, never from the client.
- template_text() / reroute_template(): the sentences shown on screen and read aloud.
"""
from dataclasses import dataclass
import re
from typing import Any, Iterable

from .config import ZONE_GROUPS
from .risk.layer import RiskZone
from .strategies.base import MESSAGES

MAX_ZONES_IN_FACTS = 5

# Statuses whose screen message is already fixed.
FIXED_STATUSES = {"baseline", "no_avoid_level", "nothing_on_route"}
ALERT_MODES = {"warning": "호우경보", "advisory": "호우주의보"}
AVOID_FAILED_RESULT = "피해야 할 위험 구간을 모두 피하는 경로를 찾지 못해 일반 경로를 그대로 보여줌"
FEWEST_RESULT = "피해야 할 위험 구간을 모두 피하는 경로를 찾지 못해 가장 적게 지나는 경로를 보여줌"
# B안: hard zones are all avoided, some soft zones remain on the route.
PARTIAL_RESULT = "반드시 피할 위험 구간은 모두 피했고, 되도록 피할 위험 구간 일부는 지남"

# Words the templates must never use: no promise of safety, no claim about flooding now,
# no order to drive or to change the route (the driver decides).
BANNED_PATTERNS = [re.compile(p) for p in (
    r"안전",
    r"보장",
    r"침수\s*(?:되|돼)?\s*지\s*않",
    r"침수\s*(?:걱정|위험)\s*(?:이|은|도)?\s*없",
    r"위험\s*(?:이|은|도)?\s*없",
    r"(?:현재|지금)\s*(?:은|도)?\s*(?:도로가\s*)?침수",
    r"침수\s*(?:되어|돼)\s*있",
    r"운전\s*(?:하세요|하십시오|해\s*주세요|을\s*권|해도)",
    r"(?:출발|이동)\s*(?:하세요|하십시오|해\s*주세요)",
    r"다른\s*경로\s*(?:를|로)\s*(?:이용|추천|선택)",
    r"(?:변경|우회)\s*(?:하세요|하십시오|해\s*주세요)",  # the driver decides
    r"(?:바꾸세요|바꿔\s*주세요)",
    r"https?://",
    r"덜\s*위험",                        # rule 7: a tier is not a lower risk
    r"지나도\s*(?:돼|되|괜찮)",
)]
YEARS_IN_NAME = re.compile(r"\(([\d·, ]+)\)\s*$")


@dataclass(frozen=True)
class ZoneLength:
    id: str
    length_m: float


@dataclass(frozen=True)
class ExplainInput:
    status: str
    level: int
    alert: str
    route_duration_s: float
    baseline_duration_s: float
    route_zones: tuple[ZoneLength, ...]
    baseline_zones: tuple[ZoneLength, ...]
    fallback_engine: bool = False  # 대체_계산: no alternative engine exists yet, so always false today
    hard_groups: tuple[str, ...] = ()
    soft_groups: tuple[str, ...] = ()


@dataclass(frozen=True)
class RerouteInput:
    action: str              # "recommend" | "keep" | "no_better"
    previous_rn1_mm: float
    previous_alert: str
    previous_level: int
    rn1_mm: float
    alert: str
    level: int
    current_duration_s: float
    candidate_duration_s: float
    current_zones: tuple[ZoneLength, ...]
    candidate_zones: tuple[ZoneLength, ...]
    hard_groups: tuple[str, ...]  # what the new level avoids
    soft_groups: tuple[str, ...]


@dataclass(frozen=True)
class ExplainResult:
    text: str

    def as_dict(self) -> dict[str, Any]:
        return {"text": self.text}


class UnknownZone(ValueError):
    pass


# ---- facts -------------------------------------------------------------------------------------

def _minutes(seconds: float) -> int:
    return max(1, round(seconds / 60)) if seconds > 0 else 0


def _evidence(zone: RiskZone) -> str:
    match = YEARS_IN_NAME.search(zone.name)
    if zone.type == "flood_history" and not zone.is_dummy and match:
        return f"{match.group(1)}년 침수피해 기록"
    return zone.source_name


def _zone_facts(items: Iterable[ZoneLength], zones: dict[str, RiskZone], labels: dict[str, str],
                tier_labels: dict[str, str] | None = None) -> dict:
    resolved = []
    for item in items:
        zone = zones.get(item.id)
        if zone is None:
            raise UnknownZone(item.id)
        resolved.append((zone, item.length_m))
    resolved.sort(key=lambda pair: (-pair[1], pair[0].id))
    listed = []
    for z, length in resolved[:MAX_ZONES_IN_FACTS]:
        fact = {"종류": labels.get(z.type, z.type), "이름": z.name, "근거": _evidence(z),
                "길이_m": int(round(length))}
        tier = (tier_labels or {}).get(z.group)  # flood_history terrain tier
        if tier:
            fact["회피_등급"] = tier
        listed.append(fact)
    return {"위험_구간_수": len(resolved), "위험_구간": listed}


def _avoid_counts(items: Iterable[ZoneLength], hard: Iterable[str], soft: Iterable[str],
                  zones: dict[str, RiskZone]) -> dict[str, int]:
    items = list(items)
    hard, soft = set(hard), set(soft)
    hard_n = sum(zones[z.id].group in hard for z in items)
    soft_n = sum(zones[z.id].group in soft for z in items)
    return {"반드시_피할_구간_수": hard_n, "되도록_피할_구간_수": soft_n, "그_밖의_구간_수": len(items) - hard_n - soft_n}


def _lower_priority(route: Iterable[ZoneLength], baseline: Iterable[ZoneLength], zones: dict[str, RiskZone]) -> bool:
    """The route swaps zones for ones further down the avoidance order (ZONE_GROUPS runs first to last)."""
    def order(items):
        groups = [zones[z.id].group for z in items]
        return [groups.count(g) for g in ZONE_GROUPS]
    return order(route) < order(baseline)


def _same_route(data: ExplainInput) -> bool:
    return (round(data.route_duration_s, 1) == round(data.baseline_duration_s, 1)
            and {z.id for z in data.route_zones} == {z.id for z in data.baseline_zones})


def compares_routes(data: ExplainInput) -> bool:
    """Whether the sentence compares the chosen route with the plain one. Statuses whose screen
    message is already fixed, and a ranked route equal to the plain one, only restate the result."""
    if data.status in FIXED_STATUSES:
        return False
    if data.status == "ranked" and _same_route(data):
        return False
    return True


def build_facts(data: ExplainInput, zones: dict[str, RiskZone], labels: dict[str, str],
                tier_labels: dict[str, str] | None = None) -> dict[str, Any]:
    """`tier_labels`: avoidance group -> tier label ("flood_history.high" -> "우선 회피")."""
    mode = ALERT_MODES.get(data.alert, f"강수 {data.level}단계")
    facts: dict[str, Any] = {"모드": mode, "강수_단계": data.level}
    route_min, base_min = _minutes(data.route_duration_s), _minutes(data.baseline_duration_s)

    def zone_facts(items):
        return _zone_facts(items, zones, labels, tier_labels)

    if data.status == "avoid_failed" and _same_route(data):
        facts["결과"] = AVOID_FAILED_RESULT
        facts["추천"] = {"경로": "일반 경로", "소요시간_분": base_min, **zone_facts(data.baseline_zones)}
    elif not compares_routes(data):
        facts["결과"] = MESSAGES.get(data.status, MESSAGES["ranked"])
        facts["추천"] = {"경로": "일반 경로", "소요시간_분": route_min, **zone_facts(data.route_zones)}
    else:
        if data.status == "partially_avoided":
            facts["결과"] = PARTIAL_RESULT
        elif data.status == "avoid_failed":
            facts["결과"] = FEWEST_RESULT
        facts["추천"] = {"경로": "회피 경로" if data.status == "avoided" else "추천 경로",
                       "소요시간_분": route_min, **zone_facts(data.route_zones)}
        facts["비교"] = {"경로": "일반 경로", "소요시간_분": base_min, **zone_facts(data.baseline_zones)}
        # From the seconds, not the rounded minutes: the screen shows the same difference.
        facts["추가_소요시간_분"] = round((data.route_duration_s - data.baseline_duration_s) / 60)
        if data.hard_groups or data.soft_groups:
            counts = (data.hard_groups, data.soft_groups, zones)
            facts["추천"].update(_avoid_counts(data.route_zones, *counts))
            facts["비교"].update(_avoid_counts(data.baseline_zones, *counts))
            facts["회피_우선순위가_낮아짐"] = _lower_priority(data.route_zones, data.baseline_zones, zones)
    facts["대체_계산"] = bool(data.fallback_engine)
    return facts


# ---- template ----------------------------------------------------------------------------------

OUT_OF_SCOPE_RESULTS = {MESSAGES["no_avoid_level"], MESSAGES["nothing_on_route"]}


def _compared_sentences(facts: dict[str, Any]) -> str:
    chosen, baseline, extra = facts["추천"], facts["비교"], facts["추가_소요시간_분"]
    phrase = _avoid_phrase(baseline)
    first = f"일반 경로는 {phrase}을 지나요." if phrase else "일반 경로는 이번 단계에서 피할 구간을 지나지 않아요."
    same = all(chosen[k] == baseline[k] for k in ("반드시_피할_구간_수", "되도록_피할_구간_수"))
    if same and phrase and facts["회피_우선순위가_낮아짐"]:
        time_part = _time_part(extra, "고")
        return f"{first} 추천 경로는 {time_part} 수는 같지만 회피 우선순위가 더 낮은 구간으로 지나요."
    return f"{first} {_candidate_sentence('추천 경로', baseline, chosen, extra)}"



def template_text(facts: dict[str, Any]) -> str:
    lead = "기본 후보 경로가 모두 위험 구역을 지나 다른 경로 엔진으로 따로 계산했어요. " if facts["대체_계산"] else ""
    chosen = facts["추천"]
    if "비교" not in facts:
        count = chosen["위험_구간_수"]
        # These statuses mean nothing this level avoids is on the route, so what is left is outside its rules.
        kind = "이번 단계에서 피하지 않는 위험 구간" if facts.get("결과") in OUT_OF_SCOPE_RESULTS else "위험 구간"
        tail = f" 이 경로는 {kind} {count}곳을 지나요." if count else " 이 경로는 알려진 위험 구간을 지나지 않아요."
        if facts.get("결과") == AVOID_FAILED_RESULT:
            return lead + "위험 구간을 모두 피하는 경로를 찾지 못해 일반 경로를 보여드려요." + tail
        return lead + facts["결과"] + tail
    if facts.get("결과") == FEWEST_RESULT:
        lead += "위험 구간을 모두 피하는 경로를 찾지 못해 가장 적게 지나는 경로를 보여드려요. "
    if "반드시_피할_구간_수" in chosen:
        return lead + _compared_sentences(facts)
    base_n, route_n, extra = facts["비교"]["위험_구간_수"], chosen["위험_구간_수"], facts["추가_소요시간_분"]
    first = f"일반 경로는 위험 구간 {base_n}곳을 지나요." if base_n else "일반 경로는 알려진 위험 구간을 지나지 않아요."
    if route_n == 0:
        time_part = (f"{extra}분 더 걸리지만" if extra > 0 else "소요시간은 비슷하지만" if extra == 0
                     else f"{-extra}분 덜 걸리고")
        second = f"추천 경로는 {time_part} 알려진 위험 구간을 지나지 않아요."
    else:
        time_part = (f"{extra}분 더 걸리고" if extra > 0 else "소요시간이 비슷하고" if extra == 0
                     else f"{-extra}분 덜 걸리고")
        second = f"추천 경로는 {time_part} 위험 구간 {route_n}곳을 지나요."
    return f"{lead}{first} {second}"


# ---- reroute during a drive --------------------------------------------------------------------

def _mm(value: float) -> int | float:
    value = round(value, 1)
    return int(value) if value == int(value) else value  # "75", as the check_output number rule reads it


def _reroute_side(duration_s: float, items: Iterable[ZoneLength], data: RerouteInput, zones: dict[str, RiskZone],
                  labels: dict[str, str], tier_labels: dict[str, str] | None) -> dict[str, Any]:
    items = list(items)
    for item in items:
        if item.id not in zones:
            raise UnknownZone(item.id)
    hard, soft = set(data.hard_groups), set(data.soft_groups)
    avoid = [z for z in items if zones[z.id].group in hard | soft]
    return {"소요시간_분": _minutes(duration_s),
            "반드시_피할_구간_수": sum(zones[z.id].group in hard for z in avoid),
            "되도록_피할_구간_수": sum(zones[z.id].group in soft for z in avoid),
            "피할_구간": _zone_facts(avoid, zones, labels, tier_labels)["위험_구간"],
            "그_밖의_구간_수": len(items) - len(avoid)}


def reroute_facts(data: RerouteInput, zones: dict[str, RiskZone], labels: dict[str, str],
                  tier_labels: dict[str, str] | None = None) -> dict[str, Any]:
    current = _reroute_side(data.current_duration_s, data.current_zones, data, zones, labels, tier_labels)
    candidate = _reroute_side(data.candidate_duration_s, data.candidate_zones, data, zones, labels, tier_labels)
    return {"판단": data.action,
            "강수_변화": {"이전_강수_mm": _mm(data.previous_rn1_mm), "현재_강수_mm": _mm(data.rn1_mm),
                      "이전_특보": ALERT_MODES.get(data.previous_alert, "없음"),
                      "현재_특보": ALERT_MODES.get(data.alert, "없음"),
                      "이전_단계": data.previous_level, "현재_단계": data.level},
            "현재_경로": current, "변경_경로": candidate,
            # From the seconds, not the rounded minutes: the screen shows the same difference.
            "추가_소요시간_분": round((data.candidate_duration_s - data.current_duration_s) / 60)}


def _rain_change(change: dict[str, Any]) -> str:
    if change["현재_단계"] <= change["이전_단계"]:  # real rain was re-read and did not stay as high
        return "강수 정보를 다시 확인했어요"
    steps = f"강수 단계가 {change['이전_단계']}단계에서 {change['현재_단계']}단계로 올랐어요"
    if change["현재_특보"] != "없음" and change["현재_특보"] != change["이전_특보"]:
        return f"{change['현재_특보']}가 내려져 {steps}"
    if change["현재_강수_mm"] != change["이전_강수_mm"]:
        return f"시간당 강수량이 {change['이전_강수_mm']}mm에서 {change['현재_강수_mm']}mm로 늘어 {steps}"
    return steps


def _avoid_phrase(side: dict[str, Any]) -> str:
    parts = []
    if side["반드시_피할_구간_수"]:
        parts.append(f"반드시 피할 구간 {side['반드시_피할_구간_수']}곳")
    if side["되도록_피할_구간_수"]:
        parts.append(f"되도록 피할 구간 {side['되도록_피할_구간_수']}곳")
    return "과 ".join(parts)


def _time_part(extra: int, ending: str) -> str:
    """ending "고": "3분 더 걸리고". "지만": "3분 더 걸리지만". A shorter time is never a trade-off."""
    if extra < 0:
        return f"{-extra}분 덜 걸리고"
    if extra > 0:
        return f"{extra}분 더 걸리{ending}"
    return "소요시간이 비슷하고" if ending == "고" else "소요시간은 비슷하지만"


def _candidate_sentence(subject: str, current: dict[str, Any], candidate: dict[str, Any], extra: int) -> str:
    phrase = _avoid_phrase(candidate)
    if phrase:
        time_part = _time_part(extra, "고")
        if current["반드시_피할_구간_수"] and not candidate["반드시_피할_구간_수"]:
            return f"{subject}는 {time_part} 반드시 피할 구간은 지나지 않지만 {phrase}은 지나요."
        return f"{subject}는 {time_part} {phrase}을 지나요."
    time_part = _time_part(extra, "지만")
    if candidate["그_밖의_구간_수"]:
        return (f"{subject}는 {time_part} 이번 단계에 피할 구간을 지나지 않아요. "
                f"그 밖의 위험 구간 {candidate['그_밖의_구간_수']}곳은 지나요.")
    return f"{subject}는 {time_part} 알려진 위험 구간을 지나지 않아요."


def reroute_template(facts: dict[str, Any]) -> str:
    first = _rain_change(facts["강수_변화"])
    current, candidate = facts["현재_경로"], facts["변경_경로"]
    if facts["판단"] == "keep":
        return f"{first}. 남은 경로에는 이번 단계에서 피할 구간이 없어 지금 경로를 유지해요."
    second = f"지금 경로는 남은 길에서 {_avoid_phrase(current)}을 지나요."
    if facts["판단"] == "no_better":
        return f"{first}. {second} 더 나은 경로를 찾지 못해 지금 경로로 안내해요."
    return f"{first}. {second} {_candidate_sentence('변경 경로', current, candidate, facts['추가_소요시간_분'])}"


# ---- entry point -------------------------------------------------------------------------------

def explain(data: ExplainInput, zones: dict[str, RiskZone], labels: dict[str, str],
            tier_labels: dict[str, str] | None = None) -> ExplainResult:
    return ExplainResult(template_text(build_facts(data, zones, labels, tier_labels)))
