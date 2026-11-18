"""Settings for Gazebo magnetometer and pressure relays."""

from dataclasses import dataclass
import math
from pathlib import Path

from sensor_relay import SENSORS
from .paths import SENSOR_RELAY_SCRIPT


@dataclass(frozen=True)
class RelaySettings:
    sensor: str
    intensity_percent: float = 100.0
    duration_seconds: float = 60.0
    time_mode: str = "sync"
    active_seconds: float = 0.0
    idle_seconds: float = 0.0
    offsets: tuple[float, ...] = ()
    model_name: str = "x500_0"
    world_name: str = "default"

    @property
    def topic(self):
        return f"/spoof_gaz/{self.sensor}"

    @property
    def source_topic(self):
        suffix = SENSORS[self.sensor][0]
        return f"/world/{self.world_name}/model/{self.model_name}/link/base_link/sensor/{suffix}"

    @property
    def environment_key(self):
        return SENSORS[self.sensor][1]

    @property
    def effective_offsets(self):
        return self.offsets if self.offsets else ((0.0, 0.0, 0.00002) if self.sensor == "mag" else (100.0,))

    def validate(self):
        if self.time_mode not in {"sync", "async"}:
            raise ValueError("time_mode должен быть sync или async")
        if self.sensor not in SENSORS or not self.model_name or not self.world_name:
            raise ValueError("invalid sensor, model or world")
        if len(self.effective_offsets) != (3 if self.sensor == "mag" else 1):
            raise ValueError("invalid offsets length")
        if not all(math.isfinite(v) for v in (self.intensity_percent, self.duration_seconds,
                self.active_seconds, self.idle_seconds, *self.effective_offsets)):
            raise ValueError("sensor settings must be finite")
        if not 0 <= self.intensity_percent <= 1000 or self.duration_seconds <= 0:
            raise ValueError("invalid intensity or duration")
        if min(self.active_seconds, self.idle_seconds) < 0 or (self.active_seconds == 0) != (self.idle_seconds == 0):
            raise ValueError("active/idle must both be positive or both zero")
        if self.sensor == "baro" and self.effective_offsets[0] * self.intensity_percent / 100 <= -80000:
            raise ValueError("pressure offset too negative for flight")

    def build_command(self, python: Path):
        command = [str(python), "-u", str(SENSOR_RELAY_SCRIPT), self.sensor,
                   "--time-mode", self.time_mode,
                   "--source-topic", self.source_topic, "--topic", self.topic,
                   "--intensity", str(self.intensity_percent), "--duration", str(self.duration_seconds),
                   "--active-seconds", str(self.active_seconds), "--idle-seconds", str(self.idle_seconds)]
        if self.sensor == "mag":
            for axis, value in zip("xyz", self.effective_offsets):
                command += [f"--{axis}", str(value)]
        else:
            command += ["--pressure-offset", str(self.effective_offsets[0])]
        return command
