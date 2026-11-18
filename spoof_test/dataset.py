"""Временные ряды PX4 для анализа GNSS/IMU и ошибок относительно SITL truth."""

from __future__ import annotations

import math
from pathlib import Path

# Только явно названные поля; отсутствующие в конкретной версии PX4 не создаются.
# sensor_gyro / sensor_accel — измерения, а vehicle_* — результат оценивания.
ANALYSIS_FIELDS = {
    "vehicle_gps_position": (
        "latitude_deg", "longitude_deg", "altitude_msl_m", "fix_type", "satellites_used",
        "eph", "epv", "jamming_indicator", "spoofing_state", "vel_n_m_s", "vel_e_m_s", "vel_d_m_s",
    ),
    "sensor_gps": ("latitude_deg", "longitude_deg", "altitude_msl_m", "fix_type", "eph", "epv"),
    "vehicle_global_position": ("lat", "lon", "alt", "dead_reckoning"),
    "vehicle_global_position_groundtruth": ("lat", "lon", "alt"),
    "vehicle_local_position": ("x", "y", "z", "vx", "vy", "vz", "heading", "xy_valid", "z_valid", "ref_lat", "ref_lon"),
    "vehicle_local_position_groundtruth": ("x", "y", "z", "vx", "vy", "vz", "heading", "ref_lat", "ref_lon"),
    "vehicle_attitude": ("q[0]", "q[1]", "q[2]", "q[3]"),
    "vehicle_angular_velocity": ("xyz[0]", "xyz[1]", "xyz[2]"),
    "vehicle_angular_velocity_groundtruth": ("xyz[0]", "xyz[1]", "xyz[2]"),
    "sensor_gyro": ("x", "y", "z"),
    "sensor_accel": ("x", "y", "z"),
    "vehicle_imu": ("delta_angle[0]", "delta_angle[1]", "delta_angle[2]", "delta_velocity[0]", "delta_velocity[1]", "delta_velocity[2]", "delta_angle_dt", "delta_velocity_dt"),
    "vehicle_imu_status": ("accel_clipping[0]", "gyro_clipping[0]", "accel_rate_hz", "gyro_rate_hz"),
    "estimator_aid_src_gnss_pos": ("innovation[0]", "innovation[1]", "test_ratio[0]", "test_ratio[1]", "fused", "innovation_rejected"),
    "estimator_aid_src_gnss_vel": ("innovation[0]", "innovation[1]", "innovation[2]", "fused", "innovation_rejected"),
    "estimator_aid_src_gnss_hgt": ("innovation", "test_ratio", "fused", "innovation_rejected"),
    "estimator_aid_src_gravity": ("innovation[0]", "innovation[1]", "innovation[2]", "fused", "innovation_rejected"),
    "estimator_status": ("gps_check_fail_flags", "solution_status_flags", "pos_horiz_accuracy", "pos_vert_accuracy"),
    "estimator_status_flags": ("cs_gnss_pos", "cs_inertial_dead_reckoning", "cs_gps_yaw"),
    "sensors_status_imu": ("accel_inconsistency_m_s_s[0]", "gyro_inconsistency_rad_s[0]"),
    "vehicle_status": ("nav_state", "arming_state", "failsafe"),
    "mission_result": ("finished", "failure", "seq_current"),
    "failsafe_flags": ("local_position_invalid", "global_position_invalid"),
    "vehicle_local_position_setpoint": ("x", "y", "z", "vx", "vy", "vz"),
}


def matching_topic(stem: str, ulg_stem: str) -> tuple[str, int] | None:
    """Точное сопоставление имени ULog/топика/instance, без совпадений подстрок."""
    prefix = ulg_stem + "_"
    if not stem.startswith(prefix):
        return None
    suffix = stem[len(prefix):]
    for name in ANALYSIS_FIELDS:
        if suffix == name + "_0":
            return name, 0
    return None


def horizontal_distance_m(lat1, lon1, lat2, lon2):
    """Гаверсинус по координатам в градусах (локальная оценка в метрах)."""
    a1, a2 = math.radians(lat1), math.radians(lat2)
    da, dl = a2 - a1, math.radians(lon2 - lon1)
    h = math.sin(da / 2) ** 2 + math.cos(a1) * math.cos(a2) * math.sin(dl / 2) ** 2
    return 2 * 6371000 * math.asin(min(1.0, math.sqrt(h)))


def build_analysis_csv(csv_files: list[Path], ulg_stem: str, output: Path,
                       *, sample_ms: int = 100, max_age_ms: int = 500):
    """Выравнивает по timestamp (мкс) назад, никогда не подставляя будущие данные."""
    import numpy as np
    import pandas as pd

    if sample_ms <= 0 or max_age_ms < 0:
        raise ValueError("sample_ms должен быть > 0, max_age_ms должен быть >= 0")

    streams = {}
    for csv_file in csv_files:
        matched = matching_topic(csv_file.stem, ulg_stem)
        if matched is None:
            continue
        topic, _ = matched
        header = pd.read_csv(csv_file, nrows=0).columns
        fields = [field for field in ANALYSIS_FIELDS[topic] if field in header]
        if "timestamp" not in header:
            continue
        data = pd.read_csv(csv_file, usecols=["timestamp", *fields])
        data["timestamp"] = pd.to_numeric(data["timestamp"], errors="coerce")
        data = data.dropna(subset=["timestamp"]).sort_values("timestamp")
        if data.empty:
            continue
        data["timestamp"] = data["timestamp"].astype("int64")
        # Повторные timestamp оставляем последнюю запись, как и as-of merge.
        streams[topic] = data.drop_duplicates("timestamp", keep="last")
    if not streams:
        return False

    step = sample_ms * 1000
    start = min(int(data.timestamp.iloc[0]) for data in streams.values())
    stop = max(int(data.timestamp.iloc[-1]) for data in streams.values())
    times = np.arange(start, stop + 1, step, dtype="int64")
    clock = pd.DataFrame({"timestamp_us": times, "time_s": (times - start) / 1e6})
    aligned_streams = []
    for topic, data in streams.items():
        data = data.rename(columns={"timestamp": "source_timestamp_us", **{
            field: f"{topic}.{field}" for field in data.columns if field != "timestamp"
        }})
        aligned = pd.merge_asof(
            clock[["timestamp_us"]], data, left_on="timestamp_us",
            right_on="source_timestamp_us", direction="backward", tolerance=max_age_ms * 1000,
        )
        aligned[f"{topic}.age_ms"] = (clock.timestamp_us - aligned.source_timestamp_us) / 1000
        aligned_streams.append(aligned.drop(columns=["timestamp_us", "source_timestamp_us"]))
    result = pd.concat([clock, *aligned_streams], axis=1)
    derived = {}

    def error_distance(prefix, lat, lon, tlat, tlon, alt=None, talt=None):
        columns = [lat, lon, tlat, tlon]
        if not all(name in result for name in columns):
            return
        values = result[columns].apply(pd.to_numeric, errors="coerce")
        valid = values.notna().all(axis=1) & values[lat].between(-90, 90) & values[tlat].between(-90, 90) & values[lon].between(-180, 180) & values[tlon].between(-180, 180)
        errors = pd.Series(np.nan, index=result.index)
        errors.loc[valid] = [horizontal_distance_m(*row)
                             for row in values.loc[valid].itertuples(index=False, name=None)]
        derived[f"{prefix}.horizontal_error_m"] = errors
        if alt in result and talt in result:
            derived[f"{prefix}.altitude_error_m"] = pd.to_numeric(result[alt], errors="coerce") - pd.to_numeric(result[talt], errors="coerce")

    truth = "vehicle_global_position_groundtruth"
    error_distance("gps_vs_truth", "vehicle_gps_position.latitude_deg",
                   "vehicle_gps_position.longitude_deg", f"{truth}.lat", f"{truth}.lon",
                   "vehicle_gps_position.altitude_msl_m", f"{truth}.alt")
    error_distance("estimate_vs_truth", "vehicle_global_position.lat",
                   "vehicle_global_position.lon", f"{truth}.lat", f"{truth}.lon",
                   "vehicle_global_position.alt", f"{truth}.alt")
    local, actual = "vehicle_local_position", "vehicle_local_position_groundtruth"
    # Абсолютные local x/y не сравниваем: у оценивателя и ground truth могут
    # различаться опорные точки (ref_lat/ref_lon). Для скорости NED общая.
    if all(f"{name}.{axis}" in result for name in (local, actual) for axis in ("vx", "vy")):
        dvx = pd.to_numeric(result[f"{local}.vx"], errors="coerce") - pd.to_numeric(result[f"{actual}.vx"], errors="coerce")
        dvy = pd.to_numeric(result[f"{local}.vy"], errors="coerce") - pd.to_numeric(result[f"{actual}.vy"], errors="coerce")
        derived["local_estimate_vs_truth.horizontal_velocity_error_m_s"] = np.hypot(dvx, dvy)
    if derived:
        result = pd.concat([result, pd.DataFrame(derived, index=result.index)], axis=1)
    result.to_csv(output, index=False)
    return True
