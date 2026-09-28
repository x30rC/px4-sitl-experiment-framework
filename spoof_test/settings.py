"""Модели конфигурации полного GPS/IMU-теста."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from .paths import GPS_SPOOF_SCRIPT, IMU_SPOOF_SCRIPT


@dataclass(frozen=True)
class GpsSpoofSettings:
    """Параметры, передаваемые из сценария запуска в ``g.py``."""

    intensity_percent: float = 100.0
    publish_rate_hz: float = 100.0
    active_seconds: float = 0.0
    idle_seconds: float = 0.0
    duration_seconds: float = 60.0

    reference_latitude: float = 47.398
    reference_longitude: float = 8.545
    reference_altitude: float = 488.0
    target_latitude: float = 47.399
    target_longitude: float = 8.546
    target_altitude: float = 500.0

    @property
    def is_periodic(self) -> bool:
        return self.active_seconds > 0.0 and self.idle_seconds > 0.0

    @property
    def effective_coordinates(self) -> tuple[float, float, float]:
        scale = self.intensity_percent / 100.0
        return (
            self.reference_latitude
            + (self.target_latitude - self.reference_latitude) * scale,
            self.reference_longitude
            + (self.target_longitude - self.reference_longitude) * scale,
            self.reference_altitude
            + (self.target_altitude - self.reference_altitude) * scale,
        )

    def validate(self) -> None:
        errors: list[str] = []
        for name, value in (
            ("intensity_percent", self.intensity_percent),
            ("publish_rate_hz", self.publish_rate_hz),
            ("active_seconds", self.active_seconds),
            ("idle_seconds", self.idle_seconds),
            ("duration_seconds", self.duration_seconds),
            ("reference_altitude", self.reference_altitude),
            ("target_altitude", self.target_altitude),
        ):
            if not math.isfinite(value):
                errors.append(f"{name} должен быть конечным числом")

        if not 0.0 <= self.intensity_percent <= 1000.0:
            errors.append("интенсивность должна быть в диапазоне 0..1000 %")
        if not 0.1 <= self.publish_rate_hz <= 1000.0:
            errors.append("частота должна быть в диапазоне 0.1..1000 Гц")
        if self.duration_seconds <= 0.0:
            errors.append("длительность GPS-публикации должна быть больше нуля")
        if self.active_seconds < 0.0 or self.idle_seconds < 0.0:
            errors.append("активный период и пауза не могут быть отрицательными")
        if (self.active_seconds == 0.0) != (self.idle_seconds == 0.0):
            errors.append(
                "active-seconds и idle-seconds должны быть оба больше нуля "
                "либо оба равны нулю"
            )

        for name, value, minimum, maximum in (
            ("reference_latitude", self.reference_latitude, -90.0, 90.0),
            ("target_latitude", self.target_latitude, -90.0, 90.0),
            ("reference_longitude", self.reference_longitude, -180.0, 180.0),
            ("target_longitude", self.target_longitude, -180.0, 180.0),
        ):
            if not math.isfinite(value) or not minimum <= value <= maximum:
                errors.append(f"{name} должен быть в диапазоне [{minimum}, {maximum}]")

        effective_latitude, effective_longitude, _ = self.effective_coordinates
        if not -90.0 <= effective_latitude <= 90.0:
            errors.append("итоговая широта после применения интенсивности выходит за диапазон")
        if not -180.0 <= effective_longitude <= 180.0:
            errors.append("итоговая долгота после применения интенсивности выходит за диапазон")

        if errors:
            raise ValueError("; ".join(errors))

    def build_command(self, python_executable: Path) -> list[str]:
        """Формирует воспроизводимую команду запуска ``g.py``."""
        return [
            str(python_executable),
            "-u",
            str(GPS_SPOOF_SCRIPT),
            "--intensity",
            str(self.intensity_percent),
            "--rate-hz",
            str(self.publish_rate_hz),
            "--duration",
            str(self.duration_seconds),
            "--active-seconds",
            str(self.active_seconds),
            "--idle-seconds",
            str(self.idle_seconds),
            "--reference-lat",
            str(self.reference_latitude),
            "--reference-lon",
            str(self.reference_longitude),
            "--reference-alt",
            str(self.reference_altitude),
            "--target-lat",
            str(self.target_latitude),
            "--target-lon",
            str(self.target_longitude),
            "--target-alt",
            str(self.target_altitude),
        ]

    # Совместимость с первой модульной версией проекта.
    def to_command(self, python_executable: Path) -> list[str]:
        return self.build_command(python_executable)

    def print_summary(self) -> None:
        latitude, longitude, altitude = self.effective_coordinates
        mode = "непрерывный"
        if self.is_periodic:
            mode = f"{self.active_seconds:g} сек работа / {self.idle_seconds:g} сек пауза"

        print("\n--- Настройки GPS-публикации ---")
        print(f"Интенсивность: {self.intensity_percent:g} %")
        print(f"Частота публикации: {self.publish_rate_hz:g} Гц")
        print(f"Периодичность: {mode}")
        print(f"Общая длительность: {self.duration_seconds:g} сек")
        print(f"Итоговые координаты: lat={latitude:.8f}, lon={longitude:.8f}, alt={altitude:.2f}")
        print("--------------------------------\n")


@dataclass(frozen=True)
class ImuSpoofSettings:
    """Параметры, передаваемые полным сценарием в ``i.py``."""

    intensity_percent: float = 100.0
    publish_rate_hz: float = 250.0
    active_seconds: float = 0.0
    idle_seconds: float = 0.0
    duration_seconds: float = 60.0
    angular_velocity_offset_x: float = 0.0
    angular_velocity_offset_y: float = 0.0
    angular_velocity_offset_z: float = 0.5
    linear_acceleration_offset_x: float = 0.0
    linear_acceleration_offset_y: float = 0.0
    linear_acceleration_offset_z: float = 0.0
    topic: str = "/spoof_gaz/imu"
    source_topic: str = (
        "/world/default/model/x500_0/link/base_link/sensor/imu_sensor/imu"
    )
    wait_for_activation: bool = False
    post_baseline_seconds: float = 0.0

    @property
    def is_periodic(self) -> bool:
        return self.active_seconds > 0.0 and self.idle_seconds > 0.0

    def validate(self) -> None:
        errors: list[str] = []
        for name, value in (
            ("intensity_percent", self.intensity_percent),
            ("publish_rate_hz", self.publish_rate_hz),
            ("active_seconds", self.active_seconds),
            ("idle_seconds", self.idle_seconds),
            ("duration_seconds", self.duration_seconds),
            ("angular_velocity_offset_x", self.angular_velocity_offset_x),
            ("angular_velocity_offset_y", self.angular_velocity_offset_y),
            ("angular_velocity_offset_z", self.angular_velocity_offset_z),
            ("linear_acceleration_offset_x", self.linear_acceleration_offset_x),
            ("linear_acceleration_offset_y", self.linear_acceleration_offset_y),
            ("linear_acceleration_offset_z", self.linear_acceleration_offset_z),
            ("post_baseline_seconds", self.post_baseline_seconds),
        ):
            if not math.isfinite(value):
                errors.append(f"{name} должен быть конечным числом")
        if not 0.0 <= self.intensity_percent <= 1000.0:
            errors.append("IMU-интенсивность должна быть в диапазоне 0..1000 %")
        if not 0.1 <= self.publish_rate_hz <= 2000.0:
            errors.append("частота IMU должна быть в диапазоне 0.1..2000 Гц")
        if self.duration_seconds <= 0.0:
            errors.append("длительность IMU-публикации должна быть больше нуля")
        if self.active_seconds < 0.0 or self.idle_seconds < 0.0:
            errors.append("IMU active/idle периоды не могут быть отрицательными")
        if (self.active_seconds == 0.0) != (self.idle_seconds == 0.0):
            errors.append(
                "IMU active-seconds и idle-seconds должны быть оба больше нуля "
                "либо оба равны нулю"
            )
        if errors:
            raise ValueError("; ".join(errors))
        if not self.topic.startswith("/"):
            raise ValueError("IMU topic должен начинаться с '/'")
        if not self.source_topic.startswith("/"):
            raise ValueError("IMU source topic должен начинаться с '/'")
        if self.source_topic == self.topic:
            raise ValueError("IMU source topic и output topic должны отличаться")
        if self.post_baseline_seconds < 0.0:
            raise ValueError("IMU post baseline не может быть отрицательным")

    def build_command(self, python_executable: Path) -> list[str]:
        command = [
            str(python_executable),
            "-u",
            str(IMU_SPOOF_SCRIPT),
            "--topic",
            self.topic,
            "--source-topic",
            self.source_topic,
            "--intensity",
            str(self.intensity_percent),
            "--rate-hz",
            str(self.publish_rate_hz),
            "--duration",
            str(self.duration_seconds),
            "--active-seconds",
            str(self.active_seconds),
            "--idle-seconds",
            str(self.idle_seconds),
            "--angular-velocity-offset-x",
            str(self.angular_velocity_offset_x),
            "--angular-velocity-offset-y",
            str(self.angular_velocity_offset_y),
            "--angular-velocity-offset-z",
            str(self.angular_velocity_offset_z),
            "--linear-acceleration-offset-x",
            str(self.linear_acceleration_offset_x),
            "--linear-acceleration-offset-y",
            str(self.linear_acceleration_offset_y),
            "--linear-acceleration-offset-z",
            str(self.linear_acceleration_offset_z),
        ]
        if self.wait_for_activation:
            command.append("--wait-for-activation")
        if self.post_baseline_seconds:
            command.extend(
                ["--post-baseline-seconds", str(self.post_baseline_seconds)]
            )
        return command

    def print_summary(self) -> None:
        scale = self.intensity_percent / 100.0
        mode = "непрерывный"
        if self.is_periodic:
            mode = f"{self.active_seconds:g} сек работа / {self.idle_seconds:g} сек пауза"
        print("\n--- Настройки IMU-публикации ---")
        print(f"Топик PX4 IMU: {self.topic}")
        print(f"Штатный IMU source: {self.source_topic}")
        print(f"Интенсивность: {self.intensity_percent:g} %")
        print(f"Частота публикации: {self.publish_rate_hz:g} Гц")
        print(f"Периодичность: {mode}")
        print(f"Общая длительность: {self.duration_seconds:g} сек")
        print(
            "Угловая скорость offset, рад/с: "
            f"x={self.angular_velocity_offset_x * scale:g}, "
            f"y={self.angular_velocity_offset_y * scale:g}, "
            f"z={self.angular_velocity_offset_z * scale:g}"
        )
        print(
            "Линейное ускорение offset, м/с²: "
            f"x={self.linear_acceleration_offset_x * scale:g}, "
            f"y={self.linear_acceleration_offset_y * scale:g}, "
            f"z={self.linear_acceleration_offset_z * scale:g}"
        )
        print("--------------------------------\n")


@dataclass(frozen=True)
class SimulationTestSettings:
    """Настройки полного сценария PX4/Gazebo."""

    px4_startup_delay: float = 15.0
    stabilization_delay: float = 10.0
    wait_after_spoof: float = 5.0
    mission_timeout: float = 45.0
    skip_mission: bool = False
    skip_px4: bool = False
    interactive: bool = False
    dry_run: bool = False
    spoof_sensor: str = "gps"

    def validate(self) -> None:
        for name, value in (
            ("px4_startup_delay", self.px4_startup_delay),
            ("stabilization_delay", self.stabilization_delay),
            ("wait_after_spoof", self.wait_after_spoof),
            ("mission_timeout", self.mission_timeout),
        ):
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} не может быть отрицательным")
        if self.mission_timeout == 0.0:
            raise ValueError("тайм-аут MAVSDK должен быть больше нуля")
        if self.spoof_sensor not in {"gps", "imu", "both"}:
            raise ValueError("spoof_sensor должен быть gps, imu или both")

    @property
    def use_gps(self) -> bool:
        return self.spoof_sensor in {"gps", "both"}

    @property
    def use_imu(self) -> bool:
        return self.spoof_sensor in {"imu", "both"}