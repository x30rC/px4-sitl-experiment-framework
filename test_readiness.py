"""Готовность настоящего мира Gazebo и изоляция последовательных запусков."""

import os
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import Mock, patch

from spoof_test.readiness import wait_for_sensors
from spoof_test.settings import GpsSpoofSettings, SimulationTestSettings
from spoof_test.paths import MISSION_PLAN
from spoof_test.workflow import GpsSpoofTestWorkflow


class ReadinessTests(unittest.TestCase):
    def test_waits_for_real_messages_not_advertised_topics(self):
        callbacks = {}
        node = Mock()
        node.subscribe.side_effect = lambda cls, topic, callback: callbacks.setdefault(topic, callback) or True
        clock = [0.0]
        def tick(_seconds):
            clock[0] += 1.0
            if clock[0] == 2:
                callbacks[next(topic for topic in callbacks if "navsat" in topic)](object())
            if clock[0] == 3:
                callbacks[next(topic for topic in callbacks if "magnetometer" in topic)](object())

        healthcheck = Mock()
        with (patch("gz.transport13.Node", return_value=node),
              patch("spoof_test.readiness.time.monotonic", side_effect=lambda: clock[0]),
              patch("spoof_test.readiness.time.sleep", side_effect=tick),
              redirect_stdout(StringIO())):
            wait_for_sensors({"mag"}, healthcheck, timeout=5)
        self.assertGreaterEqual(healthcheck.call_count, 3)
        self.assertEqual(len(callbacks), 2)

    def test_no_messages_fails_before_mission(self):
        node = Mock()
        node.subscribe.return_value = True  # объявления топиков есть, данных нет
        clock = [0.0]
        with (patch("gz.transport13.Node", return_value=node),
              patch("spoof_test.readiness.time.monotonic", side_effect=lambda: clock[0]),
              patch("spoof_test.readiness.time.sleep", side_effect=lambda sec: clock.__setitem__(0, clock[0] + sec)),
              redirect_stdout(StringIO())):
            with self.assertRaisesRegex(RuntimeError, "нет сообщений gps, mag"):
                wait_for_sensors({"mag"}, Mock(), timeout=0.5)

    def test_repeat_runs_get_distinct_partitions_and_restore_parent(self):
        # repeats требует миссию, поэтому проверяем два последовательных _run_once.
        settings = SimulationTestSettings(spoof_sensor="none", skip_mission=True,
                                          px4_startup_delay=0, stabilization_delay=0, baseline_duration=0.1)
        partitions = []
        supervisor = Mock()
        px4 = Mock()
        px4.poll.return_value = None
        supervisor.start_process.return_value = px4
        supervisor.wait_while_running.return_value = None
        workflow = GpsSpoofTestWorkflow(settings, GpsSpoofSettings(),
                                        process_supervisor=supervisor, log_collector=Mock())

        def ready(sensors, healthcheck):
            healthcheck()
            partitions.append(os.environ.get("GZ_PARTITION"))

        with (patch.object(workflow, "validate_environment", return_value=[]),
              patch("spoof_test.workflow.wait_for_sensors", side_effect=ready),
              patch.object(workflow, "_save_flight_csv"),
              patch.dict(os.environ, {"GZ_PARTITION": "parent"}),
              redirect_stdout(StringIO())):
            self.assertEqual(workflow._run_once(MISSION_PLAN), 0)
            self.assertEqual(os.environ["GZ_PARTITION"], "parent")
            self.assertEqual(workflow._run_once(MISSION_PLAN), 0)
            self.assertEqual(os.environ["GZ_PARTITION"], "parent")
        self.assertEqual(len(set(partitions)), 2)
        self.assertTrue(all(p.startswith("spoof_gaz_") for p in partitions))


if __name__ == "__main__":
    unittest.main()
