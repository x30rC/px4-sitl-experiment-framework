"""Проверка выбора часов и фаз для всех датчиков без запуска SITL."""

import unittest
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gz.msgs10.imu_pb2 import IMU
from gz.msgs10.fluid_pressure_pb2 import FluidPressure

from gps_publisher import GpsSpoofer, SpoofConfig
from imu_publisher import ImuSpoofPublisher, ImuSpoofSettings, Vector3
from sensor_relay import Relay, parser as relay_parser
from spoof_test.cli import build_argument_parser, main, run_menu
from spoof_test.sensor_settings import RelaySettings
from spoof_test.settings import GpsSpoofSettings, SimulationTestSettings
from spoof_test.sim_clock import SimulationClock
from spoof_runtime import ActiveIdleSchedule


class ModeTests(unittest.TestCase):
    def test_selection_reaches_every_publisher(self):
        for mode in ("sync", "async"):
            with self.subTest(mode=mode), redirect_stdout(StringIO()) as output:
                self.assertEqual(main(["--non-interactive", "--dry-run", "--skip-mission",
                                       "--spoof-sensor", "gps,imu,mag,baro", "--time-mode", mode]), 0)
            commands = [line for line in output.getvalue().splitlines() if line.startswith("Команда ")]
            self.assertEqual(len(commands), 4)
            for command in commands:
                self.assertIn(f"--time-mode {mode}", command)

    def test_invalid_mode_rejected_by_settings(self):
        for settings in (SimulationTestSettings(time_mode="other"), GpsSpoofSettings(time_mode="other"),
                         RelaySettings("mag", time_mode="other")):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                settings.validate()

    def test_menu_mode_is_passed_to_workflow(self):
        args = build_argument_parser().parse_args(["--dry-run", "--skip-mission"])
        with patch("builtins.input", side_effect=["2", "gps,baro", "1", "0"]), redirect_stdout(StringIO()) as output:
            self.assertEqual(run_menu(args), 0)
        self.assertIn("Режим атаки: синхронный", output.getvalue())
        self.assertIn("--time-mode sync", output.getvalue())

    def test_relay_phase_progression_uses_selected_clock(self):
        for mode, expected in (("sync", [100100, 100100, 100100]),
                               ("async", [100100, 100000, 100100])):
            args = relay_parser().parse_args(["baro", "--source-topic", "/in", "--topic", "/out",
                                               "--time-mode", mode, "--duration", "3",
                                               "--active-seconds", "1", "--idle-seconds", "1",
                                               "--pressure-offset", "100"])
            relay = Relay(args)
            relay.publisher = Mock()
            message = FluidPressure()
            message.pressure = 100000
            with patch("sensor_relay.time.monotonic", side_effect=[10, 11.1, 12.1]), redirect_stdout(StringIO()):
                relay.activate()
                for stamp in (20, 20, 22):
                    message.header.stamp.sec = stamp
                    relay.callback(message)
            values = [c.args[0].pressure for c in relay.publisher.publish.call_args_list]
            self.assertEqual(values, expected, mode)

    def test_imu_relay_phase_progression_uses_selected_clock(self):
        for mode, expected in (("sync", [0.5, 0.5]), ("async", [0.5, 0, 0.5])):
            settings = ImuSpoofSettings(source_topic="/in", time_mode=mode, duration_seconds=3,
                                        active_seconds=1, idle_seconds=1, publish_rate_hz=100,
                                        angular_velocity_offset=Vector3(0, 0, 0.5))
            publisher = ImuSpoofPublisher(settings)
            publisher._relay_publisher = Mock()
            publisher._relay_message_type = IMU
            publisher._relay_schedule = ActiveIdleSchedule(1, 1)
            publisher.activate_attack()
            message = IMU()
            with patch("imu_publisher.time.monotonic", side_effect=[10, 11.1, 12.1]), redirect_stdout(StringIO()):
                for stamp in (20, 22, 24):
                    message.header.stamp.sec = stamp
                    publisher._relay_callback(message)
            values = [c.args[0].angular_velocity.z for c in publisher._relay_publisher.publish.call_args_list]
            self.assertEqual(values, expected, mode)

    def test_gps_clock_callback_only_moves_forward(self):
        publisher = GpsSpoofer(SpoofConfig(time_mode="sync"))
        def message(sec):
            return SimpleNamespace(sim=SimpleNamespace(sec=sec, nsec=0))
        publisher._clock_callback(message(10))
        publisher._clock_callback(message(9))
        self.assertEqual(publisher._clock_time, 10)
        publisher._clock_callback(message(12))
        self.assertEqual(publisher._clock_time, 12)

    def test_sim_clock_waits_for_simulated_time_not_wall_time(self):
        clock = SimulationClock.__new__(SimulationClock)
        from threading import Event, Lock
        clock._lock = Lock()
        clock._event = Event()
        clock._value = 10.0
        process = Mock(pid=42)
        process.poll.return_value = None
        changes = iter([10.0, 11.0, 12.1])
        def tick():
            clock._on_clock(SimpleNamespace(sim=SimpleNamespace(sec=int(value := next(changes)),
                                                              nsec=int((value % 1) * 1e9))))
        clock._event.wait = lambda timeout: tick()
        clock.wait(2.0, [(process, 2.0)], stall_timeout=15.0)
        self.assertGreaterEqual(process.poll.call_count, 2)

    def test_sim_clock_fails_on_stalled_clock(self):
        from threading import Event, Lock
        clock = SimulationClock.__new__(SimulationClock)
        clock._lock = Lock()
        clock._event = Event()
        clock._value = 10.0
        process = Mock(pid=42)
        process.poll.return_value = None
        with self.assertRaisesRegex(RuntimeError, "clock остановился"):
            clock.wait(2.0, [(process, 2.0)], stall_timeout=0.01)


if __name__ == "__main__":
    unittest.main()
