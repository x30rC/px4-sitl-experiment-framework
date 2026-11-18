"""Geometry and Gazebo request tests without a running simulator."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spoof_test.goal_marker import final_waypoint, marker_sdf, spawn_goal_marker
from spoof_test.paths import MISSION_PLAN
from spoof_test.routes import make_route


class GoalMarkerTests(unittest.TestCase):
    def test_final_waypoint_of_actual_plan(self):
        result = final_waypoint(MISSION_PLAN)
        self.assertEqual(result, (47.40343255, 8.55517113, 50.0))
        plan, _, _ = make_route(MISSION_PLAN, 0, 2, 400, 800, 42)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "route.plan"
            path.write_text(json.dumps(plan), encoding="utf-8")
            self.assertEqual(final_waypoint(path)[:2], tuple(plan["mission"]["items"][1]["params"][4:6]))

    def test_marker_is_static_and_has_no_collision(self):
        from xml.etree import ElementTree

        model = ElementTree.fromstring(marker_sdf(50))
        self.assertEqual(model.find("model/static").text, "true")
        self.assertIsNone(model.find(".//collision"))
        self.assertEqual(model.find(".//sphere/radius").text, "5")

    def test_gazebo_create_request_uses_geographic_coordinates(self):
        with patch("gz.transport13.Node") as node:
            response = type("Response", (), {"data": True})()
            node.return_value.request.return_value = (True, response)
            self.assertEqual(spawn_goal_marker(MISSION_PLAN), (47.40343255, 8.55517113, 50))
            service, request, *_ = node.return_value.request.call_args.args
            self.assertEqual(service, "/world/default/create")
            self.assertEqual(request.spherical_coordinates.latitude_deg, 47.40343255)
            self.assertEqual(request.spherical_coordinates.longitude_deg, 8.55517113)
            self.assertNotIn("<collision", request.sdf)


if __name__ == "__main__":
    unittest.main()
