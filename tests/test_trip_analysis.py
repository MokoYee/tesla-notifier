"""行程分析算法测试。"""

from datetime import datetime, timedelta

from tesla_notifier.storage.database import (
    DrivingContext,
    TerrainContext,
    _build_terrain_context,
    _build_trip_key_factors,
    _count_hard_accel_events,
    _count_hard_brake_events,
    _normalized_event_rate,
)


def _sample(second: float, elevation: float) -> tuple[float, datetime]:
    """构造带时间戳的海拔样本。"""
    started_at = datetime(2026, 8, 11, 11, 0, 0)
    return elevation, started_at + timedelta(seconds=second)


def test_terrain_context_filters_gps_elevation_jump() -> None:
    """GPS 从正常海拔瞬时跳到数百米时，不应生成虚假地形起伏。"""
    terrain = _build_terrain_context(
        [
            _sample(0, 46),
            _sample(1, 46),
            _sample(2, 318),
            _sample(8, 238),
            _sample(55, 96),
            _sample(56, 82),
            _sample(57, 73),
            _sample(58, 69),
            _sample(59, 67),
            _sample(60, 66),
            _sample(61, 66),
        ],
        distance_km=5.3,
    )

    assert terrain.elevation_gain_m == 0.0
    assert terrain.elevation_loss_m < 10.0
    assert terrain.net_elevation_change_m == 20.0
    assert terrain.terrain_variation_m_per_km < 2.0


def test_terrain_context_keeps_gradual_climb() -> None:
    """连续、合理的海拔变化应继续计入真实爬升。"""
    terrain = _build_terrain_context(
        [
            _sample(0, 100),
            _sample(5, 103),
            _sample(10, 107),
            _sample(15, 112),
            _sample(20, 118),
        ],
        distance_km=2.0,
    )

    assert terrain.elevation_gain_m == 18.0
    assert terrain.elevation_loss_m == 0.0
    assert terrain.net_elevation_change_m == 18.0
    assert terrain.terrain_variation_m_per_km == 9.0


def test_key_factors_keep_terrain_evidence_out_of_commentary() -> None:
    """结构化证据应集中在关键因素，供通知压缩为单行。"""
    factors = _build_trip_key_factors(
        context=DrivingContext(
            urban_ratio=0.2,
            highway_ratio=0.1,
            overspeed_ratio=0.0,
            stop_go_density=1.0,
            road_context="综合路况",
        ),
        terrain=TerrainContext(
            elevation_gain_m=260.0,
            elevation_loss_m=40.0,
            net_elevation_change_m=220.0,
            terrain_variation_m_per_km=14.5,
        ),
        traffic_summary=None,
        outside_temp_c=20.0,
        hard_accel_count=1,
        hard_brake_count=2,
    )

    assert factors == ["综合路况", "急加速 1 次、急减速 2 次", "净爬升 220 m"]


def test_event_detection_is_independent_from_sampling_frequency() -> None:
    """相同动作在高低采样频率下应得到相同事件数。"""
    started_at = datetime(2026, 8, 11, 11, 0, 0)

    def motion_sample(
        second: float,
        power: float,
        speed: float,
    ) -> tuple[float, float, datetime]:
        return power, speed, started_at + timedelta(seconds=second)

    dense_accel = [
        motion_sample(0.0, 0, 20),
        motion_sample(0.25, 5, 22),
        motion_sample(0.5, 15, 25),
        motion_sample(0.75, 30, 28),
        motion_sample(1.0, 55, 31),
    ]
    sparse_accel = [dense_accel[0], dense_accel[-1]]
    dense_brake: list[tuple[float, datetime]] = [
        (32.0, started_at + timedelta(seconds=0.0)),
        (29.0, started_at + timedelta(seconds=0.25)),
        (26.0, started_at + timedelta(seconds=0.5)),
        (23.0, started_at + timedelta(seconds=0.75)),
        (20.0, started_at + timedelta(seconds=1.0)),
    ]
    sparse_brake = [dense_brake[0], dense_brake[-1]]

    assert _count_hard_accel_events(dense_accel) == 1
    assert _count_hard_accel_events(sparse_accel) == 1
    assert _count_hard_brake_events(dense_brake) == 1
    assert _count_hard_brake_events(sparse_brake) == 1


def test_short_trip_event_rate_uses_minimum_exposure() -> None:
    """短途事件频率应按最少 15 km 归一化，避免分母过小放大风险。"""
    assert _normalized_event_rate(3, 5.3) == 20.0
    assert _normalized_event_rate(3, 30.0) == 10.0
