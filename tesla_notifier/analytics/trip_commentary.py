"""行程点评生成器。"""

from dataclasses import dataclass
from enum import StrEnum


class TripScene(StrEnum):
    """行程主要场景。"""

    CITY = "城市通勤"
    HIGHWAY = "高速巡航"
    MIXED = "综合路况"


class TripTone(StrEnum):
    """点评语气。"""

    POSITIVE = "positive"
    NEUTRAL = "neutral"
    CAUTION = "caution"


class TripExpression(StrEnum):
    """点评表达强度。"""

    DIRECT = "direct"
    WARM = "warm"
    ROAST = "roast"


@dataclass(frozen=True, slots=True)
class TripCommentaryInput:
    """行程点评输入。"""

    drive_id: int
    distance_km: float
    duration_min: float
    avg_speed_kmh: float
    max_speed_kmh: float | None
    hard_accel_rate: float
    hard_brake_rate: float
    hard_accel_count: int
    hard_brake_count: int
    road_context: str
    traffic_label: str | None
    outside_temp_c: float | None
    confidence: float
    overspeed_ratio: float
    stop_go_density: float
    elevation_gain_m: float = 0.0
    elevation_loss_m: float = 0.0
    net_elevation_change_m: float = 0.0
    terrain_variation_m_per_km: float = 0.0


@dataclass(frozen=True, slots=True)
class TripRisk:
    """行程风险画像。"""

    score: float
    combined_rate: float
    max_speed_kmh: float


@dataclass(frozen=True, slots=True)
class TripProfile:
    """结构化行程画像。"""

    scene: TripScene
    tone: TripTone
    conclusion: str
    reason: str
    advice: str | None


def _clamp(value: float, lower: float, upper: float) -> float:
    """限制数值范围。"""
    return max(lower, min(value, upper))


def _should_hide_commentary(commentary_input: TripCommentaryInput) -> bool:
    """短样本点评直接隐藏，避免弱信号硬凑结论。"""
    return (
        commentary_input.distance_km < 3.0
        or commentary_input.duration_min < 8.0
        or commentary_input.confidence < 0.35
    )


def _scene_from_context(road_context: str) -> TripScene:
    """从已有路况上下文转换为内部枚举。"""
    if road_context == TripScene.CITY.value:
        return TripScene.CITY
    if road_context == TripScene.HIGHWAY.value:
        return TripScene.HIGHWAY
    return TripScene.MIXED


def _build_profile(commentary_input: TripCommentaryInput) -> TripProfile:
    """把原始行程信号压缩为可解释画像。"""
    scene = _scene_from_context(commentary_input.road_context)
    risk = _build_risk(commentary_input)
    tone = _classify_tone(commentary_input, risk)
    expression = _classify_expression(commentary_input, risk, tone)
    conclusion = _build_conclusion(commentary_input, scene, tone, expression)
    reason = _build_reason(commentary_input, scene, tone)
    advice = _build_advice(commentary_input, scene, tone)
    return TripProfile(
        scene=scene,
        tone=tone,
        conclusion=conclusion,
        reason=reason,
        advice=advice,
    )


def _build_risk(commentary_input: TripCommentaryInput) -> TripRisk:
    """把多维驾驶信号压成稳定风险分。"""
    combined_rate = commentary_input.hard_accel_rate + commentary_input.hard_brake_rate
    max_speed = max(commentary_input.avg_speed_kmh, commentary_input.max_speed_kmh or 0.0)

    score = 0.0
    score += _clamp((commentary_input.hard_brake_rate - 8.0) / 8.0, 0.0, 1.0) * 0.42
    score += _clamp((commentary_input.hard_accel_rate - 8.0) / 8.0, 0.0, 1.0) * 0.30
    score += _clamp((commentary_input.overspeed_ratio - 0.06) / 0.12, 0.0, 1.0) * 0.18
    score += _clamp((max_speed - 128.0) / 18.0, 0.0, 1.0) * 0.16
    score += _clamp((combined_rate - 14.0) / 10.0, 0.0, 1.0) * 0.20
    return TripRisk(score=score, combined_rate=combined_rate, max_speed_kmh=max_speed)


def _classify_tone(commentary_input: TripCommentaryInput, risk: TripRisk) -> TripTone:
    """判断本次点评应偏表扬、中性还是提醒。"""
    if (
        commentary_input.overspeed_ratio >= 0.12
        or (commentary_input.max_speed_kmh is not None and commentary_input.max_speed_kmh >= 132)
    ):
        return TripTone.CAUTION

    if risk.score >= 0.42:
        return TripTone.CAUTION

    if risk.combined_rate <= 5.0 and commentary_input.overspeed_ratio <= 0.04:
        return TripTone.POSITIVE

    if commentary_input.hard_accel_count == 0 and commentary_input.hard_brake_count == 0:
        return TripTone.POSITIVE

    return TripTone.NEUTRAL


def _classify_expression(
    commentary_input: TripCommentaryInput,
    risk: TripRisk,
    tone: TripTone,
) -> TripExpression:
    """按真实特征决定表达强度。"""
    if tone == TripTone.CAUTION:
        if risk.score >= 0.70 or _has_obvious_showy_driving(commentary_input):
            return TripExpression.ROAST
        return TripExpression.DIRECT

    if tone == TripTone.POSITIVE:
        if _deserves_warmth(commentary_input):
            return TripExpression.WARM
        return TripExpression.DIRECT

    return TripExpression.DIRECT


def _has_obvious_showy_driving(commentary_input: TripCommentaryInput) -> bool:
    """是否存在值得轻吐槽的明显激进行为。"""
    return (
        commentary_input.hard_accel_rate >= 12.0
        or commentary_input.hard_brake_rate >= 14.0
        or commentary_input.overspeed_ratio >= 0.16
        or (commentary_input.max_speed_kmh is not None and commentary_input.max_speed_kmh >= 136)
    )


def _deserves_warmth(commentary_input: TripCommentaryInput) -> bool:
    """是否值得给一点情绪价值。"""
    return (
        _is_roadtrip(commentary_input)
        or _is_endurance_trip(commentary_input)
        or _is_mountainous(commentary_input)
        or (
            commentary_input.hard_accel_count == 0
            and commentary_input.hard_brake_count == 0
            and commentary_input.distance_km >= 10.0
        )
    )


def _build_conclusion(
    commentary_input: TripCommentaryInput,
    scene: TripScene,
    tone: TripTone,
    expression: TripExpression,
) -> str:
    """生成一句总判断。"""
    if expression == TripExpression.ROAST:
        return _build_roast_conclusion(commentary_input, scene)

    if _is_roadtrip(commentary_input):
        if tone == TripTone.CAUTION:
            return "长途后段节奏有点散"
        return "这趟长途完成得比较稳"

    if _is_endurance_trip(commentary_input):
        if tone == TripTone.CAUTION:
            return "连续驾驶时间偏长，后段需要更收敛"
        return "连续驾驶时间不短，整体节奏还稳"

    if _is_mountainous(commentary_input):
        if tone == TripTone.CAUTION:
            return "山路起伏放大了驾驶动作"
        return "山路起伏不少，整体控制住了"

    if tone == TripTone.CAUTION:
        if scene == TripScene.HIGHWAY:
            return "高速巡航节奏偏激进"
        if scene == TripScene.CITY:
            return "城市路段动作有点密"
        return "这趟速度和动作起伏偏大"

    if tone == TripTone.POSITIVE:
        return _build_positive_conclusion(scene, expression)

    if scene == TripScene.HIGHWAY:
        return "高速为主，整体节奏正常"
    if scene == TripScene.CITY:
        return "城市通勤为主，节奏正常"
    return "这趟整体表现正常"


def _build_roast_conclusion(
    commentary_input: TripCommentaryInput,
    scene: TripScene,
) -> str:
    """生成轻吐槽结论，确保只在风险明显时出现。"""
    if commentary_input.hard_brake_rate >= 14.0:
        if scene == TripScene.CITY:
            return "这趟城市路段脚下戏有点多"
        return "这趟急减速有点抢戏"

    if commentary_input.hard_accel_rate >= 12.0:
        return "这趟电门存在感有点强"

    if commentary_input.overspeed_ratio >= 0.16 or (
        commentary_input.max_speed_kmh is not None and commentary_input.max_speed_kmh >= 136
    ):
        if scene == TripScene.HIGHWAY:
            return "这趟高速有点上头了"
        return "这趟速度欲望有点藏不住"

    return "这趟节奏有点抢戏"


def _build_positive_conclusion(scene: TripScene, expression: TripExpression) -> str:
    """生成正向结论。"""
    if expression != TripExpression.WARM:
        if scene == TripScene.HIGHWAY:
            return "高速巡航比较稳"
        if scene == TripScene.CITY:
            return "通勤节奏比较顺"
        return "这趟开得比较干净"

    if scene == TripScene.HIGHWAY:
        return "这趟高速巡航挺稳，手感在线"
    if scene == TripScene.CITY:
        return "这趟通勤节奏很顺，动作干净"
    return "这趟开得挺清爽，整体在线"


def _build_reason(
    commentary_input: TripCommentaryInput,
    scene: TripScene,
    tone: TripTone,
) -> str:
    """生成主因解释。"""
    reasons: list[str] = []

    if scene == TripScene.HIGHWAY:
        reasons.append(f"均速 {commentary_input.avg_speed_kmh:.0f} km/h")
        if commentary_input.max_speed_kmh is not None:
            reasons.append(f"峰值 {commentary_input.max_speed_kmh:.0f} km/h")
    elif scene == TripScene.CITY:
        if commentary_input.stop_go_density >= 3.0:
            reasons.append(f"停走密度 {commentary_input.stop_go_density:.1f} 次/10km")
        else:
            reasons.append(f"均速 {commentary_input.avg_speed_kmh:.0f} km/h")
    else:
        reasons.append(f"均速 {commentary_input.avg_speed_kmh:.0f} km/h")

    if commentary_input.hard_accel_count or commentary_input.hard_brake_count:
        reasons.append(
            f"急加速 {commentary_input.hard_accel_count} 次、急减速 "
            f"{commentary_input.hard_brake_count} 次"
        )
    elif tone == TripTone.POSITIVE:
        reasons.append("未检测到明显急加速和急减速")

    terrain_reason = _terrain_reason(commentary_input)
    if terrain_reason:
        reasons.append(terrain_reason)

    traffic_reason = _traffic_reason(commentary_input)
    if traffic_reason and len(reasons) < 3:
        reasons.append(traffic_reason)

    return "，".join(reasons[:3])


def _build_advice(
    commentary_input: TripCommentaryInput,
    scene: TripScene,
    tone: TripTone,
) -> str | None:
    """根据画像给出一句可执行建议。"""
    if tone == TripTone.POSITIVE:
        if commentary_input.outside_temp_c is not None and commentary_input.outside_temp_c < 5:
            return "低温下继续保持这种柔和节奏就可以"
        if _is_roadtrip(commentary_input) or _is_endurance_trip(commentary_input):
            return "长时间驾驶注意中途休息"
        return None

    if commentary_input.hard_brake_rate >= 9.0:
        return "下一趟可以把预判再提前一点，少用急刹收尾"
    if commentary_input.hard_accel_rate >= 9.0:
        return "电门开度再线性一点，乘坐和能耗都会更好"
    if commentary_input.overspeed_ratio >= 0.08 or (
        commentary_input.max_speed_kmh is not None and commentary_input.max_speed_kmh >= 130
    ):
        return "高速段建议多留速度余量"
    if _is_mountainous(commentary_input):
        return "起伏路段提前控速，别等到坡顶坡底再修正"
    if scene == TripScene.CITY and commentary_input.stop_go_density >= 4.0:
        return "拥堵里把跟车距离留出来，节奏会更稳"

    if tone == TripTone.NEUTRAL:
        return "整体没大问题，继续减少多余速度波动"

    return "下一趟把节奏再放平一点"


def _terrain_reason(commentary_input: TripCommentaryInput) -> str | None:
    """生成地形原因。"""
    if commentary_input.net_elevation_change_m >= 90:
        return f"净爬升 {commentary_input.net_elevation_change_m:.0f} m"
    if commentary_input.net_elevation_change_m <= -90:
        return f"净下坡 {abs(commentary_input.net_elevation_change_m):.0f} m"
    if commentary_input.terrain_variation_m_per_km >= 18:
        return f"地形起伏 {commentary_input.terrain_variation_m_per_km:.0f} m/km"
    return None


def _traffic_reason(commentary_input: TripCommentaryInput) -> str | None:
    """生成外部路况原因。"""
    traffic_label = commentary_input.traffic_label
    if traffic_label in {"明显拥堵", "高压拥堵"}:
        return f"路况{traffic_label}"
    if traffic_label == "轻度拥堵":
        return "有轻度拥堵"
    return None


def _is_endurance_trip(commentary_input: TripCommentaryInput) -> bool:
    """是否属于连续驾驶较久。"""
    return commentary_input.duration_min >= 180


def _is_roadtrip(commentary_input: TripCommentaryInput) -> bool:
    """是否属于长途行程。"""
    return commentary_input.distance_km >= 180


def _is_mountainous(commentary_input: TripCommentaryInput) -> bool:
    """是否属于明显起伏行程。"""
    return (
        commentary_input.terrain_variation_m_per_km >= 20
        or commentary_input.elevation_gain_m >= 250
        or commentary_input.elevation_loss_m >= 250
        or abs(commentary_input.net_elevation_change_m) >= 180
    )


def _compose_commentary(profile: TripProfile) -> str:
    """组合最终点评句子。"""
    if profile.advice:
        return f"{profile.conclusion}，{profile.reason}；{profile.advice}"
    return f"{profile.conclusion}，{profile.reason}"


def build_trip_commentary(commentary_input: TripCommentaryInput) -> str | None:
    """生成面向车主的行程点评。"""
    if _should_hide_commentary(commentary_input):
        return None

    profile = _build_profile(commentary_input)
    return _compose_commentary(profile)


__all__ = [
    "TripCommentaryInput",
    "build_trip_commentary",
]
