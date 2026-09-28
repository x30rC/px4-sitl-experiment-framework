"""Высокоуровневый сценарий GPS/IMU-теста с PX4 SITL и Gazebo."""

from __future__ import annotations

import os
import importlib.util
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

from .logs import Px4LogCollector
from .gcs import GcsHeartbeatService
from .mission import MavsdkMissionService
from .paths import (
    GPS_SPOOF_SCRIPT,
    IMU_SPOOF_SCRIPT,
    LOG_DESTINATION,
    MISSION_PLAN,
    PROJECT_VENV_PYTHON,
    PX4_ROOT,
)
from .processes import ProcessSupervisor
from .settings import GpsSpoofSettings, ImuSpoofSettings, SimulationTestSettings


class GpsSpoofTestWorkflow:
    """Координирует PX4, миссию, сенсорные publisher и сохранение ULog."""

    def __init__(
        self,
        test_settings: SimulationTestSettings,
        gps_settings: GpsSpoofSettings,
        imu_settings: ImuSpoofSettings | None = None,
        *,
        process_supervisor: ProcessSupervisor | None = None,
        mission_service: MavsdkMissionService | None = None,
        gcs_heartbeat: GcsHeartbeatService | None = None,
        log_collector: Px4LogCollector | None = None,
    ) -> None:
        test_settings.validate()
        if test_settings.use_gps:
            gps_settings.validate()
        if test_settings.use_imu:
            (imu_settings or ImuSpoofSettings()).validate()
        self.test_settings = test_settings
        self.gps_settings = gps_settings
        self.imu_settings = imu_settings or ImuSpoofSettings(
            duration_seconds=gps_settings.duration_seconds
        )
        self.process_supervisor = process_supervisor or ProcessSupervisor()
        self.mission_service = mission_service or MavsdkMissionService(
            test_settings.mission_timeout
        )
        self.gcs_heartbeat = gcs_heartbeat or GcsHeartbeatService()
        self.log_collector = log_collector or Px4LogCollector()
        self.started_at_epoch = time.time()

    @staticmethod
    def validate_environment(
        require_px4: bool = True,
        require_mission_dependencies: bool = True,
        require_gps: bool = True,
        require_imu: bool = False,
    ) -> list[str]:
        errors: list[str] = []
        if os.name == "nt":
            errors.append(
                "run_test.py нужно запускать внутри Ubuntu 24.04 WSL, "
                "а не Windows Python"
            )
        if require_px4 and not PX4_ROOT.is_dir():
            errors.append(f"папка PX4 не найдена: {PX4_ROOT}")
        if require_gps and not GPS_SPOOF_SCRIPT.is_file():
            errors.append(f"GPS-скрипт не найден: {GPS_SPOOF_SCRIPT}")
        if require_imu and not IMU_SPOOF_SCRIPT.is_file():
            errors.append(f"IMU-скрипт не найден: {IMU_SPOOF_SCRIPT}")
        if not shutil.which("python3"):
            errors.append("команда python3 не найдена")
        if require_px4 and not shutil.which("make"):
            errors.append("команда make не найдена")
        if require_mission_dependencies:
            for module_name in ("mavsdk", "pymavlink"):
                if importlib.util.find_spec(module_name) is None:
                    errors.append(
                        f"Python-модуль {module_name} не найден в {sys.executable}; "
                        f"ожидался venv {PROJECT_VENV_PYTHON}"
                    )
        return errors

    @staticmethod
    def project_python() -> Path:
        if PROJECT_VENV_PYTHON.is_file() and os.access(PROJECT_VENV_PYTHON, os.X_OK):
            return PROJECT_VENV_PYTHON
        return Path(sys.executable)

    def _start_gps_publisher(self) -> subprocess.Popen:
        return self.process_supervisor.start_process(
            "GPS publisher",
            self.gps_settings.build_command(self.project_python()),
            working_directory=GPS_SPOOF_SCRIPT.parent,
        )

    def _start_imu_publisher(
        self,
        settings: ImuSpoofSettings | None = None,
        name: str = "IMU publisher",
    ) -> subprocess.Popen:
        selected_settings = settings or self.imu_settings
        return self.process_supervisor.start_process(
            name,
            selected_settings.build_command(self.project_python()),
            working_directory=IMU_SPOOF_SCRIPT.parent,
        )

    def _start_imu_baseline(self) -> subprocess.Popen:
        longest_attack = max(
            self.imu_settings.duration_seconds,
            self.gps_settings.duration_seconds if self.test_settings.use_gps else 0.0,
        )
        baseline_settings = replace(
            self.imu_settings,
            wait_for_activation=True,
            post_baseline_seconds=(
                longest_attack
                - self.imu_settings.duration_seconds
                + self.test_settings.wait_after_spoof
            ),
        )
        print("Запуск IMU relay: baseline до миссии, затем spoofing по SIGUSR1...")
        return self._start_imu_publisher(baseline_settings, "IMU relay")

    def _print_paths(self) -> None:
        print("Текущие пути проекта:")
        print(f"  PX4: {PX4_ROOT}")
        print(f"  g.py: {GPS_SPOOF_SCRIPT}")
        print(f"  i.py: {IMU_SPOOF_SCRIPT}")
        print(f"  mission.plan: {MISSION_PLAN}")
        print(f"  logs: {LOG_DESTINATION}")

    def _print_dry_run(self) -> None:
        print("\nDry-run: процессы не запускались.")
        if self.test_settings.use_gps:
            command = self.gps_settings.build_command(self.project_python())
            print(f"Команда g.py: {self.process_supervisor.format_command(command)}")
        if self.test_settings.use_imu:
            command = self.imu_settings.build_command(self.project_python())
            print(f"Команда i.py: {self.process_supervisor.format_command(command)}")

    def run(self) -> int:
        environment_errors = self.validate_environment(
            require_px4=not self.test_settings.skip_px4,
            require_mission_dependencies=not self.test_settings.skip_mission,
            require_gps=self.test_settings.use_gps,
            require_imu=self.test_settings.use_imu,
        )
        if environment_errors:
            for error in environment_errors:
                print(f"Ошибка окружения: {error}", file=sys.stderr)
            return 1

        if self.test_settings.use_gps:
            self.gps_settings.print_summary()
        if self.test_settings.use_imu:
            self.imu_settings.print_summary()
        self._print_paths()
        if self.test_settings.dry_run:
            self._print_dry_run()
            return 0

        LOG_DESTINATION.mkdir(parents=True, exist_ok=True)
        px4_process: subprocess.Popen | None = None
        imu_baseline_process: subprocess.Popen | None = None
        imu_relay_process: subprocess.Popen | None = None
        publisher_processes: list[tuple[subprocess.Popen, float]] = []
        exit_code = 0

        try:
            if self.test_settings.skip_px4:
                print("Запуск PX4 пропущен: используется уже запущенный SITL.")
                if (
                    self.test_settings.use_imu
                    and os.environ.get("PX4_GZ_IMU_TOPIC") != self.imu_settings.topic
                ):
                    raise RuntimeError(
                        "для --skip-px4 с IMU внешний PX4 должен быть запущен с "
                        f"PX4_GZ_IMU_TOPIC={self.imu_settings.topic}"
                    )
            else:
                self.process_supervisor.cleanup_stale_simulation()
                print("\nЗапуск PX4 SITL + Gazebo Harmonic...")
                px4_environment = os.environ.copy()
                if self.test_settings.use_imu:
                    px4_environment["PX4_GZ_IMU_TOPIC"] = self.imu_settings.topic
                    print(
                        "PX4 GZ bridge будет использовать отдельный IMU-топик: "
                        f"{self.imu_settings.topic}"
                    )
                px4_process = self.process_supervisor.start_process(
                    "PX4 SITL",
                    ["make", "px4_sitl", "gz_x500"],
                    working_directory=PX4_ROOT,
                    environment=px4_environment,
                )
                if self.test_settings.use_imu:
                    imu_baseline_process = self._start_imu_baseline()
                self.process_supervisor.wait_while_running(
                    self.test_settings.px4_startup_delay,
                    "Ожидание инициализации PX4",
                    px4_process,
                )
                print("PX4 прошёл начальный период запуска.")

            if self.test_settings.use_imu and imu_baseline_process is None:
                imu_baseline_process = self._start_imu_baseline()
            if imu_baseline_process is not None:
                self.process_supervisor.wait_while_running(
                    1.0,
                    "Ожидание первых baseline IMU-сообщений",
                    imu_baseline_process,
                )

            if self.test_settings.skip_mission:
                print("Загрузка миссии отключена параметром --skip-mission.")
            elif MISSION_PLAN.is_file():
                self.gcs_heartbeat.start()
                if not self.mission_service.upload_and_start(MISSION_PLAN):
                    raise RuntimeError(
                        "миссия обязательна, но PX4 не подтвердил её запуск"
                    )
                self.gcs_heartbeat.ensure_running()
            else:
                raise RuntimeError(f"файл миссии не найден: {MISSION_PLAN}")

            self.process_supervisor.wait_while_running(
                self.test_settings.stabilization_delay,
                "Ожидание стабилизации перед подменой сенсорных данных",
                px4_process,
            )

            if self.test_settings.use_gps:
                publisher_processes.append(
                    (self._start_gps_publisher(), self.gps_settings.duration_seconds)
                )
            if self.test_settings.use_imu:
                if imu_baseline_process is None:
                    raise RuntimeError("IMU relay не был запущен")
                print("Активация настроенного IMU spoofing без смены процесса...")
                os.kill(imu_baseline_process.pid, signal.SIGUSR1)
                publisher_processes.append(
                    (
                        imu_baseline_process,
                        max(
                            self.imu_settings.duration_seconds,
                            self.gps_settings.duration_seconds
                            if self.test_settings.use_gps
                            else 0.0,
                        )
                        + self.test_settings.wait_after_spoof,
                    )
                )
                imu_relay_process = imu_baseline_process
                imu_baseline_process = None
            self.process_supervisor.wait_processes_while_running(
                "Публикация подменённых сенсорных данных активна",
                publisher_processes,
            )
            for publisher_process, _ in publisher_processes:
                if publisher_process is imu_relay_process:
                    if publisher_process.poll() not in (None, 0):
                        raise RuntimeError(
                            f"IMU relay завершился с кодом {publisher_process.returncode}"
                        )
                    continue
                try:
                    publisher_process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    self.process_supervisor.terminate_managed_process(publisher_process)
                if publisher_process.returncode not in (None, 0):
                    raise RuntimeError(
                        f"публикатор PID {publisher_process.pid} завершился с кодом "
                        f"{publisher_process.returncode}"
                    )

            if not self.test_settings.use_imu:
                self.process_supervisor.wait_while_running(
                    self.test_settings.wait_after_spoof,
                    "Дополнительная запись эффекта после подмены сенсорных данных",
                    px4_process,
                )
            print("Тест завершён успешно.")
        except KeyboardInterrupt:
            print("\nТест прерван пользователем.")
            exit_code = 130
        except Exception as exc:
            print(f"Ошибка теста: {exc}", file=sys.stderr)
            exit_code = 1
        finally:
            print("Остановка дочерних процессов...")
            self.gcs_heartbeat.stop()
            # Для IMU PX4 должен завершиться до relay, иначе между остановкой
            # последнего IMU publisher и PX4 возникает sensor timeout.
            if px4_process is not None and self.test_settings.use_imu:
                self.process_supervisor.terminate_managed_process(px4_process)
            self.process_supervisor.terminate_all()
            if not self.test_settings.skip_px4:
                print("Сохранение нового PX4-лога...")
                try:
                    self.log_collector.collect_latest_since(self.started_at_epoch)
                except Exception as exc:
                    print(f"Не удалось сохранить лог: {exc}", file=sys.stderr)
                    exit_code = exit_code or 1
            print("Сценарий завершён.")
        return exit_code