"""行程点评文案匹配器。"""

import tomllib
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import cast

VALID_COMMENTARY_STYLES = frozenset({"normal", "aggressive"})
SPECIALTY_FEATURES = frozenset(
    {
        "trip_long",
        "trip_endurance",
        "trip_roadtrip",
        "terrain_uphill",
        "terrain_downhill",
        "terrain_rolling",
        "terrain_mountainous",
        "terrain_peak_finish",
        "terrain_valley_finish",
    }
)


def _clamp(value: float, lower: float, upper: float) -> float:
    """限制数值范围。"""
    return max(lower, min(value, upper))


def normalize_commentary_style(style: str | None) -> str:
    """归一化点评风格。"""
    normalized = (style or "normal").strip().lower()
    if normalized in VALID_COMMENTARY_STYLES:
        return normalized
    return "normal"


def _stable_choice_index(commentary_input: "TripCommentaryInput", size: int) -> int:
    """基于行程特征生成稳定索引，避免同类文案总是命中第一条。"""
    if size <= 0:
        return 0

    seed = (
        commentary_input.drive_id * 31
        + round(commentary_input.distance_km * 10)
        + round(commentary_input.duration_min)
        + commentary_input.hard_accel_count * 7
        + commentary_input.hard_brake_count * 11
    )
    return seed % size


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
    style: str = "normal"
    elevation_gain_m: float = 0.0
    elevation_loss_m: float = 0.0
    net_elevation_change_m: float = 0.0
    terrain_variation_m_per_km: float = 0.0


@dataclass(frozen=True, slots=True)
class TripCommentaryTemplate:
    """文案模板及其目标特征。"""

    text: str
    vector: dict[str, float]
    required: frozenset[str] = frozenset()
    forbidden: frozenset[str] = frozenset()
    styles: frozenset[str] = frozenset({"normal", "aggressive"})


@dataclass(frozen=True, slots=True)
class TripCommentaryProfile:
    """行程特征画像。"""

    vector: dict[str, float]
    tags: frozenset[str]


@dataclass(frozen=True, slots=True)
class TripCommentaryLibrary:
    """静态文案库。"""

    templates: tuple[TripCommentaryTemplate, ...]
    fallbacks: dict[str, dict[str, str]]


def _ensure_table(value: object, field_name: str) -> dict[str, object]:
    """确保配置对象是 TOML table。"""
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} 必须是 table")
    return cast(dict[str, object], value)


def _ensure_string(value: object, field_name: str) -> str:
    """确保配置值是字符串。"""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} 必须是字符串")
    return value


def _ensure_string_set(value: object | None, field_name: str) -> frozenset[str]:
    """确保配置值是字符串数组。"""
    if value is None:
        return frozenset()
    if not isinstance(value, list):
        raise ValueError(f"{field_name} 必须是字符串数组")

    values: set[str] = set()
    for index, item in enumerate(value, start=1):
        if not isinstance(item, str):
            raise ValueError(f"{field_name}[{index}] 必须是字符串")
        values.add(item)
    return frozenset(values)


def _ensure_vector(value: object, field_name: str) -> dict[str, float]:
    """确保向量配置是数值映射。"""
    table = _ensure_table(value, field_name)
    vector: dict[str, float] = {}
    for feature_name, feature_value in table.items():
        if not isinstance(feature_value, int | float):
            raise ValueError(f"{field_name}.{feature_name} 必须是数字")
        vector[feature_name] = float(feature_value)
    return vector


@lru_cache(maxsize=1)
def _load_trip_commentary_library() -> TripCommentaryLibrary:
    """从静态文件加载文案模板与兜底文案。"""
    try:
        raw_content = files(__package__).joinpath("trip_commentary_templates.toml").read_text(
            encoding="utf-8"
        )
    except FileNotFoundError as exc:
        raise RuntimeError("缺少行程点评模板文件 trip_commentary_templates.toml") from exc

    try:
        parsed = _ensure_table(tomllib.loads(raw_content), "trip_commentary_templates")
    except tomllib.TOMLDecodeError as exc:
        raise RuntimeError("行程点评模板文件格式错误") from exc

    raw_templates = parsed.get("templates")
    if not isinstance(raw_templates, list):
        raise RuntimeError("trip_commentary_templates.templates 必须是数组")

    templates: list[TripCommentaryTemplate] = []
    for index, raw_template in enumerate(raw_templates, start=1):
        template = _ensure_table(raw_template, f"templates[{index}]")
        styles = _ensure_string_set(template.get("styles"), f"templates[{index}].styles")
        if not styles:
            styles = VALID_COMMENTARY_STYLES
        if not styles.issubset(VALID_COMMENTARY_STYLES):
            raise RuntimeError(f"templates[{index}].styles 包含未支持的风格")

        templates.append(
            TripCommentaryTemplate(
                text=_ensure_string(template.get("text"), f"templates[{index}].text"),
                vector=_ensure_vector(template.get("vector"), f"templates[{index}].vector"),
                required=_ensure_string_set(
                    template.get("required"),
                    f"templates[{index}].required",
                ),
                forbidden=_ensure_string_set(
                    template.get("forbidden"),
                    f"templates[{index}].forbidden",
                ),
                styles=styles,
            )
        )

    raw_fallbacks = _ensure_table(parsed.get("fallbacks"), "fallbacks")
    fallbacks: dict[str, dict[str, str]] = {}
    for style in VALID_COMMENTARY_STYLES:
        raw_style_fallbacks = _ensure_table(raw_fallbacks.get(style), f"fallbacks.{style}")
        style_fallbacks: dict[str, str] = {}
        for key, value in raw_style_fallbacks.items():
            style_fallbacks[key] = _ensure_string(value, f"fallbacks.{style}.{key}")
        if "default" not in style_fallbacks:
            raise RuntimeError(f"fallbacks.{style}.default 不能为空")
        fallbacks[style] = style_fallbacks

    return TripCommentaryLibrary(
        templates=tuple(templates),
        fallbacks=fallbacks,
    )


def _should_hide_commentary(commentary_input: TripCommentaryInput) -> bool:
    """短样本点评直接隐藏，不把内部分析术语暴露给用户。"""
    return (
        commentary_input.distance_km < 3.0
        or commentary_input.duration_min < 8.0
        or commentary_input.confidence < 0.35
    )


def _build_profile(commentary_input: TripCommentaryInput) -> TripCommentaryProfile:
    """把原始行程信号压缩为稀疏特征向量。"""
    max_speed = max(commentary_input.avg_speed_kmh, commentary_input.max_speed_kmh or 0.0)
    combined_rate = max(commentary_input.hard_accel_rate, 0.0) + max(
        commentary_input.hard_brake_rate, 0.0
    )
    road_context = commentary_input.road_context or "综合路况"
    traffic_label = commentary_input.traffic_label or ""

    is_city = road_context == "城市通勤"
    is_highway = road_context == "高速巡航"
    is_mixed = not is_city and not is_highway
    is_long_trip = commentary_input.distance_km >= 50.0 or commentary_input.duration_min >= 70.0
    trip_endurance = _clamp((commentary_input.duration_min - 180.0) / 120.0, 0.0, 1.0)
    trip_roadtrip = _clamp((commentary_input.distance_km - 180.0) / 140.0, 0.0, 1.0)
    is_heavy_congestion = (
        traffic_label in {"明显拥堵", "高压拥堵"} or commentary_input.stop_go_density >= 4.5
    )
    is_congested = is_heavy_congestion or traffic_label == "轻度拥堵" or (
        commentary_input.stop_go_density >= 2.8
    )
    is_clear_traffic = (
        not is_congested
        and traffic_label in {"", "整体畅通"}
        and commentary_input.stop_go_density < 1.4
    )
    is_low_temp = (
        commentary_input.outside_temp_c is not None and commentary_input.outside_temp_c < 5.0
    )
    gain_per_km = commentary_input.elevation_gain_m / max(commentary_input.distance_km, 1.0)
    loss_per_km = commentary_input.elevation_loss_m / max(commentary_input.distance_km, 1.0)
    terrain_uphill = max(
        _clamp((gain_per_km - 8.0) / 8.0, 0.0, 1.0),
        _clamp((commentary_input.net_elevation_change_m - 60.0) / 90.0, 0.0, 1.0),
    )
    if commentary_input.elevation_gain_m < 80.0:
        terrain_uphill = 0.0
    terrain_downhill = max(
        _clamp((loss_per_km - 8.0) / 8.0, 0.0, 1.0),
        _clamp((abs(commentary_input.net_elevation_change_m) - 60.0) / 90.0, 0.0, 1.0),
    )
    if commentary_input.net_elevation_change_m >= -60.0 or commentary_input.elevation_loss_m < 80.0:
        terrain_downhill = 0.0
    terrain_rolling = max(
        _clamp((commentary_input.terrain_variation_m_per_km - 14.0) / 14.0, 0.0, 1.0),
        0.7
        if commentary_input.elevation_gain_m >= 100.0
        and commentary_input.elevation_loss_m >= 100.0
        else 0.0,
    )
    terrain_mountainous = max(
        _clamp((commentary_input.terrain_variation_m_per_km - 20.0) / 16.0, 0.0, 1.0),
        min(
            terrain_rolling,
            _clamp(
                (
                    commentary_input.elevation_gain_m
                    + commentary_input.elevation_loss_m
                    - 320.0
                )
                / 220.0,
                0.0,
                1.0,
            ),
        ),
    )
    if commentary_input.elevation_gain_m < 140.0 or commentary_input.elevation_loss_m < 140.0:
        terrain_mountainous = 0.0
    terrain_peak_finish = max(
        terrain_uphill * 0.7,
        _clamp((commentary_input.net_elevation_change_m - 110.0) / 120.0, 0.0, 1.0),
    )
    if commentary_input.net_elevation_change_m < 90.0 or commentary_input.elevation_gain_m < 120.0:
        terrain_peak_finish = 0.0
    terrain_valley_finish = max(
        terrain_downhill * 0.7,
        _clamp((abs(commentary_input.net_elevation_change_m) - 110.0) / 120.0, 0.0, 1.0),
    )
    if (
        commentary_input.net_elevation_change_m > -90.0
        or commentary_input.elevation_loss_m < 120.0
    ):
        terrain_valley_finish = 0.0

    if is_city:
        calm_pace = _clamp((48.0 - commentary_input.avg_speed_kmh) / 18.0, 0.0, 1.0)
        fast_pace = max(
            _clamp((commentary_input.avg_speed_kmh - 46.0) / 12.0, 0.0, 1.0),
            _clamp((max_speed - 96.0) / 18.0, 0.0, 1.0),
        )
        slow_clear = (
            _clamp((19.0 - commentary_input.avg_speed_kmh) / 5.5, 0.0, 1.0)
            if is_clear_traffic and commentary_input.distance_km >= 5.0
            else 0.0
        )
    elif is_highway:
        calm_pace = _clamp((102.0 - commentary_input.avg_speed_kmh) / 25.0, 0.0, 1.0)
        fast_pace = max(
            _clamp((commentary_input.avg_speed_kmh - 108.0) / 15.0, 0.0, 1.0),
            _clamp((max_speed - 120.0) / 12.0, 0.0, 1.0),
        )
        slow_clear = (
            _clamp((78.0 - commentary_input.avg_speed_kmh) / 16.0, 0.0, 1.0)
            if is_clear_traffic and commentary_input.distance_km >= 10.0
            else 0.0
        )
    else:
        calm_pace = _clamp((62.0 - commentary_input.avg_speed_kmh) / 18.0, 0.0, 1.0)
        fast_pace = max(
            _clamp((commentary_input.avg_speed_kmh - 78.0) / 15.0, 0.0, 1.0),
            _clamp((max_speed - 118.0) / 14.0, 0.0, 1.0),
        )
        slow_clear = (
            _clamp((32.0 - commentary_input.avg_speed_kmh) / 9.0, 0.0, 1.0)
            if is_clear_traffic and commentary_input.distance_km >= 6.0
            else 0.0
        )

    smooth_score = _clamp((5.0 - combined_rate) / 5.0, 0.0, 1.0)
    if commentary_input.hard_accel_count == 0 and commentary_input.hard_brake_count == 0:
        smooth_score = max(smooth_score, 1.0)

    stable_score = _clamp((10.0 - combined_rate) / 8.0, 0.0, 1.0)
    brake_ratio = commentary_input.hard_brake_count / max(commentary_input.hard_accel_count, 1)
    brake_heavy = max(
        _clamp((commentary_input.hard_brake_rate - 10.0) / 5.0, 0.0, 1.0),
        1.0 if commentary_input.hard_brake_count >= 2 and brake_ratio >= 1.8 else 0.0,
        0.75 if commentary_input.hard_brake_count >= 3 and brake_ratio >= 1.5 else 0.0,
    )
    accel_heavy = max(
        _clamp((commentary_input.hard_accel_rate - 10.0) / 5.0, 0.0, 1.0),
        0.8
        if commentary_input.hard_accel_count >= 3
        and commentary_input.hard_accel_count >= commentary_input.hard_brake_count + 2
        else 0.0,
    )
    speed_high = max(
        _clamp((commentary_input.overspeed_ratio - 0.08) / 0.10, 0.0, 1.0),
        _clamp((max_speed - 122.0) / 10.0, 0.0, 1.0),
    )
    aggressive_score = max(
        _clamp((combined_rate - 10.0) / 8.0, 0.0, 1.0),
        brake_heavy * 0.85,
        accel_heavy * 0.85,
    )

    stable_in_pressure = 0.0
    if is_congested:
        stable_in_pressure = _clamp((stable_score + (1.0 - aggressive_score)) / 2.0, 0.0, 1.0)

    positive_tone = max(
        smooth_score * 0.62 + stable_score * 0.38,
        stable_in_pressure * 0.75 if is_congested else 0.0,
    )
    caution_tone = max(
        brake_heavy,
        accel_heavy,
        aggressive_score,
        speed_high,
        fast_pace * 0.9,
        slow_clear * 0.7,
        terrain_rolling * 0.45,
    )

    vector = {
        "scene_city": 1.0 if is_city else 0.0,
        "scene_highway": 1.0 if is_highway else 0.0,
        "scene_mixed": 1.0 if is_mixed else 0.0,
        "traffic_congested": 1.0 if is_congested else 0.0,
        "traffic_clear": 1.0 if is_clear_traffic else 0.0,
        "trip_long": 1.0 if is_long_trip else 0.0,
        "trip_endurance": trip_endurance,
        "trip_roadtrip": trip_roadtrip,
        "temp_low": 1.0 if is_low_temp else 0.0,
        "terrain_uphill": terrain_uphill,
        "terrain_downhill": terrain_downhill,
        "terrain_rolling": terrain_rolling,
        "terrain_mountainous": terrain_mountainous,
        "terrain_peak_finish": terrain_peak_finish,
        "terrain_valley_finish": terrain_valley_finish,
        "pace_calm": calm_pace,
        "pace_fast": fast_pace,
        "pace_slow_clear": slow_clear,
        "control_smooth": smooth_score,
        "control_stable": stable_score,
        "control_aggressive": aggressive_score,
        "risk_brake_heavy": brake_heavy,
        "risk_accel_heavy": accel_heavy,
        "risk_speed_high": speed_high,
        "stable_in_pressure": stable_in_pressure,
        "positive_tone": _clamp(positive_tone, 0.0, 1.0),
        "caution_tone": _clamp(caution_tone, 0.0, 1.0),
    }

    tags = {
        "scene_city" if is_city else "scene_mixed" if is_mixed else "scene_highway",
    }
    if is_congested:
        tags.add("traffic_congested")
    if is_clear_traffic:
        tags.add("traffic_clear")
    if is_long_trip:
        tags.add("trip_long")
    if vector["trip_endurance"] >= 0.45:
        tags.add("trip_endurance")
    if vector["trip_roadtrip"] >= 0.45:
        tags.add("trip_roadtrip")
    if is_low_temp:
        tags.add("temp_low")
    if vector["terrain_uphill"] >= 0.45:
        tags.add("terrain_uphill")
    if vector["terrain_downhill"] >= 0.45:
        tags.add("terrain_downhill")
    if vector["terrain_rolling"] >= 0.45:
        tags.add("terrain_rolling")
    if vector["terrain_mountainous"] >= 0.45:
        tags.add("terrain_mountainous")
    if vector["terrain_peak_finish"] >= 0.45:
        tags.add("terrain_peak_finish")
    if vector["terrain_valley_finish"] >= 0.45:
        tags.add("terrain_valley_finish")
    if vector["control_smooth"] >= 0.62:
        tags.add("control_smooth")
    if vector["control_stable"] >= 0.5:
        tags.add("control_stable")
    if vector["control_aggressive"] >= 0.42:
        tags.add("control_aggressive")
    if vector["risk_brake_heavy"] >= 0.5:
        tags.add("risk_brake_heavy")
    if vector["risk_accel_heavy"] >= 0.5:
        tags.add("risk_accel_heavy")
    if vector["risk_speed_high"] >= 0.45:
        tags.add("risk_speed_high")
    if vector["pace_calm"] >= 0.55:
        tags.add("pace_calm")
    if vector["pace_fast"] >= 0.5:
        tags.add("pace_fast")
    if vector["pace_slow_clear"] >= 0.5:
        tags.add("pace_slow_clear")
    if vector["stable_in_pressure"] >= 0.55:
        tags.add("stable_in_pressure")
    if vector["positive_tone"] >= 0.42 or (
        vector["caution_tone"] < 0.35 and vector["control_stable"] >= 0.35
    ):
        tags.add("positive_tone")
    if vector["caution_tone"] >= 0.38:
        tags.add("caution_tone")

    return TripCommentaryProfile(vector=vector, tags=frozenset(tags))


def _score_template(
    template: TripCommentaryTemplate,
    profile: TripCommentaryProfile,
    style: str,
) -> float | None:
    """计算模板与当前行程的匹配分数。"""
    if style not in template.styles:
        return None
    if not template.required.issubset(profile.tags):
        return None
    if template.forbidden.intersection(profile.tags):
        return None

    total_weight = sum(template.vector.values())
    if total_weight <= 0:
        return 0.0

    weighted_match = 0.0
    hit_count = 0
    for feature_name, feature_weight in template.vector.items():
        feature_value = profile.vector.get(feature_name, 0.0)
        weighted_match += feature_value * feature_weight
        if feature_value >= 0.45:
            hit_count += 1

    coverage_bonus = (hit_count / len(template.vector)) * 0.08
    required_bonus = len(template.required) * 0.03
    specialty_bonus = sum(
        0.18
        for feature_name in SPECIALTY_FEATURES
        if feature_name in template.vector and profile.vector.get(feature_name, 0.0) >= 0.45
    )
    style_bonus = 0.0
    if style == "aggressive" and template.styles == frozenset({"aggressive"}):
        style_bonus = 0.08
    if style == "normal" and template.styles == frozenset({"normal"}):
        style_bonus = 0.04
    return (
        (weighted_match / total_weight)
        + coverage_bonus
        + required_bonus
        + specialty_bonus
        + style_bonus
    )


def _fallback_copy(fallbacks: dict[str, dict[str, str]], style: str, key: str) -> str:
    """读取指定风格的兜底文案。"""
    style_fallbacks = fallbacks.get(style)
    if style_fallbacks is None:
        raise RuntimeError(f"缺少行程点评兜底文案: {style}")
    return style_fallbacks.get(key, style_fallbacks["default"])


def _fallback_text(
    profile: TripCommentaryProfile,
    style: str,
    fallbacks: dict[str, dict[str, str]],
) -> str:
    """兜底文案，确保缺省场景也有输出。"""
    if style == "aggressive":
        if "trip_endurance" in profile.tags and "caution_tone" in profile.tags:
            return _fallback_copy(fallbacks, style, "trip_endurance_caution")
        if "trip_endurance" in profile.tags:
            return _fallback_copy(fallbacks, style, "trip_endurance_positive")
        if "trip_roadtrip" in profile.tags and "caution_tone" in profile.tags:
            return _fallback_copy(fallbacks, style, "trip_roadtrip_caution")
        if "trip_roadtrip" in profile.tags:
            return _fallback_copy(fallbacks, style, "trip_roadtrip_positive")
        if "terrain_mountainous" in profile.tags and "caution_tone" in profile.tags:
            return _fallback_copy(fallbacks, style, "terrain_mountainous_caution")
        if "terrain_mountainous" in profile.tags:
            return _fallback_copy(fallbacks, style, "terrain_mountainous_positive")
        if "terrain_peak_finish" in profile.tags and "caution_tone" in profile.tags:
            return _fallback_copy(fallbacks, style, "terrain_peak_finish_caution")
        if "terrain_peak_finish" in profile.tags:
            return _fallback_copy(fallbacks, style, "terrain_peak_finish_positive")
        if "terrain_valley_finish" in profile.tags and "caution_tone" in profile.tags:
            return _fallback_copy(fallbacks, style, "terrain_valley_finish_caution")
        if "terrain_valley_finish" in profile.tags:
            return _fallback_copy(fallbacks, style, "terrain_valley_finish_positive")
        if "terrain_rolling" in profile.tags and "caution_tone" in profile.tags:
            return _fallback_copy(fallbacks, style, "terrain_rolling_caution")
        if "terrain_rolling" in profile.tags:
            return _fallback_copy(fallbacks, style, "terrain_rolling_positive")
        if "traffic_clear" in profile.tags and "pace_slow_clear" in profile.tags:
            return _fallback_copy(fallbacks, style, "traffic_clear_slow")
        if "traffic_congested" in profile.tags and "caution_tone" in profile.tags:
            return _fallback_copy(fallbacks, style, "traffic_congested_caution")
        if "scene_highway" in profile.tags and "caution_tone" in profile.tags:
            return _fallback_copy(fallbacks, style, "highway_caution")
        if "caution_tone" in profile.tags:
            return _fallback_copy(fallbacks, style, "caution_tone")
        return _fallback_copy(fallbacks, style, "default")

    if "trip_endurance" in profile.tags and "caution_tone" in profile.tags:
        return _fallback_copy(fallbacks, style, "trip_endurance_caution")
    if "trip_endurance" in profile.tags:
        return _fallback_copy(fallbacks, style, "trip_endurance_positive")
    if "trip_roadtrip" in profile.tags and "caution_tone" in profile.tags:
        return _fallback_copy(fallbacks, style, "trip_roadtrip_caution")
    if "trip_roadtrip" in profile.tags:
        return _fallback_copy(fallbacks, style, "trip_roadtrip_positive")
    if "terrain_mountainous" in profile.tags and "caution_tone" in profile.tags:
        return _fallback_copy(fallbacks, style, "terrain_mountainous_caution")
    if "terrain_mountainous" in profile.tags:
        return _fallback_copy(fallbacks, style, "terrain_mountainous_positive")
    if "terrain_peak_finish" in profile.tags and "caution_tone" in profile.tags:
        return _fallback_copy(fallbacks, style, "terrain_peak_finish_caution")
    if "terrain_peak_finish" in profile.tags:
        return _fallback_copy(fallbacks, style, "terrain_peak_finish_positive")
    if "terrain_valley_finish" in profile.tags and "caution_tone" in profile.tags:
        return _fallback_copy(fallbacks, style, "terrain_valley_finish_caution")
    if "terrain_valley_finish" in profile.tags:
        return _fallback_copy(fallbacks, style, "terrain_valley_finish_positive")
    if "terrain_rolling" in profile.tags and "caution_tone" in profile.tags:
        return _fallback_copy(fallbacks, style, "terrain_rolling_caution")
    if "terrain_rolling" in profile.tags:
        return _fallback_copy(fallbacks, style, "terrain_rolling_positive")
    if "traffic_clear" in profile.tags and "pace_slow_clear" in profile.tags:
        return _fallback_copy(fallbacks, style, "traffic_clear_slow")
    if "traffic_congested" in profile.tags and "caution_tone" in profile.tags:
        return _fallback_copy(fallbacks, style, "traffic_congested_caution")
    if "scene_highway" in profile.tags and "caution_tone" in profile.tags:
        return _fallback_copy(fallbacks, style, "highway_caution")
    if "risk_brake_heavy" in profile.tags:
        return _fallback_copy(fallbacks, style, "risk_brake_heavy")
    if "caution_tone" in profile.tags:
        return _fallback_copy(fallbacks, style, "caution_tone")
    return _fallback_copy(fallbacks, style, "default")


def _dominant_specialties(profile: TripCommentaryProfile) -> frozenset[str]:
    """挑出当前行程最强的一组专项特征，避免强地形被通用模板稀释。"""
    specialty_scores = {
        feature_name: profile.vector.get(feature_name, 0.0)
        for feature_name in SPECIALTY_FEATURES
    }
    if specialty_scores["trip_endurance"] >= 0.45 or specialty_scores["trip_roadtrip"] >= 0.45:
        specialty_scores["trip_long"] = 0.0
    if specialty_scores["terrain_peak_finish"] >= 0.45:
        specialty_scores["terrain_uphill"] = 0.0
    if specialty_scores["terrain_valley_finish"] >= 0.45:
        specialty_scores["terrain_downhill"] = 0.0
    if specialty_scores["terrain_mountainous"] >= 0.45:
        specialty_scores["terrain_rolling"] = 0.0

    dominant_value = max(specialty_scores.values(), default=0.0)
    if dominant_value < 0.45:
        return frozenset()

    return frozenset(
        feature_name
        for feature_name, value in specialty_scores.items()
        if value >= 0.45 and (dominant_value - value) <= 0.1
    )


def build_trip_commentary(commentary_input: TripCommentaryInput) -> str | None:
    """生成面向车主的短句点评。"""
    if _should_hide_commentary(commentary_input):
        return None

    library = _load_trip_commentary_library()
    style = normalize_commentary_style(commentary_input.style)
    profile = _build_profile(commentary_input)
    dominant_specialties = _dominant_specialties(profile)
    scored_templates: list[tuple[float, TripCommentaryTemplate]] = []

    for template in library.templates:
        score = _score_template(template, profile, style)
        if score is None:
            continue
        scored_templates.append((score, template))

    if dominant_specialties:
        specialty_templates = [
            (score, template)
            for score, template in scored_templates
            if dominant_specialties.intersection(template.vector)
        ]
        if specialty_templates:
            scored_templates = specialty_templates

    if not scored_templates:
        return _fallback_text(profile, style, library.fallbacks)

    ranked_templates = sorted(
        scored_templates,
        key=lambda item: (-item[0], item[1].text),
    )
    best_score = ranked_templates[0][0]
    best_template = ranked_templates[0][1]
    best_specialties = SPECIALTY_FEATURES.intersection(best_template.vector)
    shortlist_threshold = 0.08
    if best_specialties:
        shortlist_threshold = 0.02
    shortlist = [
        template
        for score, template in ranked_templates
        if (best_score - score) <= shortlist_threshold
    ]
    if best_specialties:
        shortlist = [
            template
            for template in shortlist
            if dominant_specialties.intersection(template.vector)
            or best_specialties.intersection(template.vector)
        ] or [best_template]

    selected_index = _stable_choice_index(commentary_input, len(shortlist))
    return shortlist[selected_index].text


__all__ = [
    "TripCommentaryInput",
    "VALID_COMMENTARY_STYLES",
    "build_trip_commentary",
    "normalize_commentary_style",
]
