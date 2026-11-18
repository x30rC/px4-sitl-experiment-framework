"""Воспроизводимые варианты QGC-миссии для отдельных SITL-прогонов."""

from __future__ import annotations

import json
import math
import random
from pathlib import Path


def make_route(
    template: Path,
    index: int,
    count: int,
    minimum_m: float,
    maximum_m: float,
    seed: int,
) -> tuple[dict, float, float]:
    """Меняет только конечный waypoint; направление разное для каждого повтора."""
    if not 0 <= index < count or count < 1:
        raise ValueError("некорректный номер или количество маршрутов")
    plan = json.loads(template.read_text(encoding="utf-8"))
    mission = plan["mission"]
    home = mission["plannedHomePosition"]
    items = mission["items"]
    if (
        plan.get("fileType") != "Plan"
        or len(home) < 2
        or not -90 < home[0] < 90
        or not -180 < home[1] < 180
        or len(items) != 2
        or items[0].get("command") != 22
        or items[1].get("command") != 16 or items[1].get("frame") != 3
        or len(items[1].get("params", [])) != 7
    ):
        raise ValueError("ожидается QGC Plan с home, takeoff и одним relative-alt waypoint")
    rng = random.Random(seed)
    sectors = list(range(count))
    rng.shuffle(sectors)
    for _ in range(index):
        rng.random()
        rng.uniform(minimum_m, maximum_m)
    bearing = 2 * math.pi * (sectors[index] + rng.random()) / count
    distance = rng.uniform(minimum_m, maximum_m)
    lat1, lon1 = math.radians(home[0]), math.radians(home[1])
    delta = distance / 6371000.0
    lat2 = math.asin(
        math.sin(lat1) * math.cos(delta)
        + math.cos(lat1) * math.sin(delta) * math.cos(bearing)
    )
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(delta) * math.cos(lat1),
        math.cos(delta) - math.sin(lat1) * math.sin(lat2),
    )
    latitude, longitude = math.degrees(lat2), (math.degrees(lon2) + 180) % 360 - 180
    items[1]["params"][4:6] = [latitude, longitude]
    return plan, distance, math.degrees(bearing)
