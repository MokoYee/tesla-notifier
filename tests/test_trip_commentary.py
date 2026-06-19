"""行程点评画像测试。"""

from tesla_notifier.analytics.trip_commentary import (
    TripCommentaryInput,
    build_trip_commentary,
)


def _make_input(
    drive_id: int = 100,
    distance_km: float = 12.0,
    duration_min: float = 28.0,
    avg_speed_kmh: float = 26.0,
    max_speed_kmh: float | None = 58.0,
    hard_accel_rate: float = 0.0,
    hard_brake_rate: float = 0.0,
    hard_accel_count: int = 0,
    hard_brake_count: int = 0,
    road_context: str = "城市通勤",
    traffic_label: str | None = "整体畅通",
    outside_temp_c: float | None = 20.0,
    confidence: float = 0.85,
    overspeed_ratio: float = 0.0,
    stop_go_density: float = 1.0,
    elevation_gain_m: float = 0.0,
    elevation_loss_m: float = 0.0,
    net_elevation_change_m: float = 0.0,
    terrain_variation_m_per_km: float = 0.0,
) -> TripCommentaryInput:
    """构造默认行程输入，测试时只覆盖关键特征。"""
    return TripCommentaryInput(
        drive_id=drive_id,
        distance_km=distance_km,
        duration_min=duration_min,
        avg_speed_kmh=avg_speed_kmh,
        max_speed_kmh=max_speed_kmh,
        hard_accel_rate=hard_accel_rate,
        hard_brake_rate=hard_brake_rate,
        hard_accel_count=hard_accel_count,
        hard_brake_count=hard_brake_count,
        road_context=road_context,
        traffic_label=traffic_label,
        outside_temp_c=outside_temp_c,
        confidence=confidence,
        overspeed_ratio=overspeed_ratio,
        stop_go_density=stop_go_density,
        elevation_gain_m=elevation_gain_m,
        elevation_loss_m=elevation_loss_m,
        net_elevation_change_m=net_elevation_change_m,
        terrain_variation_m_per_km=terrain_variation_m_per_km,
    )


def test_short_trip_commentary_is_hidden() -> None:
    """短途低置信度行程不展示点评。"""
    commentary = build_trip_commentary(
        _make_input(
            distance_km=1.8,
            duration_min=6.0,
            avg_speed_kmh=18.0,
            max_speed_kmh=42.0,
            confidence=0.12,
            traffic_label=None,
        )
    )

    assert commentary is None


def test_smooth_city_commentary_uses_warm_but_not_roast_tone() -> None:
    """平顺城市通勤应给正反馈，不应硬吐槽。"""
    commentary = build_trip_commentary(_make_input(drive_id=202))

    assert commentary is not None
    assert "通勤节奏" in commentary
    assert "未检测到明显急加速和急减速" in commentary
    assert "上头" not in commentary
    assert "脚下戏" not in commentary


def test_fast_highway_commentary_warns_about_speed_margin() -> None:
    """高速偏快场景应给出速度余量提醒。"""
    commentary = build_trip_commentary(
        _make_input(
            drive_id=303,
            distance_km=86.0,
            duration_min=54.0,
            avg_speed_kmh=95.5,
            max_speed_kmh=134.0,
            hard_accel_rate=3.5,
            hard_brake_rate=2.4,
            hard_accel_count=3,
            hard_brake_count=2,
            road_context="高速巡航",
            confidence=1.0,
            stop_go_density=0.4,
            overspeed_ratio=0.18,
        )
    )

    assert commentary is not None
    assert "高速" in commentary
    assert "峰值 134 km/h" in commentary
    assert "高速段建议多留速度余量" in commentary


def test_congested_hard_brake_commentary_uses_roast_only_when_risky() -> None:
    """拥堵且急刹明显时允许轻吐槽，并给出可执行建议。"""
    commentary = build_trip_commentary(
        _make_input(
            drive_id=404,
            distance_km=14.0,
            duration_min=49.0,
            avg_speed_kmh=17.1,
            max_speed_kmh=54.0,
            hard_accel_rate=4.0,
            hard_brake_rate=15.5,
            hard_accel_count=2,
            hard_brake_count=5,
            traffic_label="明显拥堵",
            stop_go_density=5.1,
            confidence=0.93,
        )
    )

    assert commentary is not None
    assert "脚下戏" in commentary
    assert "路况明显拥堵" in commentary
    assert "少用急刹收尾" in commentary


def test_commentary_is_deterministic_for_same_input() -> None:
    """同一输入应稳定输出同一句文案，避免重复渲染抖动。"""
    trip_input = _make_input(
        drive_id=500,
        distance_km=126.0,
        duration_min=108.0,
        avg_speed_kmh=70.0,
        max_speed_kmh=118.0,
        road_context="综合路况",
        confidence=1.0,
    )

    first = build_trip_commentary(trip_input)
    second = build_trip_commentary(trip_input)

    assert first is not None
    assert second == first


def test_mountainous_commentary_uses_terrain_feature() -> None:
    """起伏明显的山路行程应展示地形原因。"""
    commentary = build_trip_commentary(
        _make_input(
            drive_id=612,
            distance_km=26.0,
            duration_min=42.0,
            avg_speed_kmh=37.0,
            max_speed_kmh=78.0,
            road_context="综合路况",
            confidence=1.0,
            elevation_gain_m=320.0,
            elevation_loss_m=280.0,
            net_elevation_change_m=40.0,
            terrain_variation_m_per_km=23.0,
        )
    )

    assert commentary is not None
    assert "山路起伏" in commentary
    assert "地形起伏 23 m/km" in commentary


def test_uphill_and_downhill_commentary_use_net_elevation_change() -> None:
    """持续爬升和持续下坡应展示净海拔变化。"""
    uphill = build_trip_commentary(
        _make_input(
            drive_id=714,
            distance_km=21.0,
            duration_min=36.0,
            avg_speed_kmh=35.0,
            max_speed_kmh=72.0,
            road_context="综合路况",
            traffic_label="整体畅通",
            stop_go_density=0.8,
            confidence=1.0,
            elevation_gain_m=260.0,
            elevation_loss_m=40.0,
            net_elevation_change_m=220.0,
            terrain_variation_m_per_km=14.5,
        )
    )
    downhill = build_trip_commentary(
        _make_input(
            drive_id=730,
            distance_km=20.0,
            duration_min=32.0,
            avg_speed_kmh=37.0,
            max_speed_kmh=80.0,
            road_context="综合路况",
            traffic_label="整体畅通",
            stop_go_density=0.8,
            confidence=1.0,
            elevation_gain_m=20.0,
            elevation_loss_m=260.0,
            net_elevation_change_m=-220.0,
            terrain_variation_m_per_km=14.0,
        )
    )

    assert uphill is not None
    assert downhill is not None
    assert "净爬升 220 m" in uphill
    assert "净下坡 220 m" in downhill


def test_endurance_and_roadtrip_commentary_include_rest_advice() -> None:
    """连续驾驶和长途行程应给出休息提醒。"""
    endurance = build_trip_commentary(
        _make_input(
            drive_id=811,
            distance_km=148.0,
            duration_min=250.0,
            avg_speed_kmh=38.0,
            max_speed_kmh=88.0,
            road_context="综合路况",
            traffic_label="整体畅通",
            stop_go_density=1.1,
            confidence=1.0,
        )
    )
    roadtrip = build_trip_commentary(
        _make_input(
            drive_id=802,
            distance_km=286.0,
            duration_min=215.0,
            avg_speed_kmh=80.0,
            max_speed_kmh=118.0,
            road_context="高速巡航",
            traffic_label="整体畅通",
            stop_go_density=0.5,
            confidence=1.0,
        )
    )

    assert endurance is not None
    assert roadtrip is not None
    assert "连续驾驶时间不短" in endurance
    assert "这趟长途完成得比较稳" in roadtrip
    assert "长时间驾驶注意中途休息" in endurance
    assert "长时间驾驶注意中途休息" in roadtrip
