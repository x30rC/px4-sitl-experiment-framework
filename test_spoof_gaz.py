#!/usr/bin/env python3
"""Unit-тесты чистой логики без запуска PX4 и Gazebo."""

import os
import sys
import unittest
from pathlib import Path

import g
import i
import run_test
from spoof_runtime import ActiveIdleSchedule
from spoof_test.gcs import GcsHeartbeatService
from spoof_test.mission import MavsdkMissionService
from spoof_test.paths import PROJECT_ROOT, PROJECT_VENV_PYTHON
from spoof_test.settings import GpsSpoofSettings, ImuSpoofSettings, SimulationTestSettings


class GpsConfigurationTests(unittest.TestCase):
    def test_intensity_interpolates_coordinates(self):
        settings = g.SpoofConfig(intensity_percent=50.0)
        self.assertEqual(
            tuple(round(value, 6) for value in settings.effective_coordinates),
            (47.3985, 8.5455, 494.0),
        )

    def test_default_full_topic_is_preserved(self):
        self.assertEqual(
            g.SpoofConfig().topic,
            "/world/default/model/x500_0/link/base_link/sensor/navsat_sensor/navsat",
        )

    def test_partial_periodic_schedule_is_rejected(self):
        with self.assertRaises(ValueError):
            g.SpoofConfig(active_seconds=5.0, idle_seconds=0.0).validate()


class ActiveIdleScheduleTests(unittest.TestCase):
    def test_periodic_phases(self):
        schedule = ActiveIdleSchedule(active_seconds=2.0, idle_seconds=3.0)
        self.assertTrue(schedule.is_active(0.0))
        self.assertTrue(schedule.is_active(1.999))
        self.assertFalse(schedule.is_active(2.0))
        self.assertFalse(schedule.is_active(4.999))
        self.assertTrue(schedule.is_active(5.0))

    def test_continuous_schedule_is_always_active(self):
        schedule = ActiveIdleSchedule()
        self.assertTrue(schedule.is_active(100_000.0))


class ImuConfigurationTests(unittest.TestCase):
    def test_default_topic_matches_px4_gz_bridge(self):
        self.assertEqual(
            i.ImuSpoofSettings().topic,
            "/world/default/model/x500_0/link/base_link/sensor/imu_sensor/imu",
        )

    def test_entity_name_uses_model_name(self):
        settings = i.ImuSpoofSettings(model_name="vehicle_7")
        self.assertEqual(settings.entity_name, "vehicle_7::base_link::imu_sensor")

    def test_intensity_scales_only_configured_offsets(self):
        settings = i.ImuSpoofSettings(
            intensity_percent=50.0,
            angular_velocity_offset=i.Vector3(0.2, -0.4, 1.0),
            linear_acceleration_offset=i.Vector3(2.0, 4.0, -6.0),
        )
        self.assertEqual(
            settings.effective_angular_velocity.as_tuple(),
            (0.1, -0.2, 0.5),
        )
        effective_acceleration = settings.effective_linear_acceleration
        self.assertAlmostEqual(effective_acceleration.x, 1.0)
        self.assertAlmostEqual(effective_acceleration.y, 2.0)
        self.assertAlmostEqual(effective_acceleration.z, 6.81)

    def test_default_gravity_is_positive_z_in_gazebo_flu(self):
        self.assertEqual(
            i.ImuSpoofSettings().effective_linear_acceleration.as_tuple(),
            (0.0, 0.0, 9.81),
        )

    def test_partial_periodic_schedule_is_rejected(self):
        with self.assertRaises(ValueError):
            i.ImuSpoofSettings(active_seconds=1.0, idle_seconds=0.0).validate()

    def test_baseline_message_has_no_spoof_offset(self):
        class Message:
            def __init__(self):
                from gz.msgs10.imu_pb2 import IMU
                self._message = IMU()

            def __new__(cls):
                from gz.msgs10.imu_pb2 import IMU
                return IMU()

        settings = i.ImuSpoofSettings(
            angular_velocity_offset=i.Vector3(0.0, 0.0, 0.8)
        )
        publisher = i.ImuSpoofPublisher(settings)
        baseline = publisher.build_message(Message, spoofed=False)
        spoofed = publisher.build_message(Message, spoofed=True)
        self.assertEqual(baseline.angular_velocity.z, 0.0)
        self.assertEqual(spoofed.angular_velocity.z, 0.8)


class ModularRunTestSettingsTests(unittest.TestCase):
    def test_command_contains_all_runtime_controls(self):
        settings = GpsSpoofSettings(
            intensity_percent=30.0,
            publish_rate_hz=20.0,
            active_seconds=2.0,
            idle_seconds=4.0,
            duration_seconds=12.0,
        )
        command = settings.build_command(Path("/tmp/python"))
        self.assertEqual(command[0], str(Path("/tmp/python")))
        self.assertEqual(command[command.index("--intensity") + 1], "30.0")
        self.assertEqual(command[command.index("--rate-hz") + 1], "20.0")
        self.assertEqual(command[command.index("--active-seconds") + 1], "2.0")
        self.assertEqual(command[command.index("--idle-seconds") + 1], "4.0")
        self.assertEqual(command[command.index("--duration") + 1], "12.0")

    def test_negative_test_delay_is_rejected(self):
        with self.assertRaises(ValueError):
            SimulationTestSettings(stabilization_delay=-1.0).validate()

    def test_imu_command_contains_runtime_controls(self):
        settings = ImuSpoofSettings(
            intensity_percent=85.0,
            publish_rate_hz=75.0,
            active_seconds=5.0,
            idle_seconds=4.0,
            duration_seconds=60.0,
            angular_velocity_offset_z=0.8,
        )
        command = settings.build_command(Path("/tmp/python"))
        self.assertEqual(command[command.index("--intensity") + 1], "85.0")
        self.assertEqual(command[command.index("--rate-hz") + 1], "75.0")
        self.assertEqual(command[command.index("--active-seconds") + 1], "5.0")
        self.assertEqual(command[command.index("--idle-seconds") + 1], "4.0")
        self.assertEqual(
            command[command.index("--angular-velocity-offset-z") + 1], "0.8"
        )
        self.assertEqual(command[command.index("--topic") + 1], "/spoof_gaz/imu")
        self.assertEqual(
            command[command.index("--source-topic") + 1],
            "/world/default/model/x500_0/link/base_link/sensor/imu_sensor/imu",
        )

    def test_sensor_selection_properties(self):
        gps = SimulationTestSettings(spoof_sensor="gps")
        imu = SimulationTestSettings(spoof_sensor="imu")
        both = SimulationTestSettings(spoof_sensor="both")
        self.assertTrue(gps.use_gps)
        self.assertFalse(gps.use_imu)
        self.assertFalse(imu.use_gps)
        self.assertTrue(imu.use_imu)
        self.assertTrue(both.use_gps and both.use_imu)

    def test_imu_baseline_has_zero_intensity_and_continuous_schedule(self):
        from dataclasses import replace

        configured = ImuSpoofSettings(
            intensity_percent=85.0,
            active_seconds=3.0,
            idle_seconds=2.0,
        )
        baseline = replace(
            configured,
            intensity_percent=0.0,
            active_seconds=0.0,
            idle_seconds=0.0,
        )
        self.assertEqual(baseline.intensity_percent, 0.0)
        self.assertFalse(baseline.is_periodic)

    def test_imu_activation_controls_are_added_to_command(self):
        settings = ImuSpoofSettings(
            wait_for_activation=True,
            post_baseline_seconds=2.0,
        )
        command = settings.build_command(Path("/tmp/python"))
        self.assertIn("--wait-for-activation", command)
        self.assertEqual(command[command.index("--post-baseline-seconds") + 1], "2.0")

    def test_old_import_names_still_point_to_new_classes(self):
        self.assertIs(run_test.SpoofLaunchConfig, GpsSpoofSettings)
        self.assertIs(run_test.TestConfig, SimulationTestSettings)


class MissionStartupTests(unittest.TestCase):
    def test_project_root_follows_the_imported_project_copy(self):
        self.assertEqual(PROJECT_ROOT, Path(__file__).resolve().parent)

    def test_existing_shared_venv_is_discovered(self):
        self.assertTrue(PROJECT_VENV_PYTHON.is_file())
        self.assertTrue(os.access(PROJECT_VENV_PYTHON, os.X_OK))

    def test_tests_are_running_in_discovered_venv(self):
        expected_prefix = PROJECT_VENV_PYTHON.parent.parent.resolve()
        self.assertEqual(Path(sys.prefix).resolve(), expected_prefix)

    def test_health_requires_armable_and_all_position_estimates(self):
        class Health:
            is_local_position_ok = True
            is_global_position_ok = True
            is_home_position_ok = True
            is_armable = False

        health = Health()
        self.assertFalse(MavsdkMissionService._health_is_ready(health))
        health.is_armable = True
        self.assertTrue(MavsdkMissionService._health_is_ready(health))

    def test_mavsdk_uses_non_deprecated_udp_input_url(self):
        service = MavsdkMissionService(10.0)
        self.assertEqual(service.system_address, "udpin://0.0.0.0:14540")

    def test_gcs_heartbeat_uses_px4_sitl_gcs_port(self):
        heartbeat = GcsHeartbeatService()
        self.assertEqual(heartbeat.endpoint, "udpout:127.0.0.1:18570")


if __name__ == "__main__":
    unittest.main()