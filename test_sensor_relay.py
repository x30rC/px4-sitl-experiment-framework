"""Tests for live Gazebo sensor relays without starting SITL."""

import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch
from contextlib import redirect_stdout

from gz.msgs10.magnetometer_pb2 import Magnetometer
from gz.msgs10.fluid_pressure_pb2 import FluidPressure

from sensor_relay import Relay, modify, parser
from spoof_test.sensor_settings import RelaySettings
from spoof_test.settings import SimulationTestSettings
from spoof_test.settings import GpsSpoofSettings
from spoof_test.workflow import GpsSpoofTestWorkflow
from spoof_test.cli import build_argument_parser, configure_relay_interactively, main


class SensorRelayTests(unittest.TestCase):
    def test_magnetic_offset_copies_and_scales(self):
        original = Magnetometer()
        original.field_tesla.x = 0.00001
        original.header.stamp.sec = 5
        result = modify(original, "mag", (0.00002, 0, 0), 50)
        self.assertAlmostEqual(result.field_tesla.x, 0.00002)
        self.assertEqual(original.field_tesla.x, 0.00001)
        self.assertEqual(result.header.stamp.sec, 5)

    def test_pressure_offset(self):
        original = FluidPressure()
        original.pressure = 101325
        result = modify(original, "baro", (200,), 50)
        self.assertEqual(result.pressure, 101425)
        self.assertEqual(original.pressure, 101325)
        with self.assertRaises(ValueError):
            modify(original, "baro", (-200000,), 100)

    def test_activation_and_periodic_baseline(self):
        args = parser().parse_args(["baro", "--source-topic", "/src", "--topic", "/dst",
                                    "--pressure-offset", "100", "--duration", "5",
                                    "--active-seconds", "2", "--idle-seconds", "2"])
        relay = Relay(args)
        relay.publisher = Mock()
        source = FluidPressure()
        source.pressure = 100000
        source.header.stamp.sec = 10
        relay.callback(source)
        self.assertEqual(relay.publisher.publish.call_args.args[0].pressure, 100000)
        relay.activate()
        for stamp in (11, 14, 16):
            source.header.stamp.sec = stamp
            relay.callback(source)
        values = [call.args[0].pressure for call in relay.publisher.publish.call_args_list]
        self.assertEqual(values, [100000, 100100, 100000, 100000])

    def test_settings_and_commands(self):
        settings = SimulationTestSettings(spoof_sensor="gps,mag,baro")
        settings.validate()
        self.assertTrue(settings.use_gps and settings.use_mag and settings.use_baro)
        self.assertFalse(settings.use_imu)
        self.assertTrue(SimulationTestSettings(spoof_sensor="both").use_imu)
        for sensor in ("mag", "baro"):
            config = RelaySettings(sensor)
            config.validate()
            self.assertIn(config.source_topic, config.build_command(Path("/usr/bin/python3")))

    def test_workflow_dry_run_mag_and_baro(self):
        workflow = GpsSpoofTestWorkflow(
            SimulationTestSettings(spoof_sensor="mag,baro", dry_run=True, skip_mission=True),
            GpsSpoofSettings(),
            relay_settings={"mag": RelaySettings("mag", offsets=(0.0, 0.0, 0.00002)),
                            "baro": RelaySettings("baro", offsets=(150.0,))},
        )
        self.assertEqual(workflow.run(), 0)

    def test_interactive_baro_configures_nonzero_offset_and_periodic_schedule(self):
        settings = RelaySettings("baro", offsets=(100.0,))
        with patch("builtins.input", side_effect=["85", "25", "150", "y", "5", "4"]):
            configured = configure_relay_interactively(settings)
        self.assertEqual(configured.intensity_percent, 85)
        self.assertEqual(configured.duration_seconds, 25)
        self.assertEqual(configured.effective_offsets, (150,))
        self.assertEqual((configured.active_seconds, configured.idle_seconds), (5, 4))
        command = configured.build_command(Path("/tmp/python"))
        self.assertEqual(float(command[command.index("--pressure-offset") + 1]), 150)
        self.assertEqual(float(command[command.index("--idle-seconds") + 1]), 4)

    def test_interactive_magnetometer_parameters(self):
        with patch("builtins.input", side_effect=["70", "30", "0", "0", "0.00002", "y", "3", "2"]):
            settings = configure_relay_interactively(RelaySettings("mag"))
        self.assertEqual(settings.effective_offsets, (0, 0, 0.00002))
        self.assertEqual((settings.active_seconds, settings.idle_seconds), (3, 2))

    def test_cli_baro_default_is_nonzero(self):
        args = build_argument_parser().parse_args([])
        self.assertEqual(args.baro_pressure_offset, 100)
        self.assertEqual(args.mag_z, 0.00002)
        self.assertEqual(RelaySettings("baro").effective_offsets, (100,))
        self.assertEqual(RelaySettings("mag").effective_offsets, (0, 0, 0.00002))

    def test_cli_passes_interactive_settings_to_workflow(self):
        with (patch("spoof_test.cli.restart_in_project_venv_if_needed"),
              patch("spoof_test.cli.prompt_sensor", return_value="baro"),
              patch("builtins.input", side_effect=["80", "12", "200", "y", "2", "3"]),
              patch("spoof_test.cli.GpsSpoofTestWorkflow") as workflow,
              redirect_stdout(StringIO())):
            workflow.return_value.run.return_value = 0
            self.assertEqual(main(["--interactive"]), 0)
        settings = workflow.call_args.kwargs["relay_settings"]["baro"]
        self.assertEqual(settings.effective_offsets, (200,))
        self.assertEqual((settings.active_seconds, settings.idle_seconds), (2, 3))

    def test_relay_activation_is_reported_and_applies_nonzero_pressure(self):
        args = parser().parse_args(["baro", "--source-topic", "/src", "--topic", "/dst",
                                    "--pressure-offset", "100", "--duration", "5"])
        relay = Relay(args)
        relay.publisher = Mock()
        source = FluidPressure()
        source.pressure = 100000
        source.header.stamp.sec = 11
        with redirect_stdout(StringIO()) as output:
            relay.activate()
            relay.callback(source)
        self.assertEqual(relay.spoofed, 1)
        self.assertEqual(relay.publisher.publish.call_args.args[0].pressure, 100100)
        self.assertIn("первое подменённое", output.getvalue())


if __name__ == "__main__":
    unittest.main()
