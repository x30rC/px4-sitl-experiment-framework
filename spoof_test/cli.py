"""CLI и интерактивная настройка полного GPS/IMU-теста."""

from __future__ import annotations

import argparse
import math
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from spoof_runtime import parse_finite_float

from .paths import PROJECT_VENV_PYTHON
from .settings import GpsSpoofSettings, ImuSpoofSettings, SimulationTestSettings
from .workflow import GpsSpoofTestWorkflow


def prompt_float(label: str, current: float, minimum: float, maximum: float) -> float:
    while True:
        raw_value = input(f"{label} [{current:g}]: ").strip()
        if not raw_value:
            return current
        try:
            value = float(raw_value.replace(",", "."))
        except ValueError:
            print("Введите число или нажмите Enter, чтобы оставить текущее значение.")
            continue
        if not math.isfinite(value) or not minimum <= value <= maximum:
            print(f"Допустимый диапазон: {minimum:g}..{maximum:g}.")
            continue
        return value


def prompt_yes_no(label: str, current: bool) -> bool:
    default = "y" if current else "n"
    while True:
        raw_value = input(f"{label} y/n [{default}]: ").strip().lower()
        if not raw_value:
            return current
        if raw_value in {"y", "yes", "д", "да"}:
            return True
        if raw_value in {"n", "no", "н", "нет"}:
            return False
        print("Введите y или n.")


def prompt_sensor(current: str) -> str:
    aliases = {"1": "gps", "2": "imu", "3": "both", "gps": "gps", "imu": "imu", "both": "both"}
    while True:
        raw_value = input(
            f"Тип подмены: 1=GPS, 2=IMU, 3=оба [{current}]: "
        ).strip().lower()
        if not raw_value:
            return current
        if raw_value in aliases:
            return aliases[raw_value]
        print("Введите 1, 2, 3, gps, imu или both.")


def configure_gps_interactively(settings: GpsSpoofSettings) -> GpsSpoofSettings:
    """Запрашивает основные параметры непосредственно перед тестом."""
    if not sys.stdin.isatty():
        print("Интерактивная настройка пропущена: stdin не является терминалом.")
        return settings

    print("\n=== Интерактивная настройка GPS-публикации ===")
    print("Enter оставляет текущее значение.")
    intensity = prompt_float(
        "Интенсивность смещения, %",
        settings.intensity_percent,
        0.0,
        1000.0,
    )
    publish_rate = prompt_float(
        "Частота публикации, Гц",
        settings.publish_rate_hz,
        0.1,
        1000.0,
    )
    duration = prompt_float(
        "Общая длительность, сек",
        settings.duration_seconds,
        0.1,
        86400.0,
    )

    periodic = prompt_yes_no("Включить периодический режим?", settings.is_periodic)
    if periodic:
        active_default = settings.active_seconds if settings.active_seconds > 0.0 else 5.0
        idle_default = settings.idle_seconds if settings.idle_seconds > 0.0 else 5.0
        active_seconds = prompt_float(
            "Длительность активной фазы, сек",
            active_default,
            0.1,
            86400.0,
        )
        idle_seconds = prompt_float(
            "Пауза между фазами, сек",
            idle_default,
            0.1,
            86400.0,
        )
    else:
        active_seconds = 0.0
        idle_seconds = 0.0

    configured_settings = replace(
        settings,
        intensity_percent=intensity,
        publish_rate_hz=publish_rate,
        duration_seconds=duration,
        active_seconds=active_seconds,
        idle_seconds=idle_seconds,
    )
    configured_settings.validate()
    return configured_settings


def configure_imu_interactively(settings: ImuSpoofSettings) -> ImuSpoofSettings:
    print("\n=== Интерактивная настройка IMU-публикации ===")
    intensity = prompt_float(
        "Интенсивность IMU-смещения, %", settings.intensity_percent, 0.0, 1000.0
    )
    publish_rate = prompt_float(
        "Частота IMU-публикации, Гц", settings.publish_rate_hz, 0.1, 2000.0
    )
    duration = prompt_float(
        "Общая длительность IMU, сек", settings.duration_seconds, 0.1, 86400.0
    )
    angular_x = prompt_float(
        "Смещение угловой скорости X, рад/с", settings.angular_velocity_offset_x, -1000.0, 1000.0
    )
    angular_y = prompt_float(
        "Смещение угловой скорости Y, рад/с", settings.angular_velocity_offset_y, -1000.0, 1000.0
    )
    angular_z = prompt_float(
        "Смещение угловой скорости Z, рад/с", settings.angular_velocity_offset_z, -1000.0, 1000.0
    )
    linear_x = prompt_float(
        "Смещение ускорения X, м/с²", settings.linear_acceleration_offset_x, -1000.0, 1000.0
    )
    linear_y = prompt_float(
        "Смещение ускорения Y, м/с²", settings.linear_acceleration_offset_y, -1000.0, 1000.0
    )
    linear_z = prompt_float(
        "Смещение ускорения Z, м/с²", settings.linear_acceleration_offset_z, -1000.0, 1000.0
    )
    periodic = prompt_yes_no("Включить периодический IMU-режим?", settings.is_periodic)
    if periodic:
        active_seconds = prompt_float(
            "Длительность активной IMU-фазы, сек",
            settings.active_seconds or 5.0,
            0.1,
            86400.0,
        )
        idle_seconds = prompt_float(
            "Пауза между IMU-фазами, сек",
            settings.idle_seconds or 5.0,
            0.1,
            86400.0,
        )
    else:
        active_seconds = idle_seconds = 0.0
    configured = replace(
        settings,
        intensity_percent=intensity,
        publish_rate_hz=publish_rate,
        duration_seconds=duration,
        angular_velocity_offset_x=angular_x,
        angular_velocity_offset_y=angular_y,
        angular_velocity_offset_z=angular_z,
        linear_acceleration_offset_x=linear_x,
        linear_acceleration_offset_y=linear_y,
        linear_acceleration_offset_z=linear_z,
        active_seconds=active_seconds,
        idle_seconds=idle_seconds,
    )
    configured.validate()
    return configured


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Запуск PX4 SITL, миссии и GPS/IMU-публикации.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    interaction_group = parser.add_mutually_exclusive_group()
    interaction_group.add_argument(
        "--interactive",
        dest="interactive",
        action="store_true",
        default=None,
        help="спросить основные параметры перед запуском",
    )
    interaction_group.add_argument(
        "--non-interactive",
        dest="interactive",
        action="store_false",
        default=None,
        help="использовать CLI-параметры без вопросов",
    )

    parser.add_argument(
        "--spoof-sensor",
        choices=("gps", "imu", "both"),
        default="gps",
        help="какие сенсорные данные подменять",
    )
    parser.add_argument(
        "--intensity",
        type=parse_finite_float,
        default=100.0,
        help="интенсивность смещения, %%",
    )
    parser.add_argument(
        "--rate-hz",
        type=parse_finite_float,
        default=100.0,
        help="частота g.py",
    )
    parser.add_argument("--active-seconds", type=parse_finite_float, default=0.0)
    parser.add_argument("--idle-seconds", type=parse_finite_float, default=0.0)
    parser.add_argument(
        "--duration",
        type=parse_finite_float,
        default=60.0,
        help="длительность g.py",
    )

    parser.add_argument("--reference-lat", type=parse_finite_float, default=47.398)
    parser.add_argument("--reference-lon", type=parse_finite_float, default=8.545)
    parser.add_argument("--reference-alt", type=parse_finite_float, default=488.0)
    parser.add_argument("--target-lat", type=parse_finite_float, default=47.399)
    parser.add_argument("--target-lon", type=parse_finite_float, default=8.546)
    parser.add_argument("--target-alt", type=parse_finite_float, default=500.0)

    parser.add_argument("--imu-intensity", type=parse_finite_float, default=100.0)
    parser.add_argument("--imu-rate-hz", type=parse_finite_float, default=250.0)
    parser.add_argument("--imu-active-seconds", type=parse_finite_float, default=0.0)
    parser.add_argument("--imu-idle-seconds", type=parse_finite_float, default=0.0)
    parser.add_argument("--imu-duration", type=parse_finite_float, default=60.0)
    parser.add_argument("--imu-angular-x", type=parse_finite_float, default=0.0)
    parser.add_argument("--imu-angular-y", type=parse_finite_float, default=0.0)
    parser.add_argument("--imu-angular-z", type=parse_finite_float, default=0.5)
    parser.add_argument("--imu-linear-x", type=parse_finite_float, default=0.0)
    parser.add_argument("--imu-linear-y", type=parse_finite_float, default=0.0)
    parser.add_argument("--imu-linear-z", type=parse_finite_float, default=0.0)

    parser.add_argument("--px4-startup-delay", type=parse_finite_float, default=15.0)
    parser.add_argument("--stabilization-delay", type=parse_finite_float, default=10.0)
    parser.add_argument("--wait-after-spoof", type=parse_finite_float, default=5.0)
    parser.add_argument("--mission-timeout", type=parse_finite_float, default=45.0)
    parser.add_argument("--skip-mission", action="store_true")
    parser.add_argument(
        "--skip-px4",
        action="store_true",
        help="не запускать PX4; использовать уже запущенный SITL",
    )
    parser.add_argument("--dry-run", action="store_true", help="проверить настройки без запуска")
    return parser


def restart_in_project_venv_if_needed() -> None:
    """Подключает существующий Linux venv, не меняя пути проекта."""
    if os.name == "nt":
        return
    if not PROJECT_VENV_PYTHON.is_file() or not os.access(PROJECT_VENV_PYTHON, os.X_OK):
        return

    # Нельзя сравнивать ``sys.executable`` через resolve(): python в venv обычно
    # является symlink на /usr/bin/python3, и системный Python ошибочно выглядит
    # тем же интерпретатором. ``sys.prefix`` надёжно указывает активный venv.
    expected_prefix = PROJECT_VENV_PYTHON.parent.parent.resolve()
    if Path(sys.prefix).resolve() == expected_prefix:
        return

    if os.environ.get("SPOOF_GAZ_VENV_RESTARTED") == "1":
        raise RuntimeError(
            "автоперезапуск через venv не активировал ожидаемое окружение: "
            f"sys.prefix={sys.prefix}, ожидалось {expected_prefix}"
        )

    environment = os.environ.copy()
    environment["SPOOF_GAZ_VENV_RESTARTED"] = "1"
    try:
        os.execve(
            str(PROJECT_VENV_PYTHON),
            [
                str(PROJECT_VENV_PYTHON),
                "-u",
                str(Path(sys.argv[0]).resolve()),
                *sys.argv[1:],
            ],
            environment,
        )
    except OSError as exc:
        raise RuntimeError(
            f"не удалось перезапустить сценарий через venv {PROJECT_VENV_PYTHON}: {exc}"
        ) from exc


def settings_from_arguments(
    args: argparse.Namespace,
) -> tuple[GpsSpoofSettings, ImuSpoofSettings, SimulationTestSettings]:
    gps_settings = GpsSpoofSettings(
        intensity_percent=args.intensity,
        publish_rate_hz=args.rate_hz,
        active_seconds=args.active_seconds,
        idle_seconds=args.idle_seconds,
        duration_seconds=args.duration,
        reference_latitude=args.reference_lat,
        reference_longitude=args.reference_lon,
        reference_altitude=args.reference_alt,
        target_latitude=args.target_lat,
        target_longitude=args.target_lon,
        target_altitude=args.target_alt,
    )
    imu_settings = ImuSpoofSettings(
        intensity_percent=args.imu_intensity,
        publish_rate_hz=args.imu_rate_hz,
        active_seconds=args.imu_active_seconds,
        idle_seconds=args.imu_idle_seconds,
        duration_seconds=args.imu_duration,
        angular_velocity_offset_x=args.imu_angular_x,
        angular_velocity_offset_y=args.imu_angular_y,
        angular_velocity_offset_z=args.imu_angular_z,
        linear_acceleration_offset_x=args.imu_linear_x,
        linear_acceleration_offset_y=args.imu_linear_y,
        linear_acceleration_offset_z=args.imu_linear_z,
    )
    interactive = args.interactive
    if interactive is None:
        interactive = sys.stdin.isatty() and not args.dry_run

    test_settings = SimulationTestSettings(
        px4_startup_delay=args.px4_startup_delay,
        stabilization_delay=args.stabilization_delay,
        wait_after_spoof=args.wait_after_spoof,
        mission_timeout=args.mission_timeout,
        skip_mission=args.skip_mission,
        skip_px4=args.skip_px4,
        interactive=interactive,
        dry_run=args.dry_run,
        spoof_sensor=args.spoof_sensor,
    )
    return gps_settings, imu_settings, test_settings


def main(argv: Sequence[str] | None = None) -> int:
    restart_in_project_venv_if_needed()
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    try:
        gps_settings, imu_settings, test_settings = settings_from_arguments(args)
        if test_settings.interactive:
            sensor = prompt_sensor(test_settings.spoof_sensor)
            test_settings = replace(test_settings, spoof_sensor=sensor)
            if test_settings.use_gps:
                gps_settings = configure_gps_interactively(gps_settings)
            if test_settings.use_imu:
                imu_settings = configure_imu_interactively(imu_settings)
        return GpsSpoofTestWorkflow(test_settings, gps_settings, imu_settings).run()
    except ValueError as exc:
        parser.error(str(exc))
    return 2