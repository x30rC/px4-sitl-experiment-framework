"""Запуск, наблюдение и гарантированное завершение дочерних процессов."""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class ManagedProcess:
    """Процесс вместе с человекочитаемым именем."""

    name: str
    process: subprocess.Popen


class ProcessSupervisor:
    """Управляет жизненным циклом процессов PX4 и публикаторов."""

    def __init__(self) -> None:
        self._managed_processes: list[ManagedProcess] = []

    @staticmethod
    def _stale_simulation_pids() -> list[int]:
        """Находит только процессы PX4/Gazebo текущего локального стенда."""
        if os.name == "nt":
            return []
        own_pid = os.getpid()
        uid = os.getuid()
        matches: list[int] = []
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            if pid == own_pid:
                continue
            try:
                if entry.stat().st_uid != uid:
                    continue
                command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                    errors="replace"
                )
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
            is_px4 = "PX4-Autopilot/build/px4_sitl_default/bin/px4" in command
            is_px4_make = "make px4_sitl" in command
            is_px4_gazebo = (
                "gz sim" in command
                and "PX4-Autopilot/Tools/simulation/gz/worlds/" in command
            )
            is_mavsdk = "mavsdk_server" in command
            if is_px4 or is_px4_make or is_px4_gazebo or is_mavsdk:
                matches.append(pid)
        return matches

    @classmethod
    def cleanup_stale_simulation(cls, timeout_seconds: float = 5.0) -> None:
        pids = cls._stale_simulation_pids()
        if not pids:
            return
        print("Найдены старые процессы PX4/Gazebo: " + ", ".join(map(str, pids)))
        process_groups: set[int] = set()
        for pid in pids:
            try:
                process_groups.add(os.getpgid(pid))
            except ProcessLookupError:
                pass
        for process_group in process_groups:
            try:
                os.killpg(process_group, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            remaining = [pid for pid in pids if Path(f"/proc/{pid}").exists()]
            if not remaining:
                print("Старые процессы симулятора остановлены.")
                return
            time.sleep(0.1)
        remaining = [pid for pid in pids if Path(f"/proc/{pid}").exists()]
        remaining_groups: set[int] = set()
        for pid in remaining:
            try:
                remaining_groups.add(os.getpgid(pid))
            except ProcessLookupError:
                pass
        for process_group in remaining_groups:
            try:
                os.killpg(process_group, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if remaining:
            print("Старые процессы симулятора принудительно остановлены.")

    @staticmethod
    def format_command(command: Sequence[str]) -> str:
        return shlex.join(str(part) for part in command)

    def start_process(
        self,
        name: str,
        command: Sequence[str],
        *,
        working_directory: Path | None = None,
        environment: dict[str, str] | None = None,
    ) -> subprocess.Popen:
        normalized_command = [str(part) for part in command]
        print(f"[CMD:{name}] {self.format_command(normalized_command)}")
        popen_options: dict = {
            "cwd": str(working_directory) if working_directory else None,
            # Открытый pipe не даёт PX4-консоли постоянно получать EOF и
            # печатать бесконечные приглашения `pxh>`.
            "stdin": subprocess.PIPE,
            "env": environment,
        }
        if os.name == "nt":
            popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_options["start_new_session"] = True

        process = subprocess.Popen(normalized_command, **popen_options)
        self._managed_processes.append(ManagedProcess(name=name, process=process))
        return process

    @staticmethod
    def terminate_process(
        process: subprocess.Popen,
        name: str,
        timeout_seconds: float = 10.0,
    ) -> None:
        if process.poll() is not None and os.name == "nt":
            if process.stdin is not None:
                process.stdin.close()
            return

        # Лидер process group (например, make) мог уже завершиться, оставив
        # запущенный им gz sim. Поэтому на Linux всё равно сигнализируем группе.
        if process.poll() is not None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            if process.stdin is not None:
                process.stdin.close()
            return

        print(f"Остановка процесса {name} (PID {process.pid})...")
        try:
            if os.name == "nt":
                process.terminate()
            else:
                os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            print(
                f"Процесс {name} не завершился за {timeout_seconds:g} сек; "
                "отправляется принудительный сигнал."
            )
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        except ProcessLookupError:
            pass
        finally:
            if process.stdin is not None:
                process.stdin.close()

    def terminate_managed_process(
        self,
        process: subprocess.Popen,
        timeout_seconds: float = 10.0,
    ) -> None:
        for managed_process in self._managed_processes:
            if managed_process.process is process:
                self.terminate_process(
                    process,
                    managed_process.name,
                    timeout_seconds,
                )
                return
        self.terminate_process(process, f"PID {process.pid}", timeout_seconds)

    def terminate_all(self) -> None:
        for managed_process in reversed(self._managed_processes):
            self.terminate_process(managed_process.process, managed_process.name)

    @staticmethod
    def wait_while_running(
        seconds: float,
        description: str,
        process: subprocess.Popen | None = None,
        *,
        allow_clean_exit_near_deadline: bool = False,
    ) -> None:
        """Ожидает заданный период и отслеживает раннее завершение процесса."""
        if seconds <= 0.0:
            return

        print(f"{description} ({seconds:g} сек)...")
        deadline = time.monotonic() + seconds
        next_progress_report = time.monotonic() + 5.0
        while True:
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0.0:
                return

            if process is not None:
                return_code = process.poll()
                if return_code is not None:
                    if (
                        allow_clean_exit_near_deadline
                        and return_code == 0
                        and remaining_seconds <= 1.0
                    ):
                        print(f"Процесс PID {process.pid} штатно завершил работу.")
                        return
                    raise RuntimeError(
                        f"процесс PID {process.pid} завершился раньше времени, "
                        f"код {return_code}"
                    )

            if time.monotonic() >= next_progress_report and remaining_seconds > 1.0:
                print(f"  осталось примерно {remaining_seconds:.0f} сек")
                next_progress_report = time.monotonic() + 5.0
            time.sleep(min(0.25, remaining_seconds))

    @staticmethod
    def wait_processes_while_running(
        description: str,
        processes: Sequence[tuple[subprocess.Popen, float]],
    ) -> None:
        seconds = max((duration for _, duration in processes), default=0.0)
        if seconds <= 0.0:
            return
        print(f"{description} ({seconds:g} сек)...")
        started_at = time.monotonic()
        deadline = time.monotonic() + seconds
        next_progress_report = time.monotonic() + 5.0
        while True:
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0.0:
                return
            elapsed = time.monotonic() - started_at
            for process, expected_duration in processes:
                return_code = process.poll()
                clean_expected_exit = (
                    return_code == 0 and elapsed >= expected_duration - 1.0
                )
                if return_code is not None and not clean_expected_exit:
                    raise RuntimeError(
                        f"процесс PID {process.pid} завершился раньше времени, "
                        f"код {return_code}"
                    )
            if time.monotonic() >= next_progress_report and remaining_seconds > 1.0:
                print(f"  осталось примерно {remaining_seconds:.0f} сек")
                next_progress_report = time.monotonic() + 5.0
            time.sleep(min(0.25, remaining_seconds))