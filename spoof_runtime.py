"""Общая чистая логика для GPS- и IMU-публикаторов."""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass


def parse_finite_float(value: str) -> float:
    """Преобразует CLI-значение в конечное число с поддержкой запятой."""
    try:
        result = float(value.replace(",", "."))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"ожидалось число, получено: {value}") from exc
    if not math.isfinite(result):
        raise argparse.ArgumentTypeError("значение должно быть конечным числом")
    return result


@dataclass(frozen=True)
class ActiveIdleSchedule:
    """Определяет активную и пассивную фазы периодической публикации."""

    active_seconds: float = 0.0
    idle_seconds: float = 0.0

    @property
    def is_periodic(self) -> bool:
        return self.active_seconds > 0.0 and self.idle_seconds > 0.0

    def is_active(self, elapsed_seconds: float) -> bool:
        if not self.is_periodic:
            return True
        cycle_seconds = self.active_seconds + self.idle_seconds
        return elapsed_seconds % cycle_seconds < self.active_seconds

    def seconds_until_transition(self, elapsed_seconds: float) -> float:
        if not self.is_periodic:
            return float("inf")
        cycle_seconds = self.active_seconds + self.idle_seconds
        cycle_position = elapsed_seconds % cycle_seconds
        if cycle_position < self.active_seconds:
            return self.active_seconds - cycle_position
        return cycle_seconds - cycle_position