"""Локальный MAVLink heartbeat наземной станции для автономного PX4 SITL."""

from __future__ import annotations

import threading
from types import ModuleType


class GcsHeartbeatService:
    """Поддерживает GCS data link, необходимый PX4 для штатного арминга."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 18570,
        interval_seconds: float = 1.0,
    ) -> None:
        self.host = host
        self.port = port
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._connection = None
        self._mavlink: ModuleType | None = None
        self._failure: Exception | None = None

    @property
    def endpoint(self) -> str:
        return f"udpout:{self.host}:{self.port}"

    def _send_heartbeat(self) -> None:
        if self._connection is None or self._mavlink is None:
            raise RuntimeError("GCS heartbeat не инициализирован")
        self._connection.mav.heartbeat_send(
            self._mavlink.MAV_TYPE_GCS,
            self._mavlink.MAV_AUTOPILOT_INVALID,
            0,
            0,
            self._mavlink.MAV_STATE_ACTIVE,
        )

    def _run(self) -> None:
        try:
            while not self._stop_event.wait(self.interval_seconds):
                self._send_heartbeat()
        except Exception as exc:  # pragma: no cover - зависит от UDP runtime
            self._failure = exc

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        try:
            from pymavlink import mavutil
        except ImportError as exc:
            raise RuntimeError(
                "pymavlink не найден; без GCS heartbeat PX4 блокирует арминг"
            ) from exc

        self._mavlink = mavutil.mavlink
        self._connection = mavutil.mavlink_connection(
            self.endpoint,
            source_system=255,
            source_component=self._mavlink.MAV_COMP_ID_MISSIONPLANNER,
        )
        self._stop_event.clear()
        self._failure = None
        self._send_heartbeat()
        self._thread = threading.Thread(
            target=self._run,
            name="spoof-gaz-gcs-heartbeat",
            daemon=True,
        )
        self._thread.start()
        print(f"GCS heartbeat запущен: {self.endpoint}")

    def ensure_running(self) -> None:
        if self._failure is not None:
            raise RuntimeError(f"GCS heartbeat завершился с ошибкой: {self._failure}")
        if self._thread is None or not self._thread.is_alive():
            raise RuntimeError("GCS heartbeat не работает")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=max(2.0, self.interval_seconds * 2.0))
        if self._connection is not None:
            self._connection.close()
        self._thread = None
        self._connection = None
        print("GCS heartbeat остановлен.")