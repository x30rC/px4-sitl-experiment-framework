"""Ожидание окна атаки по времени Gazebo с проверкой процессов."""

from __future__ import annotations

import time
from threading import Event, Lock
from typing import Callable, Sequence
import subprocess


class SimulationClock:
    """Получает время симуляции из Gazebo Transport (не wall-clock)."""

    def __init__(self, world: str = "default") -> None:
        from gz.msgs10.clock_pb2 import Clock
        from gz.transport13 import Node

        self._lock = Lock()
        self._event = Event()
        self._value: float | None = None
        self._node = Node()  # держим подписку живой до завершения окна
        if not self._node.subscribe(Clock, f"/world/{world}/clock", self._on_clock):
            raise RuntimeError(f"нет подписки Gazebo clock мира {world}")

    def _on_clock(self, message) -> None:
        value = message.sim.sec + message.sim.nsec / 1e9
        with self._lock:
            if self._value is None or value > self._value:
                self._value = value
                self._event.set()

    def wait(
        self,
        seconds: float,
        processes: Sequence[tuple[subprocess.Popen, float]],
        healthcheck: Callable[[], None] | None = None,
        stall_timeout: float = 15.0,
    ) -> None:
        """Часы не движутся — атака не заканчивается; при длительном простое ошибка."""
        started: float | None = None
        last_value: float | None = None
        last_advance = time.monotonic()
        while True:
            if healthcheck is not None:
                healthcheck()
            with self._lock:
                current = self._value
                self._event.clear()
            if current is not None and (last_value is None or current > last_value):
                last_value = current
                last_advance = time.monotonic()
                if started is None:
                    started = current
            if time.monotonic() - last_advance > stall_timeout:
                raise RuntimeError("Gazebo clock остановился во время атаки")
            elapsed = 0.0 if started is None else max(0.0, current - started)
            for process, expected_duration in processes:
                status = process.poll()
                if status is not None and not (status == 0 and elapsed >= expected_duration - 1.0):
                    raise RuntimeError(f"процесс PID {process.pid} завершился раньше времени, код {status}")
            if started is not None and elapsed >= seconds:
                return
            self._event.wait(0.25)
