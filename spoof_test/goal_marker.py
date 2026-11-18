"""Non-colliding Gazebo visual for the final waypoint of a QGC mission."""

from __future__ import annotations

import json
import math
from pathlib import Path
from xml.sax.saxutils import escape


def final_waypoint(plan_path: Path) -> tuple[float, float, float]:
    """Return latitude, longitude and relative altitude from the actual uploaded plan."""
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    items = plan["mission"]["items"]
    if not items or items[-1].get("command") != 16 or items[-1].get("frame") != 3:
        raise ValueError("маркер поддерживает конечный waypoint QGC с относительной высотой")
    params = items[-1]["params"]
    if len(params) != 7:
        raise ValueError("в конечной точке миссии ожидается 7 параметров")
    latitude, longitude, altitude = params[4:7]
    if (not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (latitude, longitude, altitude))
            or not -90 <= latitude <= 90 or not -180 <= longitude <= 180 or altitude < 0):
        raise ValueError("некорректные координаты или высота конечного waypoint")
    return float(latitude), float(longitude), float(altitude)


def marker_sdf(altitude: float, name: str = "spoof_gaz_goal") -> str:
    """Static sphere and beacon: visuals only, no collision or effect on the drone."""
    if not math.isfinite(altitude) or altitude < 0:
        raise ValueError("высота маркера должна быть неотрицательной")
    pole_height = max(altitude, 1.0)
    return f"""<?xml version="1.0"?>
<sdf version="1.9"><model name="{escape(name)}"><static>true</static>
  <link name="visual_only">
    <visual name="goal_sphere"><pose>0 0 {altitude:g} 0 0 0</pose>
      <geometry><sphere><radius>5</radius></sphere></geometry>
      <material><ambient>1 0.15 0.05 1</ambient><diffuse>1 0.15 0.05 1</diffuse>
      <emissive>0.8 0.08 0.02 1</emissive></material>
    </visual>
    <visual name="ground_beacon"><pose>0 0 {pole_height / 2:g} 0 0 0</pose>
      <geometry><cylinder><radius>0.6</radius><length>{pole_height:g}</length></cylinder></geometry>
      <material><ambient>1 0.65 0.05 1</ambient><diffuse>1 0.65 0.05 1</diffuse>
      <emissive>0.4 0.2 0 1</emissive></material>
    </visual>
  </link></model></sdf>"""


def spawn_goal_marker(plan_path: Path, world: str = "default") -> tuple[float, float, float]:
    """Spawn via Gazebo's geodetic create service, without guessing ENU conversion."""
    from gz.msgs10.boolean_pb2 import Boolean
    from gz.msgs10.entity_factory_pb2 import EntityFactory
    from gz.transport13 import Node

    lat, lon, altitude = final_waypoint(plan_path)
    request = EntityFactory()
    request.name = "spoof_gaz_goal"
    request.sdf = marker_sdf(altitude)
    request.spherical_coordinates.latitude_deg = lat
    request.spherical_coordinates.longitude_deg = lon
    # The PX4 default world has elevation 0. The SDF visuals have their own
    # height above ground; plannedHomePosition[2] is NOT Gazebo world height.
    request.spherical_coordinates.elevation = 0.0
    delivered, response = Node().request(
        f"/world/{world}/create", request, EntityFactory, Boolean, 3000
    )
    if not delivered or not response.data:
        raise RuntimeError("Gazebo не подтвердил создание маркера конечной точки")
    return lat, lon, altitude
