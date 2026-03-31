"""行程点评匹配测试。"""

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
    style: str = "normal",
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
        style=style,
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


def test_normal_city_commentary_stays_direct() -> None:
    """常规风格下应输出直接、克制的点评。"""
    commentary = build_trip_commentary(_make_input(drive_id=202))

    assert commentary == "动作利落，通勤状态在线"


def test_aggressive_highway_fast_commentary_uses_playful_tone() -> None:
    """激进风格下，高速偏快场景应命中更年轻化的文案。"""
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
            style="aggressive",
        )
    )

    assert commentary == "秋名山车神附身，后段收一收"


def test_aggressive_slow_clear_commentary_can_roast_driver() -> None:
    """路况顺但开得过慢时，激进风格允许更直接的吐槽。"""
    commentary = build_trip_commentary(
        _make_input(
            drive_id=408,
            distance_km=18.0,
            duration_min=54.0,
            avg_speed_kmh=20.0,
            max_speed_kmh=48.0,
            road_context="综合路况",
            traffic_label="整体畅通",
            stop_go_density=0.6,
            confidence=1.0,
            style="aggressive",
        )
    )

    assert commentary == "路况都顺了，你是行走的路障吗"


def test_normal_congested_commentary_stays_cautious() -> None:
    """拥堵且动作偏急时，常规风格应优先给出收敛提醒。"""
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

    assert commentary == "堵车已经够忙了，脚下再柔一点"


def test_long_trip_commentary_is_deterministic() -> None:
    """同一输入应稳定命中同一句文案，避免重复渲染抖动。"""
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

    assert first == "里程拉长了，动作还是稳的"
    assert second == first


def test_aggressive_hilly_commentary_uses_terrain_feature() -> None:
    """起伏明显的山路行程应命中地形文案，而不是退回普通模板。"""
    commentary = build_trip_commentary(
        _make_input(
            drive_id=612,
            distance_km=26.0,
            duration_min=42.0,
            avg_speed_kmh=37.0,
            max_speed_kmh=78.0,
            road_context="综合路况",
            confidence=1.0,
            style="aggressive",
            elevation_gain_m=320.0,
            elevation_loss_m=280.0,
            net_elevation_change_m=40.0,
            terrain_variation_m_per_km=23.0,
        )
    )

    assert commentary == "高低差都拉满了，你居然还稳住了"


def test_aggressive_uphill_finish_commentary_uses_net_climb_feature() -> None:
    """持续爬升且海拔明显抬升时，应命中爬升收官类文案。"""
    commentary = build_trip_commentary(
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
            style="aggressive",
            elevation_gain_m=260.0,
            elevation_loss_m=40.0,
            net_elevation_change_m=220.0,
            terrain_variation_m_per_km=14.5,
        )
    )

    assert commentary == "海拔一路上扬，你还真没上头"


def test_aggressive_downhill_finish_commentary_uses_net_drop_feature() -> None:
    """持续下切且收放平顺时，应命中下坡收官方向的文案。"""
    commentary = build_trip_commentary(
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
            style="aggressive",
            elevation_gain_m=20.0,
            elevation_loss_m=260.0,
            net_elevation_change_m=-220.0,
            terrain_variation_m_per_km=14.0,
        )
    )

    assert commentary == "一路往下放坡，收放居然挺稳"


def test_aggressive_mountain_commentary_uses_large_variation_feature() -> None:
    """高低差很大的山路场景，应能命中高低差专项文案。"""
    commentary = build_trip_commentary(
        _make_input(
            drive_id=713,
            distance_km=28.0,
            duration_min=42.0,
            avg_speed_kmh=40.0,
            max_speed_kmh=88.0,
            road_context="综合路况",
            traffic_label="整体畅通",
            stop_go_density=0.8,
            confidence=1.0,
            style="aggressive",
            elevation_gain_m=360.0,
            elevation_loss_m=340.0,
            net_elevation_change_m=20.0,
            terrain_variation_m_per_km=25.0,
        )
    )

    assert commentary == "高低差都拉满了，你居然还稳住了"


def test_normal_endurance_commentary_uses_multi_hour_feature() -> None:
    """连续驾驶数小时应命中耐力型专项文案。"""
    commentary = build_trip_commentary(
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
            style="normal",
        )
    )

    assert commentary == "🛣️ 连开几小时还稳得住，耐力真在线"


def test_aggressive_roadtrip_commentary_uses_human_tone() -> None:
    """几百公里的一口气长途应命中更有活人感的夸赞。"""
    commentary = build_trip_commentary(
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
            style="aggressive",
        )
    )

    assert commentary == "几百公里一口气拿下，牛逼啊🐮"


def test_aggressive_highway_slow_commentary_feels_more_human() -> None:
    """高速路况空出来但速度过慢时，应命中更口语化的吐槽文案。"""
    commentary = build_trip_commentary(
        _make_input(
            drive_id=804,
            distance_km=50.0,
            duration_min=52.0,
            avg_speed_kmh=58.0,
            max_speed_kmh=88.0,
            road_context="高速巡航",
            traffic_label="整体畅通",
            stop_go_density=0.3,
            confidence=1.0,
            style="aggressive",
        )
    )

    assert commentary == "路况这么通畅，还佛系巡航，节奏可以再放开些"
