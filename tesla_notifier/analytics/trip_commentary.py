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

    distance_km: float
    duration_min: float
    avg_speed_kmh: float
    max_speed_kmh: float | None
    hard_accel_rate: float
    hard_brake_rate: float
    hard_accel_count: int
    hard_brake_count: int
    expected_accel_rate: float
    expected_brake_rate: float
    road_context: str
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
    accel_pressure: float
    brake_pressure: float


@dataclass(frozen=True, slots=True)
class TripProfile:
    """结构化行程画像。"""

    conclusion: str
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
    conclusion = _build_conclusion(commentary_input, risk, scene, tone, expression)
    advice = _build_advice(commentary_input, risk, scene, tone)
    return TripProfile(
        conclusion=conclusion,
        advice=advice,
    )


def _build_risk(commentary_input: TripCommentaryInput) -> TripRisk:
    """按场景基线与样本置信度计算驾驶压力。"""
    max_speed = max(commentary_input.avg_speed_kmh, commentary_input.max_speed_kmh or 0.0)
    accel_pressure = commentary_input.hard_accel_rate / max(
        commentary_input.expected_accel_rate,
        1.0,
    )
    brake_pressure = commentary_input.hard_brake_rate / max(
        commentary_input.expected_brake_rate,
        1.0,
    )

    action_score = 0.0
    action_score += _clamp((brake_pressure - 1.0) / 1.5, 0.0, 1.0) * 0.45
    action_score += _clamp((accel_pressure - 1.0) / 1.5, 0.0, 1.0) * 0.30
    action_score *= 0.65 + 0.35 * commentary_input.confidence

    speed_score = 0.0
    speed_score += _clamp((commentary_input.overspeed_ratio - 0.06) / 0.12, 0.0, 1.0) * 0.18
    speed_score += _clamp((max_speed - 128.0) / 18.0, 0.0, 1.0) * 0.16
    score = action_score + speed_score
    return TripRisk(
        score=score,
        accel_pressure=accel_pressure,
        brake_pressure=brake_pressure,
    )


def _classify_tone(commentary_input: TripCommentaryInput, risk: TripRisk) -> TripTone:
    """判断本次点评应偏表扬、中性还是提醒。"""
    if (
        commentary_input.overspeed_ratio >= 0.12
        or (commentary_input.max_speed_kmh is not None and commentary_input.max_speed_kmh >= 132)
    ):
        return TripTone.CAUTION

    if risk.score >= 0.20:
        return TripTone.CAUTION

    if commentary_input.hard_accel_count == 0 and commentary_input.hard_brake_count == 0:
        return TripTone.POSITIVE

    if (
        risk.accel_pressure <= 0.65
        and risk.brake_pressure <= 0.65
        and commentary_input.overspeed_ratio <= 0.04
    ):
        return TripTone.POSITIVE

    return TripTone.NEUTRAL


def _classify_expression(
    commentary_input: TripCommentaryInput,
    risk: TripRisk,
    tone: TripTone,
) -> TripExpression:
    """按真实特征决定表达强度。"""
    if tone == TripTone.CAUTION:
        if _has_obvious_showy_driving(commentary_input, risk):
            return TripExpression.ROAST
        return TripExpression.DIRECT

    if tone == TripTone.POSITIVE:
        if _deserves_warmth(commentary_input):
            return TripExpression.WARM
        return TripExpression.DIRECT

    return TripExpression.DIRECT


def _has_obvious_showy_driving(
    commentary_input: TripCommentaryInput,
    risk: TripRisk,
) -> bool:
    """是否存在值得轻吐槽的明显激进行为。"""
    obvious_actions = commentary_input.confidence >= 0.65 and (
        (commentary_input.hard_accel_count >= 4 and risk.accel_pressure >= 1.6)
        or (commentary_input.hard_brake_count >= 4 and risk.brake_pressure >= 1.6)
    )
    obvious_speed = commentary_input.overspeed_ratio >= 0.16 or (
        commentary_input.max_speed_kmh is not None and commentary_input.max_speed_kmh >= 136
    )
    return obvious_actions or obvious_speed


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
    risk: TripRisk,
    scene: TripScene,
    tone: TripTone,
    expression: TripExpression,
) -> str:
    """生成一句总判断。"""
    if expression == TripExpression.ROAST:
        return _build_roast_conclusion(commentary_input, risk, scene)

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
        if _is_speed_risk(commentary_input):
            if scene == TripScene.HIGHWAY:
                return "高速巡航偏快，速度余量收一收"
            return "这趟速度偏快，给自己多留点余量"
        if risk.brake_pressure >= risk.accel_pressure:
            return "减速动作偏多，提前松电门会更顺"
        if risk.accel_pressure > 1.0:
            return "加速动作偏多，电门再线性一点"
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
    risk: TripRisk,
    scene: TripScene,
) -> str:
    """生成轻吐槽结论，确保只在风险明显时出现。"""
    if commentary_input.hard_brake_count >= 4 and risk.brake_pressure >= 1.6:
        if scene == TripScene.CITY:
            return "城市路段刹车有点抢戏，预判可以再早一点"
        return "这趟急减速有点抢戏，提前收一收节奏"

    if commentary_input.hard_accel_count >= 4 and risk.accel_pressure >= 1.6:
        return "这趟电门存在感有点强，线性一点会更舒服"

    if commentary_input.overspeed_ratio >= 0.16 or (
        commentary_input.max_speed_kmh is not None and commentary_input.max_speed_kmh >= 136
    ):
        if scene == TripScene.HIGHWAY:
            return "这趟高速有点上头了，速度余量得留出来"
        return "这趟速度欲望有点藏不住，还是要多留余量"

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


def _build_advice(
    commentary_input: TripCommentaryInput,
    risk: TripRisk,
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

    if _is_speed_risk(commentary_input):
        return None
    if risk.brake_pressure >= 1.0 or risk.accel_pressure >= 1.0:
        return None
    if _is_mountainous(commentary_input):
        return "起伏路段提前控速，别等到坡顶坡底再修正"
    if scene == TripScene.CITY and commentary_input.stop_go_density >= 4.0:
        return "拥堵里把跟车距离留出来，节奏会更稳"

    if tone == TripTone.NEUTRAL:
        return "整体没大问题，继续减少多余速度波动"

    return "下一趟把节奏再放平一点"


def _is_speed_risk(commentary_input: TripCommentaryInput) -> bool:
    """是否存在明确的高速风险信号。"""
    return commentary_input.overspeed_ratio >= 0.08 or (
        commentary_input.max_speed_kmh is not None and commentary_input.max_speed_kmh >= 130
    )


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
        return f"{profile.conclusion}；{profile.advice}"
    return profile.conclusion


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
