"""Проверки обработки ULog без запуска PX4 или реального конвертера."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import consolidate_logs
from spoof_test.dataset import build_analysis_csv, horizontal_distance_m, matching_topic


class ConsolidateLogsTests(unittest.TestCase):
    def setUp(self):
        # Matplotlib ищет системные шрифты через subprocess при первом импорте.
        from matplotlib.backends.backend_agg import FigureCanvasAgg

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.logs = Path(self.directory.name) / "logs"
        self.logs.mkdir()
        self.folder = self.logs / "flight"
        self.folder.mkdir()
        self.ulog = self.folder / "flight.ulg"
        self.ulog.write_bytes(b"original")

    def test_default_points_to_current_project(self):
        self.assertEqual(consolidate_logs.LOGS_BASE, Path(__file__).resolve().parent / "logs")

    def test_selection_rejects_path_traversal_and_symlinks(self):
        (self.logs / "link").symlink_to(self.folder, target_is_directory=True)
        self.assertEqual(consolidate_logs.select_folders(self.logs, None), [self.folder])
        for name in ("..", "../flight", "/tmp", "link", "missing"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                consolidate_logs.select_folders(self.logs, [name])

    def test_dry_run_creates_nothing_and_does_not_require_dependencies(self):
        with patch.object(consolidate_logs.subprocess, "run") as run:
            self.assertEqual(consolidate_logs.main([
                "--logs-dir", str(self.logs), "--folder", "flight", "--dry-run",
            ]), 0)
            run.assert_not_called()
        self.assertEqual(list(self.logs.iterdir()), [self.folder])
        self.assertEqual(self.ulog.read_bytes(), b"original")

    def test_symlinked_ulog_is_not_processed(self):
        self.ulog.unlink()
        (self.folder / "linked.ulg").symlink_to("/tmp/external.ulg")
        self.assertEqual(consolidate_logs.main([
            "--logs-dir", str(self.logs), "--folder", "flight", "--dry-run",
        ]), 1)

    def test_converts_only_generated_matching_topics_and_preserves_ulog(self):
        # Старый CSV рядом с ULog не должен оказаться в результате.
        (self.folder / "old_sensor_gps.csv").write_text("value\n999\n")

        def fake_convert(command, **kwargs):
            self.assertEqual(command[0], "ulog2csv")
            target = Path(command[command.index("-o") + 1])
            (target / "flight_vehicle_gps_position_0.csv").write_text("timestamp,latitude_deg,longitude_deg,value\n1000000,47.0,8.0,1\n")
            (target / "flight_unrelated_0.csv").write_text("value\n2\n")

        with patch.object(consolidate_logs.shutil, "which", return_value="ulog2csv"), \
             patch.object(consolidate_logs.subprocess, "run", side_effect=fake_convert):
            self.assertTrue(consolidate_logs.process_folder(self.folder, self.logs))
        output = self.logs / "flight_flight.csv"
        self.assertIn("vehicle_gps_position.latitude_deg", output.read_text())
        self.assertNotIn("999", output.read_text())
        self.assertNotIn("unrelated", output.read_text())
        self.assertEqual(self.ulog.read_bytes(), b"original")
        self.assertTrue((self.folder / "old_sensor_gps.csv").exists())
        self.assertEqual(sorted(p.name for p in self.logs.glob("*.csv")), ["flight_flight.csv"])
        self.assertEqual(sorted(p.name for p in self.logs.glob("*.png")),
                         [f"flight_flight_{kind}.png" for kind in sorted(("altitude", "diagnostics", "trajectory"))])
        for image in self.logs.glob("*.png"):
            self.assertEqual(image.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        with patch.object(consolidate_logs.subprocess, "run") as run:
            self.assertFalse(consolidate_logs.process_folder(self.folder, self.logs))
            run.assert_not_called()

    def test_failed_conversion_leaves_no_output_or_changes_to_ulog(self):
        import subprocess

        with patch.object(consolidate_logs.shutil, "which", return_value="ulog2csv"), \
             patch.object(consolidate_logs.subprocess, "run",
                          side_effect=subprocess.CalledProcessError(1, "ulog2csv")):
            self.assertEqual(consolidate_logs.main([
                "--logs-dir", str(self.logs), "--folder", "flight",
            ]), 1)
        self.assertEqual(self.ulog.read_bytes(), b"original")
        self.assertFalse((self.logs / "flight_flight.csv").exists())

    def test_analysis_uses_only_past_fresh_samples_and_ground_truth(self):
        import pandas as pd

        gps = self.folder / "flight_vehicle_gps_position_0.csv"
        gps.write_text("timestamp,latitude_deg,longitude_deg,altitude_msl_m\n"
                       "1000000,47.0,8.0,500\n2000000,47.001,8.0,502\n")
        truth = self.folder / "flight_vehicle_global_position_groundtruth_0.csv"
        truth.write_text("timestamp,lat,lon,alt\n1000000,47.0,8.0,499\n")
        output = self.logs / "analysis.csv"
        self.assertTrue(build_analysis_csv([gps, truth], "flight", output,
                                           sample_ms=500, max_age_ms=500))
        rows = pd.read_csv(output)
        self.assertEqual(list(rows.timestamp_us), [1000000, 1500000, 2000000])
        self.assertEqual(list(rows["vehicle_gps_position.age_ms"]), [0, 500, 0])
        self.assertEqual(rows.loc[0, "gps_vs_truth.horizontal_error_m"], 0)
        self.assertEqual(rows.loc[0, "gps_vs_truth.altitude_error_m"], 1)
        self.assertTrue(pd.isna(rows.loc[2, "gps_vs_truth.horizontal_error_m"]))
        self.assertTrue(pd.isna(rows.loc[2, "vehicle_global_position_groundtruth.lat"]))
        self.assertGreater(horizontal_distance_m(47, 8, 47.001, 8), 100)

    def test_analysis_does_not_use_other_ulog_or_topic_prefix(self):
        self.assertEqual(matching_topic("flight_vehicle_gps_position_0", "flight"),
                         ("vehicle_gps_position", 0))
        for name in ("other_vehicle_gps_position_0", "flight_vehicle_gps_position_1",
                     "flight_vehicle_gps_position_groundtruth_0"):
            self.assertIsNone(matching_topic(name, "flight"))

    def test_analysis_rejects_invalid_sampling(self):
        with self.assertRaises(ValueError):
            build_analysis_csv([], "flight", self.logs / "bad.csv", sample_ms=0)

    def test_missing_analysis_topic_does_not_leave_partial_outputs(self):
        def convert(command, **kwargs):
            target = Path(command[command.index("-o") + 1])
            (target / "flight_home_position_0.csv").write_text("timestamp,lat\n1000000,47\n")

        with patch.object(consolidate_logs.shutil, "which", return_value="ulog2csv"), \
             patch.object(consolidate_logs.subprocess, "run", side_effect=convert):
            self.assertFalse(consolidate_logs.process_folder(self.folder, self.logs))
        self.assertEqual(list(self.logs.iterdir()), [self.folder])

    def test_two_ulogs_have_two_independent_csv_files(self):
        import pandas as pd

        (self.folder / "second.ulg").write_bytes(b"second")

        def convert(command, **kwargs):
            target = Path(command[command.index("-o") + 1])
            stem = Path(command[1]).stem
            (target / f"{stem}_vehicle_gps_position_0.csv").write_text(
                f"timestamp,latitude_deg,longitude_deg\n1000000,{47 if stem == 'flight' else 48},8\n"
            )

        with patch.object(consolidate_logs.shutil, "which", return_value="ulog2csv"), \
             patch.object(consolidate_logs.subprocess, "run", side_effect=convert):
            self.assertTrue(consolidate_logs.process_folder(self.folder, self.logs))
        first = pd.read_csv(self.logs / "flight_flight.csv")
        second = pd.read_csv(self.logs / "flight_second.csv")
        self.assertEqual(first["vehicle_gps_position.latitude_deg"].tolist(), [47])
        self.assertEqual(second["vehicle_gps_position.latitude_deg"].tolist(), [48])
        self.assertEqual(sorted(p.name for p in self.logs.glob("*.csv")),
                         ["flight_flight.csv", "flight_second.csv"])
        self.assertEqual(len(list(self.logs.glob("*.png"))), 6)

    def test_existing_analysis_prevents_partial_regeneration(self):
        output = self.logs / "flight_flight.csv"
        output.write_text("do not overwrite")
        with patch.object(consolidate_logs.subprocess, "run") as run:
            self.assertFalse(consolidate_logs.process_folder(self.folder, self.logs))
            run.assert_not_called()
        self.assertEqual(output.read_text(), "do not overwrite")

    def test_existing_image_prevents_regeneration_without_overwrite(self):
        image = self.logs / "flight_flight_trajectory.png"
        image.write_bytes(b"existing")
        with patch.object(consolidate_logs.subprocess, "run") as run:
            self.assertFalse(consolidate_logs.process_folder(self.folder, self.logs))
            run.assert_not_called()
        self.assertEqual(image.read_bytes(), b"existing")
        self.assertFalse((self.logs / "flight_flight.csv").exists())

    def test_plot_failure_does_not_publish_csv_or_images(self):
        def convert(command, **kwargs):
            target = Path(command[command.index("-o") + 1])
            (target / "flight_vehicle_gps_position_0.csv").write_text(
                "timestamp,latitude_deg,longitude_deg\n1000000,47,8\n")

        with patch.object(consolidate_logs.shutil, "which", return_value="ulog2csv"), \
             patch.object(consolidate_logs.subprocess, "run", side_effect=convert), \
             patch.object(consolidate_logs, "render_flight_plots", side_effect=ValueError("bad image")):
            self.assertFalse(consolidate_logs.process_folder(self.folder, self.logs))
        self.assertEqual(list(self.logs.iterdir()), [self.folder])
        self.assertTrue(self.ulog.is_file())


if __name__ == "__main__":
    unittest.main()
