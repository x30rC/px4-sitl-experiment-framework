"""Пути PX4 и текущей копии проекта."""

import os
from pathlib import Path


HOME_DIRECTORY = Path.home()
PX4_ROOT = HOME_DIRECTORY / "PX4-Autopilot"
# Не привязываемся к имени каталога: поддерживаются как ``spoof_gaz``, так и
# ``_spoof_gaz`` и другие копии проекта.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
GPS_SPOOF_SCRIPT = PROJECT_ROOT / "g.py"
IMU_SPOOF_SCRIPT = PROJECT_ROOT / "i.py"
MISSION_PLAN = PROJECT_ROOT / "mission.plan"
LOG_DESTINATION = PROJECT_ROOT / "logs"


def _find_project_python() -> Path:
    candidates = (
        PROJECT_ROOT / "venv" / "bin" / "python",
        HOME_DIRECTORY / "spoof_gaz" / "venv" / "bin" / "python",
        HOME_DIRECTORY / "_spoof_gaz" / "venv" / "bin" / "python",
    )
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    # Возвращаем ожидаемый локальный путь, чтобы диагностика показывала, где
    # создать окружение, если ни одного готового venv нет.
    return candidates[0]


PROJECT_VENV_PYTHON = _find_project_python()
PX4_LOG_SOURCE = PX4_ROOT / "build" / "px4_sitl_default" / "rootfs" / "log"