"""Console menu and baseline-flight tests without launching the simulator."""

import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import Mock, patch

from spoof_test.cli import ANTENNA_ART, build_argument_parser, main, prompt_sensor, run_menu, show_menu
from spoof_test.settings import GpsSpoofSettings, SimulationTestSettings
from spoof_test.workflow import GpsSpoofTestWorkflow


class MenuTests(unittest.TestCase):
    def test_numeric_sensor_list_accepts_gps_and_mag(self):
        with patch("builtins.input", return_value="1,4"):
            self.assertEqual(prompt_sensor("gps"), "gps,mag")

    def test_antenna_menu_is_readable_without_terminal(self):
        with redirect_stdout(StringIO()) as output:
            show_menu()
        rendered = output.getvalue()
        self.assertIn(ANTENNA_ART, rendered)
        self.assertIn("/___|___\\", rendered)
        self.assertNotIn("S P O O F   G A Z", rendered)
        self.assertNotIn("sensor lab", rendered)
        self.assertIn("[1] Чистый полёт", rendered)
        self.assertIn("[2] Полёт с подменой", rendered)
        self.assertNotIn("\033[", rendered)

    def test_antenna_menu_color_only_on_terminal_without_no_color(self):
        output = StringIO()
        with (patch("spoof_test.cli.sys.stdout", output),
              patch.object(output, "isatty", return_value=True),
              patch.dict("spoof_test.cli.os.environ", {"TERM": "xterm-256color"}, clear=True)):
            show_menu()
        self.assertIn("\033[36m", output.getvalue())

        output = StringIO()
        with (patch("spoof_test.cli.sys.stdout", output),
              patch.object(output, "isatty", return_value=True),
              patch.dict("spoof_test.cli.os.environ", {"TERM": "xterm-256color", "NO_COLOR": "1"}, clear=True)):
            show_menu()
        self.assertNotIn("\033[", output.getvalue())

    def test_clean_menu_run_returns_to_menu_without_configuring_sensors(self):
        args = build_argument_parser().parse_args(["--dry-run", "--skip-mission"])
        with patch("builtins.input", side_effect=["1", "0"]), redirect_stdout(StringIO()) as output:
            self.assertEqual(run_menu(args), 0)
        self.assertIn("Чистый полёт: без publisher/relay", output.getvalue())
        self.assertNotIn("Команда gps_publisher.py", output.getvalue())

    def test_selected_sensors_menu_run(self):
        args = build_argument_parser().parse_args(["--dry-run", "--skip-mission"])
        with patch("builtins.input", side_effect=["2", "gps,baro", "2", "0"]), redirect_stdout(StringIO()) as output:
            self.assertEqual(run_menu(args), 0)
        self.assertIn("Команда gps_publisher.py", output.getvalue())
        self.assertIn("Команда baro:", output.getvalue())
        self.assertNotIn("Команда imu_publisher.py", output.getvalue())

    def test_eof_quits_menu(self):
        args = build_argument_parser().parse_args([])
        with patch("builtins.input", side_effect=EOFError), redirect_stdout(StringIO()):
            self.assertEqual(run_menu(args), 0)

    def test_noninteractive_clean_cli(self):
        with redirect_stdout(StringIO()) as output:
            self.assertEqual(main(["--non-interactive", "--dry-run", "--skip-mission",
                                   "--spoof-sensor", "none", "--baseline-duration", "12"]), 0)
        self.assertIn("наблюдение 12 сек", output.getvalue())

    def test_clean_workflow_waits_without_spoofing(self):
        supervisor = Mock()
        px4 = Mock()
        px4.poll.return_value = None
        supervisor.start_process.return_value = px4
        workflow = GpsSpoofTestWorkflow(
            SimulationTestSettings(spoof_sensor="none", baseline_duration=7, skip_mission=True,
                                   px4_startup_delay=0, stabilization_delay=0),
            GpsSpoofSettings(), process_supervisor=supervisor, log_collector=Mock(),
        )
        with (patch.object(workflow, "validate_environment", return_value=[]),
              patch("spoof_test.workflow.wait_for_sensors"),
              patch.object(workflow, "_save_flight_csv"),
              redirect_stdout(StringIO())):
            self.assertEqual(workflow.run(), 0)
        self.assertEqual(supervisor.start_process.call_count, 1)
        self.assertEqual(supervisor.start_process.call_args.args[0], "PX4 SITL")
        env = supervisor.start_process.call_args.kwargs["environment"]
        self.assertFalse(any(env.get(key) for key in ("PX4_GZ_IMU_TOPIC", "PX4_GZ_MAG_TOPIC", "PX4_GZ_BARO_TOPIC")))
        self.assertTrue(any(c.args[:2] == (7, "Чистый полёт без подмены датчиков")
                            for c in supervisor.wait_while_running.call_args_list))
        supervisor.wait_processes_while_running.assert_not_called()


if __name__ == "__main__":
    unittest.main()
