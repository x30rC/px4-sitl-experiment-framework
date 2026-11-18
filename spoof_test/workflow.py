"""Высокоуровневый сценарий GPS/IMU-теста с PX4 SITL и Gazebo."""

from __future__ import annotations

import os
import json
import secrets
import importlib.util
import shutil
import signal
import subprocess
import sys
import time
import tempfile
import uuid
from dataclasses import replace
from pathlib import Path

from .logs import Px4LogCollector
from .goal_marker import final_waypoint, spawn_goal_marker
from .gcs import GcsHeartbeatService
from .mission import MavsdkMissionService
from .paths import (
    GPS_SPOOF_SCRIPT,
    IMU_SPOOF_SCRIPT,
    SENSOR_RELAY_SCRIPT,
    LOG_DESTINATION,
    MISSION_PLAN,
    PROJECT_VENV_PYTHON,
    PX4_ROOT,
)
from .processes import ProcessSupervisor
from .routes import make_route
from .settings import GpsSpoofSettings, ImuSpoofSettings, SimulationTestSettings
from .sensor_settings import RelaySettings
from .sim_clock import SimulationClock
from .readiness import wait_for_sensors


class GpsSpoofTestWorkflow:
    """Координирует PX4, миссию, сенсорные publisher и сохранение ULog."""

    def __init__(
        self,
        test_settings: SimulationTestSettings,
        gps_settings: GpsSpoofSettings,
        imu_settings: ImuSpoofSettings | None = None,
        *,
        relay_settings: dict[str, RelaySettings] | None = None,
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
        self.relay_settings = relay_settings if relay_settings is not None else {s: RelaySettings(s) for s in ("mag", "baro")}
        for sensor in ("mag", "baro"):
            if getattr(test_settings, f"use_{sensor}"):
                self.relay_settings[sensor].validate()
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
        require_relay: bool = False,
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
        if require_relay and not SENSOR_RELAY_SCRIPT.is_file():
            errors.append(f"sensor relay не найден: {SENSOR_RELAY_SCRIPT}")
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
            replace(self.gps_settings, time_mode=self.test_settings.time_mode).build_command(self.project_python()),
            working_directory=GPS_SPOOF_SCRIPT.parent,
        )

    def _start_imu_publisher(
        self,
        settings: ImuSpoofSettings | None = None,
        name: str = "IMU publisher",
    ) -> subprocess.Popen:
        selected_settings = settings or self.imu_settings
        selected_settings = replace(selected_settings, time_mode=self.test_settings.time_mode)
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
        print(f"  gps_publisher.py: {GPS_SPOOF_SCRIPT}")
        print(f"  imu_publisher.py: {IMU_SPOOF_SCRIPT}")
        print(f"  sensor_relay.py: {SENSOR_RELAY_SCRIPT}")
        print(f"  mission.plan: {MISSION_PLAN}")
        print(f"  logs: {LOG_DESTINATION}")

    def _print_dry_run(self) -> None:
        print("\nDry-run: процессы не запускались.")
        if self.test_settings.spoof_sensor == "none":
            print(f"Чистый полёт: без publisher/relay, наблюдение {self.test_settings.baseline_duration:g} сек.")
        else:
            print(f"Режим времени: {self.test_settings.time_mode} (" +
                  ("Gazebo" if self.test_settings.time_mode == "sync" else "монотонные часы") + ")")
        if self.test_settings.use_gps:
            command = replace(self.gps_settings, time_mode=self.test_settings.time_mode).build_command(self.project_python())
            print(f"Команда gps_publisher.py: {self.process_supervisor.format_command(command)}")
        if self.test_settings.use_imu:
            command = replace(self.imu_settings, time_mode=self.test_settings.time_mode).build_command(self.project_python())
            print(f"Команда imu_publisher.py: {self.process_supervisor.format_command(command)}")
        for sensor in ("mag", "baro"):
            if getattr(self.test_settings, f"use_{sensor}"):
                config = replace(self.relay_settings[sensor], time_mode=self.test_settings.time_mode)
                print(f"{config.environment_key}={config.topic}")
                print(f"Команда {sensor}: {self.process_supervisor.format_command(config.build_command(self.project_python()))}")

    def run(self) -> int:
        """Проводит независимые прогоны; при первом сбое прекращает серию."""
        self.test_settings.validate()
        use_variants = self.test_settings.repeats > 1 or self.test_settings.random_mission
        if use_variants and not MISSION_PLAN.is_file():
            print(f"файл миссии не найден: {MISSION_PLAN}", file=sys.stderr)
            return 1
        route_seed = self.test_settings.route_seed
        if use_variants:
            route_seed = route_seed if route_seed is not None else secrets.randbits(64)
            print(f"Seed маршрутов: {route_seed}")
        for index in range(self.test_settings.repeats):
            if index and not self.test_settings.dry_run:
                self.process_supervisor = ProcessSupervisor()
                self.gcs_heartbeat = GcsHeartbeatService()
            self.started_at_epoch = time.time()
            print(f"\n=== Прогон {index + 1}/{self.test_settings.repeats} ===")
            if not use_variants:
                result = self._run_once(MISSION_PLAN)
            else:
                try:
                    plan, distance, bearing = make_route(
                        MISSION_PLAN, index, self.test_settings.repeats,
                        self.test_settings.route_min_m, self.test_settings.route_max_m,
                        route_seed,
                    )
                except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
                    print(f"Ошибка шаблона миссии: {exc}", file=sys.stderr)
                    return 1
                print(f"Маршрут: {distance:.1f} м от home, азимут {bearing:.1f}°, "
                      f"waypoint {plan['mission']['items'][1]['params'][4:6]}")
                print("Внимание: завершение по времени публикации, не по достижению waypoint. "
                      "Подберите --duration/--imu-duration и проверьте ULog.")
                if self.test_settings.dry_run:
                    result = self._run_once(MISSION_PLAN)
                else:
                    with tempfile.TemporaryDirectory(prefix="spoof-route-") as tmp:
                        mission_path = Path(tmp) / "mission.plan"
                        mission_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
                        result = self._run_once(mission_path, save_route=True)
            if result:
                print(f"Серия остановлена на прогоне {index + 1} из {self.test_settings.repeats}.")
                return result
        return 0

    def _run_once(self, mission_path: Path, *, save_route: bool = False) -> int:
        environment_errors = self.validate_environment(
            require_px4=not self.test_settings.skip_px4,
            require_mission_dependencies=not self.test_settings.skip_mission,
            require_gps=self.test_settings.use_gps,
            require_imu=self.test_settings.use_imu,
            require_relay=self.test_settings.use_mag or self.test_settings.use_baro,
        )
        if environment_errors:
            for error in environment_errors:
                print(f"Ошибка окружения: {error}", file=sys.stderr)
            return 1

        if self.test_settings.use_gps:
            self.gps_settings.print_summary()
        if self.test_settings.use_imu:
            self.imu_settings.print_summary()
        for sensor in ("mag", "baro"):
            if getattr(self.test_settings, f"use_{sensor}"):
                config = self.relay_settings[sensor]
                mode = (f"{config.active_seconds:g} сек работа / {config.idle_seconds:g} сек пауза"
                        if config.active_seconds else "непрерывно")
                print(f"{sensor}: смещение {config.effective_offsets} "
                      f"({'Тл' if sensor == 'mag' else 'Па'}), "
                      f"интенсивность {config.intensity_percent:g} %, "
                      f"длительность {config.duration_seconds:g} сек, {mode}")
                if config.intensity_percent == 0 or not any(config.effective_offsets):
                    print(f"Внимание: {sensor} настроен без фактического смещения (baseline).")
        self._print_paths()
        if self.test_settings.spoof_sensor != "none":
            print(f"Режим атаки: {'синхронный (время Gazebo)' if self.test_settings.time_mode == 'sync' else 'асинхронный (монотонное время)'}")
        if self.test_settings.dry_run:
            self._print_dry_run()
            if not self.test_settings.skip_mission:
                if self.test_settings.repeats > 1 or self.test_settings.random_mission:
                    print("Метка цели будет создана из маршрута каждого прогона (координаты выше).")
                else:
                    print(f"Конечная точка для маркера: {final_waypoint(mission_path)}")
            return 0

        LOG_DESTINATION.mkdir(parents=True, exist_ok=True)
        existing_logs = self.log_collector.snapshot_existing() if not self.test_settings.skip_px4 else None
        px4_process: subprocess.Popen | None = None
        imu_baseline_process: subprocess.Popen | None = None
        imu_relay_process: subprocess.Popen | None = None
        sensor_relays: dict[str, subprocess.Popen] = {}
        publisher_processes: list[tuple[subprocess.Popen, float]] = []
        exit_code = 0
        previous_partition = os.environ.get("GZ_PARTITION")

        try:
            if self.test_settings.skip_px4:
                print("Запуск PX4 пропущен: используется уже запущенный SITL.")
                for sensor in ("mag", "baro"):
                    if getattr(self.test_settings, f"use_{sensor}"):
                        config = replace(self.relay_settings[sensor], time_mode=self.test_settings.time_mode)
                        if os.environ.get(config.environment_key) != config.topic:
                            raise RuntimeError(f"external PX4 requires {config.environment_key}={config.topic}")
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
                # Старые объявления Gazebo Transport переживают завершение процесса:
                # новый PX4 не должен принять их clock за уже запущенный мир.
                os.environ["GZ_PARTITION"] = f"spoof_gaz_{uuid.uuid4().hex}"
                print("\nЗапуск PX4 SITL + Gazebo Harmonic...")
                px4_environment = os.environ.copy()
                if self.test_settings.spoof_sensor == "none":
                    for key in ("PX4_GZ_IMU_TOPIC", "PX4_GZ_MAG_TOPIC", "PX4_GZ_BARO_TOPIC"):
                        px4_environment.pop(key, None)
                if self.test_settings.use_imu:
                    px4_environment["PX4_GZ_IMU_TOPIC"] = self.imu_settings.topic
                    print(
                        "PX4 GZ bridge будет использовать отдельный IMU-топик: "
                        f"{self.imu_settings.topic}"
                    )
                for sensor in ("mag", "baro"):
                    if getattr(self.test_settings, f"use_{sensor}"):
                        config = self.relay_settings[sensor]
                        px4_environment[config.environment_key] = config.topic
                px4_process = self.process_supervisor.start_process(
                    "PX4 SITL",
                    ["make", "px4_sitl", "gz_x500"],
                    working_directory=PX4_ROOT,
                    environment=px4_environment,
                )
                self.process_supervisor.wait_while_running(
                    self.test_settings.px4_startup_delay,
                    "Ожидание инициализации PX4",
                    px4_process,
                )
                print("PX4 прошёл начальный период запуска; проверка мира и датчиков...")

            # В отличие от фиксированного startup delay, подтверждает, что новый
            # мир создал модель x500 и действительно передаёт измерения.
            required = {sensor for sensor in ("gps", "imu", "mag", "baro")
                        if getattr(self.test_settings, f"use_{sensor}")}
            if not self.test_settings.skip_px4:
                wait_for_sensors(required, lambda: self._ensure_px4_running(px4_process))

            if self.test_settings.use_imu and imu_baseline_process is None:
                imu_baseline_process = self._start_imu_baseline()
            for sensor in ("mag", "baro"):
                if getattr(self.test_settings, f"use_{sensor}") and sensor not in sensor_relays:
                    config = replace(self.relay_settings[sensor], time_mode=self.test_settings.time_mode)
                    sensor_relays[sensor] = self.process_supervisor.start_process(
                        f"{sensor} relay", config.build_command(self.project_python()),
                        working_directory=SENSOR_RELAY_SCRIPT.parent,
                    )
            for process in sensor_relays.values():
                self.process_supervisor.wait_while_running(
                    1.0, "Ожидание baseline датчика", process,
                )
            if imu_baseline_process is not None:
                self.process_supervisor.wait_while_running(
                    1.0,
                    "Ожидание первых baseline IMU-сообщений",
                    imu_baseline_process,
                )

            if not self.test_settings.skip_mission and not self.test_settings.skip_px4:
                try:
                    lat, lon, alt = spawn_goal_marker(mission_path)
                    print(f"Метка цели Gazebo: {lat:.8f}, {lon:.8f}, {alt:g} м над землёй "
                          "(без столкновений).", flush=True)
                except (ValueError, RuntimeError, OSError, ImportError) as exc:
                    print(f"Предупреждение: метка цели не создана: {exc}", file=sys.stderr)

            if self.test_settings.skip_mission:
                print("Загрузка миссии отключена параметром --skip-mission.")
            elif mission_path.is_file():
                self.gcs_heartbeat.start()
                if not self.mission_service.upload_and_start(mission_path):
                    raise RuntimeError(
                        "миссия обязательна, но PX4 не подтвердил её запуск"
                    )
                self.gcs_heartbeat.ensure_running()
            else:
                raise RuntimeError(f"файл миссии не найден: {mission_path}")

            self.process_supervisor.wait_while_running(
                self.test_settings.stabilization_delay,
                "Ожидание стабилизации перед наблюдением" if self.test_settings.spoof_sensor == "none"
                else "Ожидание стабилизации перед подменой сенсорных данных",
                px4_process,
            )

            clock = (SimulationClock() if self.test_settings.time_mode == "sync"
                     and self.test_settings.spoof_sensor != "none" else None)

            if self.test_settings.spoof_sensor == "none":
                self.process_supervisor.wait_while_running(
                    self.test_settings.baseline_duration,
                    "Чистый полёт без подмены датчиков",
                    px4_process,
                )
                if px4_process is not None:
                    self._ensure_px4_running(px4_process)
            else:
                if self.test_settings.use_gps:
                    publisher_processes.append(
                        (self._start_gps_publisher(), self.gps_settings.duration_seconds)
                    )
                for sensor, process in sensor_relays.items():
                    config = self.relay_settings[sensor]
                    print(f"Активация {sensor}: смещение {config.effective_offsets}, "
                          f"интенсивность {config.intensity_percent:g} %, "
                          f"длительность {config.duration_seconds:g} сек "
                          f"({'Gazebo' if self.test_settings.time_mode == 'sync' else 'монотонные часы'}), "
                          f"фазы {config.active_seconds:g}/{config.idle_seconds:g} сек", flush=True)
                    os.kill(process.pid, signal.SIGUSR1)
                    publisher_processes.append((process, config.duration_seconds + self.test_settings.wait_after_spoof))
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
                healthcheck = (lambda: self._ensure_px4_running(px4_process)) if px4_process is not None else None
                if self.test_settings.time_mode == "sync":
                    print("Ожидание окончания атаки по Gazebo clock...")
                    clock.wait(
                        max(duration for _, duration in publisher_processes),
                        publisher_processes,
                        healthcheck=healthcheck,
                    )
                else:
                    self.process_supervisor.wait_processes_while_running(
                        "Публикация подменённых сенсорных данных активна",
                        publisher_processes,
                        healthcheck=healthcheck,
                    )
                for publisher_process, _ in publisher_processes:
                    if publisher_process is imu_relay_process or publisher_process in sensor_relays.values():
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

                if not (self.test_settings.use_imu or sensor_relays):
                    self.process_supervisor.wait_while_running(
                        self.test_settings.wait_after_spoof,
                        "Дополнительная запись эффекта после подмены сенсорных данных",
                        px4_process,
                    )
                if px4_process is not None:
                    self._ensure_px4_running(px4_process)
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
            if px4_process is not None and (self.test_settings.use_imu or sensor_relays):
                self.process_supervisor.terminate_managed_process(px4_process)
            self.process_supervisor.terminate_all()
            if not self.test_settings.skip_px4:
                if previous_partition is None:
                    os.environ.pop("GZ_PARTITION", None)
                else:
                    os.environ["GZ_PARTITION"] = previous_partition
            if not self.test_settings.skip_px4:
                print("Сохранение нового PX4-лога...")
                try:
                    self._save_flight_csv(existing_logs, mission_path if save_route else None)
                except Exception as exc:
                    print(f"Не удалось сохранить CSV: {exc}", file=sys.stderr)
                    exit_code = exit_code or 1
            print("Сценарий завершён.")
        return exit_code

    def _save_flight_csv(self, existing_logs: set[Path], mission_path: Path | None) -> None:
        destination = self.log_collector.collect_latest_since(
            self.started_at_epoch, existing_logs=existing_logs
        )
        if destination is None:
            raise RuntimeError("ULog текущего прогона не найден")
        if mission_path is not None:
            shutil.copy2(mission_path, destination / "mission.plan")
        from consolidate_logs import process_folder

        if not process_folder(destination, self.log_collector.destination_directory):
            raise RuntimeError(f"Не удалось создать CSV; ULog сохранён в {destination}")
        try:
            shutil.rmtree(destination)
        except OSError as exc:
            print(f"Не удалось удалить временный ULog в {destination}: {exc}", file=sys.stderr)
        else:
            print("CSV сохранён, папка ULog текущего прогона удалена.")

    @staticmethod
    def _ensure_px4_running(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            raise RuntimeError(f"PX4 SITL завершился во время полёта (код {process.returncode}); проверьте Gazebo")
