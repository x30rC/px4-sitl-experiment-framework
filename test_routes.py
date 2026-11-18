"""Проверки вариантов маршрута без запуска PX4."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spoof_test.cli import build_argument_parser, settings_from_arguments
from spoof_test.logs import Px4LogCollector
from spoof_test.processes import ProcessSupervisor
from spoof_test.paths import MISSION_PLAN
from spoof_test.routes import make_route
from spoof_test.settings import GpsSpoofSettings, SimulationTestSettings
from spoof_test.workflow import GpsSpoofTestWorkflow


class RouteTests(unittest.TestCase):
    def test_routes_are_distant_distinct_and_reproducible(self):
        original = json.loads(MISSION_PLAN.read_text(encoding="utf-8"))
        bearings = []
        routes = []
        for index in range(10):
            plan, distance, bearing = make_route(MISSION_PLAN, index, 10, 400, 800, 42)
            self.assertGreaterEqual(distance, 400)
            self.assertLessEqual(distance, 800)
            self.assertEqual(plan["mission"]["items"][0], original["mission"]["items"][0])
            restored = copy.deepcopy(plan)
            restored["mission"]["items"][1]["params"][4:6] = original["mission"]["items"][1]["params"][4:6]
            self.assertEqual(restored, original)
            self.assertEqual((plan, distance, bearing), make_route(MISSION_PLAN, index, 10, 400, 800, 42))
            bearings.append(bearing)
            routes.append(plan["mission"]["items"][1]["params"][4:6])
        self.assertEqual(len(set(int(b // 36) for b in bearings)), 10)
        self.assertEqual(len(set(tuple(r) for r in routes)), 10)

    def test_invalid_template_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "plan.plan"
            path.write_text('{"mission":{"plannedHomePosition":[0,0],"items":[]}}')
            with self.assertRaises(ValueError):
                make_route(path, 0, 2, 400, 800, 42)

    def test_repeat_constraints(self):
        for settings in (
            SimulationTestSettings(repeats=0),
            SimulationTestSettings(repeats=2, skip_px4=True),
            SimulationTestSettings(repeats=2, skip_mission=True),
            SimulationTestSettings(random_mission=True, skip_mission=True),
            SimulationTestSettings(route_min_m=800, route_max_m=400),
        ):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                settings.validate()

    def test_cli_and_repeated_dry_run(self):
        args = build_argument_parser().parse_args([
            "--non-interactive", "--dry-run", "--repeats", "3", "--route-seed", "42",
        ])
        gps, imu, settings = settings_from_arguments(args)
        self.assertEqual(settings.repeats, 3)
        self.assertEqual(settings.route_seed, 42)
        workflow = GpsSpoofTestWorkflow(settings, gps, imu)
        with patch.object(workflow, "_run_once", return_value=0) as run_once:
            self.assertEqual(workflow.run(), 0)
        self.assertEqual(run_once.call_count, 3)

    def test_generated_plan_is_passed_to_workflow_and_template_untouched(self):
        template = MISSION_PLAN.read_bytes()
        workflow = GpsSpoofTestWorkflow(
            SimulationTestSettings(random_mission=True, route_seed=42),
            GpsSpoofSettings(),
        )

        def inspect_plan(path, *, save_route):
            self.assertTrue(save_route)
            self.assertTrue(path.is_file())
            result = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotEqual(
                result["mission"]["items"][1]["params"][4:6],
                json.loads(template)["mission"]["items"][1]["params"][4:6],
            )
            return 0

        with patch.object(workflow, "_run_once", side_effect=inspect_plan):
            self.assertEqual(workflow.run(), 0)
        self.assertEqual(MISSION_PLAN.read_bytes(), template)

    def test_ulog_directory_collision_creates_new_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source" / "2026-10-06"
            source.mkdir(parents=True)
            log = source / "flight.ulg"
            log.write_bytes(b"ULog-test")
            collector = Px4LogCollector(root / "source", root / "destination")
            first = collector.collect_latest_since(0)
            second = collector.collect_latest_since(0)
            self.assertNotEqual(first, second)
            self.assertEqual((first / log.name).read_bytes(), (second / log.name).read_bytes())

    def test_ulog_selection_uses_prelaunch_snapshot_not_clock(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source" / "2026-10-06"
            source.mkdir(parents=True)
            old = source / "old.ulg"
            old.write_bytes(b"old")
            collector = Px4LogCollector(root / "source", root / "destination")
            snapshot = collector.snapshot_existing()
            fresh = source / "fresh.ulg"
            fresh.write_bytes(b"new")
            fresh.touch()
            import os
            os.utime(fresh, (1, 1))  # PX4 wall clock differs from launcher
            saved = collector.collect_latest_since(10_000_000, existing_logs=snapshot)
            self.assertEqual((saved / "fresh.ulg").read_bytes(), b"new")
            self.assertFalse((saved / "old.ulg").exists())

    def test_flight_csv_removes_only_current_folder_after_success(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            source.mkdir()
            (source / "flight.ulg").write_bytes(b"flight")
            logs = root / "logs"
            logs.mkdir()
            old = logs / "old-flight"
            old.mkdir()
            (old / "old.ulg").write_bytes(b"old")
            workflow = GpsSpoofTestWorkflow(SimulationTestSettings(), GpsSpoofSettings(),
                                             log_collector=Px4LogCollector(source, logs))

            def convert(current, base):
                self.assertEqual(base, logs)
                self.assertTrue((current / "flight.ulg").exists())
                (logs / f"{current.name}_flight.csv").write_text("timestamp_us\n1000000\n")
                return True

            with patch("consolidate_logs.process_folder", side_effect=convert):
                workflow._save_flight_csv(set(), None)
            self.assertEqual(len(list(logs.glob("*.csv"))), 1)
            self.assertEqual([p.name for p in logs.iterdir() if p.is_dir()], [old.name])
            self.assertTrue((old / "old.ulg").exists())

    def test_flight_csv_failure_preserves_ulog(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            source.mkdir()
            (source / "flight.ulg").write_bytes(b"flight")
            logs = root / "logs"
            workflow = GpsSpoofTestWorkflow(SimulationTestSettings(), GpsSpoofSettings(),
                                             log_collector=Px4LogCollector(source, logs))
            with patch("consolidate_logs.process_folder", return_value=False):
                with self.assertRaisesRegex(RuntimeError, "ULog сохранён"):
                    workflow._save_flight_csv(set(), None)
            self.assertEqual(len(list(logs.rglob("*.ulg"))), 1)
            self.assertEqual(list(logs.glob("*.csv")), [])

    def test_wait_healthcheck_detects_simulator_failure(self):
        class Running:
            pid = 111

            def poll(self):
                return None

        with self.assertRaisesRegex(RuntimeError, "Gazebo"):
            ProcessSupervisor.wait_processes_while_running(
                "checking", [(Running(), 0.1)],
                healthcheck=lambda: (_ for _ in ()).throw(RuntimeError("Gazebo stopped")),
            )

    def test_fail_fast(self):
        settings = SimulationTestSettings(repeats=3, dry_run=True)
        workflow = GpsSpoofTestWorkflow(settings, GpsSpoofSettings())
        with patch.object(workflow, "_run_once", side_effect=[0, 1]) as run_once:
            self.assertEqual(workflow.run(), 1)
        self.assertEqual(run_once.call_count, 2)


if __name__ == "__main__":
    unittest.main()
