"""Проверка реальных потоков датчиков после запуска PX4/Gazebo."""

from __future__ import annotations

import time
from threading import Event
from typing import Callable


def wait_for_sensors(
    sensors: set[str], healthcheck: Callable[[], None], timeout: float = 60.0,
) -> None:
    """Не путать объявленные топики со здоровым, публикующим миром Gazebo."""
    from gz.transport13 import Node
    from gz.msgs10.navsat_pb2 import NavSat
    from gz.msgs10.imu_pb2 import IMU
    from gz.msgs10.magnetometer_pb2 import Magnetometer
    from gz.msgs10.fluid_pressure_pb2 import FluidPressure

    topics = {
        "gps": (NavSat, "/world/default/model/x500_0/link/base_link/sensor/navsat_sensor/navsat"),
        "imu": (IMU, "/world/default/model/x500_0/link/base_link/sensor/imu_sensor/imu"),
        "mag": (Magnetometer, "/world/default/model/x500_0/link/base_link/sensor/magnetometer_sensor/magnetometer"),
        "baro": (FluidPressure, "/world/default/model/x500_0/link/base_link/sensor/air_pressure_sensor/air_pressure"),
    }
    # GPS необходим PX4 даже когда атакуются только другие датчики.
    pending = sensors | {"gps"}
    received = {sensor: Event() for sensor in pending}
    node = Node()
    callbacks = []
    for sensor in pending:
        message_type, topic = topics[sensor]
        callback = lambda _message, event=received[sensor]: event.set()
        callbacks.append(callback)  # Сохраняем ссылки до завершения ожидания.
        if not node.subscribe(message_type, topic, callback):
            raise RuntimeError(f"не удалось подписаться на поток {sensor}: {topic}")
    print("Ожидание живых потоков Gazebo: " + ", ".join(sorted(pending)) + "...")
    deadline = time.monotonic() + timeout
    while not all(event.is_set() for event in received.values()):
        healthcheck()
        if time.monotonic() >= deadline:
            missing = ", ".join(sorted(sensor for sensor, event in received.items() if not event.is_set()))
            raise RuntimeError(f"Gazebo/PX4 не готовы: нет сообщений {missing} за {timeout:g} сек")
        time.sleep(0.2)
    healthcheck()
    print("Потоки Gazebo готовы.")
