#!/usr/bin/env python3
"""Точка входа полного GPS/IMU-теста PX4 SITL + Gazebo Harmonic."""

from __future__ import annotations

import sys

from spoof_test import (
    GpsSpoofSettings,
    GpsSpoofTestWorkflow,
    ImuSpoofSettings,
    ProcessSupervisor,
    SimulationTestSettings,
)
from spoof_test.cli import main


# Обратная совместимость с импортами предыдущей версии. Новый код должен
# использовать предметные имена из пакета ``spoof_test``.
SpoofLaunchConfig = GpsSpoofSettings
TestConfig = SimulationTestSettings
ProcessManager = ProcessSupervisor
TestRunner = GpsSpoofTestWorkflow


if __name__ == "__main__":
    sys.exit(main())