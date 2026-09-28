#!/usr/bin/env python3
"""
Настраиваемый IMU-публикатор для PX4 SITL + Gazebo Harmonic.

Скрипт предназначен для локального симулятора. Он публикует сообщения
``gz.msgs.IMU`` с регулируемым смещением угловой скорости и линейного
ускорения, частотой и периодическим режимом работы.
"""

from __future__ import annotations

import argparse
import math
import signal
import sys
import time
from dataclasses import dataclass
from threading import Event, Lock
from typing import Sequence

from spoof_runtime import ActiveIdleSchedule, parse_finite_float


DEFAULT_WORLD_NAME = "default"
DEFAULT_MODEL_NAME = "x500_0"
# В x500_base/model.sdf штатный IMU работает с update_rate=250 Гц.
DEFAULT_PUBLISH_RATE_HZ = 250.0
DEFAULT_INTENSITY_PERCENT = 100.0


@dataclass(frozen=True)
class Vector3:
    """Трёхмерный вектор без зависимости от protobuf-классов Gazebo."""

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def validate(self, name: str) -> list[str]:
        errors: list[str] = []
        for axis, value in (("x", self.x), ("y", self.y), ("z", self.z)):
            if not math.isfinite(value):
                errors.append(f"{name}.{axis} должен быть конечным числом")
        return errors

    def add_scaled(self, offset: "Vector3", scale: float) -> "Vector3":
        return Vector3(
            x=self.x + offset.x * scale,
            y=self.y + offset.y * scale,
            z=self.z + offset.z * scale,
        )

    def as_tuple(self) -> tuple[float, float, float]:
        return self.x, self.y, self.z


@dataclass(frozen=True)
class ImuSpoofSettings:
    """Конфигурация публикации подменённых IMU-измерений."""

    world_name: str = DEFAULT_WORLD_NAME
    model_name: str = DEFAULT_MODEL_NAME
    use_full_topic: bool = True
    topic_override: str | None = None

    # Базовое измерение неподвижного аппарата в Gazebo FLU.
    reference_angular_velocity: Vector3 = Vector3()
    reference_linear_acceleration: Vector3 = Vector3(0.0, 0.0, 9.81)

    # Смещение, добавляемое к базовому измерению при 100 % интенсивности.
    angular_velocity_offset: Vector3 = Vector3(0.0, 0.0, 0.5)
    linear_acceleration_offset: Vector3 = Vector3()

    intensity_percent: float = DEFAULT_INTENSITY_PERCENT
    publish_rate_hz: float = DEFAULT_PUBLISH_RATE_HZ
    active_seconds: float = 0.0
    idle_seconds: float = 0.0
    start_delay_seconds: float = 0.0
    duration_seconds: float = 0.0
    status_interval_seconds: float = 5.0
    entity_name_override: str | None = None
    clock_topic: str | None = None
    source_topic: str | None = None
    wait_for_activation: bool = False
    post_baseline_seconds: float = 0.0

    @property
    def topic(self) -> str:
        if self.topic_override:
            return self.topic_override
        if self.use_full_topic:
            return (
                f"/world/{self.world_name}/model/{self.model_name}"
                "/link/base_link/sensor/imu_sensor/imu"
            )
        return f"/model/{self.model_name}/imu"

    @property
    def entity_name(self) -> str:
        if self.entity_name_override:
            return self.entity_name_override
        return f"{self.model_name}::base_link::imu_sensor"

    @property
    def is_periodic(self) -> bool:
        return self.active_seconds > 0.0 and self.idle_seconds > 0.0

    @property
    def effective_angular_velocity(self) -> Vector3:
        return self.reference_angular_velocity.add_scaled(
            self.angular_velocity_offset,
            self.intensity_percent / 100.0,
        )

    @property
    def effective_linear_acceleration(self) -> Vector3:
        return self.reference_linear_acceleration.add_scaled(
            self.linear_acceleration_offset,
            self.intensity_percent / 100.0,
        )

    def validate(self) -> None:
        errors: list[str] = []
        if not self.world_name.strip():
            errors.append("имя мира не может быть пустым")
        if not self.model_name.strip():
            errors.append("имя модели не может быть пустым")
        if self.topic_override is not None and not self.topic_override.startswith("/"):
            errors.append("явно заданный топик должен начинаться с '/'")
        if self.entity_name_override is not None and not self.entity_name_override.strip():
            errors.append("entity-name не может быть пустым")
        if self.clock_topic is not None and not self.clock_topic.startswith("/"):
            errors.append("clock-topic должен начинаться с '/'")
        if self.source_topic is not None and not self.source_topic.startswith("/"):
            errors.append("source-topic должен начинаться с '/'")
        if self.source_topic == self.topic:
            errors.append("source-topic и выходной topic должны отличаться")

        errors.extend(self.reference_angular_velocity.validate("reference_angular_velocity"))
        errors.extend(self.reference_linear_acceleration.validate("reference_linear_acceleration"))
        errors.extend(self.angular_velocity_offset.validate("angular_velocity_offset"))
        errors.extend(self.linear_acceleration_offset.validate("linear_acceleration_offset"))

        for name, value in (
            ("intensity_percent", self.intensity_percent),
            ("publish_rate_hz", self.publish_rate_hz),
            ("active_seconds", self.active_seconds),
            ("idle_seconds", self.idle_seconds),
            ("start_delay_seconds", self.start_delay_seconds),
            ("duration_seconds", self.duration_seconds),
            ("status_interval_seconds", self.status_interval_seconds),
            ("post_baseline_seconds", self.post_baseline_seconds),
        ):
            if not math.isfinite(value):
                errors.append(f"{name} должен быть конечным числом")

        if not 0.0 <= self.intensity_percent <= 1000.0:
            errors.append("интенсивность должна быть в диапазоне 0..1000 %")
        if not 0.1 <= self.publish_rate_hz <= 2000.0:
            errors.append("частота публикации должна быть в диапазоне 0.1..2000 Гц")
        if self.active_seconds < 0.0 or self.idle_seconds < 0.0:
            errors.append("периоды активности и паузы не могут быть отрицательными")
        if (self.active_seconds == 0.0) != (self.idle_seconds == 0.0):
            errors.append(
                "active-seconds и idle-seconds должны быть оба больше нуля "
                "либо оба равны нулю"
            )
        if self.start_delay_seconds < 0.0:
            errors.append("задержка старта не может быть отрицательной")
        if self.duration_seconds < 0.0:
            errors.append("длительность не может быть отрицательной")
        if self.status_interval_seconds < 0.0:
            errors.append("интервал статуса не может быть отрицательным")
        if self.post_baseline_seconds < 0.0:
            errors.append("post-baseline-seconds не может быть отрицательным")
        if self.wait_for_activation and self.source_topic is None:
            errors.append("wait-for-activation поддерживается только в relay-режиме")

        if errors:
            raise ValueError("; ".join(errors))


class ImuSpoofPublisher:
    """Публикует настроенные сообщения IMU в Gazebo Transport."""

    def __init__(self, settings: ImuSpoofSettings) -> None:
        settings.validate()
        self.settings = settings
        self.stop_event = Event()
        self.published_messages = 0
        self.spoofed_messages = 0
        self._clock_lock = Lock()
        self._clock_event = Event()
        self._simulation_time_seconds: float | None = None
        self._relay_publisher = None
        self._relay_message_type = None
        self._relay_schedule: ActiveIdleSchedule | None = None
        self._relay_started_at: float | None = None
        self._relay_next_spoof_at: float | None = None
        self._relay_next_status_at: float | None = None
        self._relay_previous_active: bool | None = None
        self._relay_received_source = Event()
        self._attack_activation_event = Event()
        if not settings.wait_for_activation:
            self._attack_activation_event.set()

    @staticmethod
    def load_gazebo_bindings():
        try:
            from gz.msgs10.imu_pb2 import IMU
            from gz.transport13 import Node
        except ImportError as exc:
            raise RuntimeError(
                "не найдены Python bindings Gazebo (gz.msgs10/gz.transport13). "
                "Запустите i.py внутри Ubuntu 24.04 WSL в окружении проекта"
            ) from exc
        return Node, IMU

    @classmethod
    def check_environment(cls) -> str:
        _, imu_message_type = cls.load_gazebo_bindings()
        return imu_message_type.DESCRIPTOR.full_name

    def request_stop(self, *_args) -> None:
        self.stop_event.set()
        self._clock_event.set()

    def activate_attack(self, *_args) -> None:
        if not self._attack_activation_event.is_set():
            print("Получен сигнал активации IMU spoofing.")
            self._attack_activation_event.set()

    def _wait(self, seconds: float) -> bool:
        return self.stop_event.wait(max(0.0, seconds))

    def _clock_callback(self, message) -> None:
        simulation_time = float(message.sim.sec) + float(message.sim.nsec) / 1_000_000_000.0
        with self._clock_lock:
            if (
                self._simulation_time_seconds is None
                or simulation_time > self._simulation_time_seconds
            ):
                self._simulation_time_seconds = simulation_time
                self._clock_event.set()

    def _current_simulation_time(self) -> float | None:
        with self._clock_lock:
            return self._simulation_time_seconds

    def _wait_for_next_clock(self, previous_time: float | None) -> float | None:
        while not self.stop_event.is_set():
            current_time = self._current_simulation_time()
            if current_time is not None and (
                previous_time is None or current_time > previous_time
            ):
                return current_time
            self._clock_event.clear()
            self._clock_event.wait(0.25)
        return None

    def build_message(self, imu_message_type, *, spoofed: bool = True):
        """Создаёт baseline либо baseline + настроенное spoof-смещение."""
        if spoofed:
            angular_velocity = self.settings.effective_angular_velocity
            linear_acceleration = self.settings.effective_linear_acceleration
        else:
            angular_velocity = self.settings.reference_angular_velocity
            linear_acceleration = self.settings.reference_linear_acceleration

        message = imu_message_type()
        message.entity_name = self.settings.entity_name

        # Нулевой quaternion не является валидной ориентацией.
        message.orientation.x = 0.0
        message.orientation.y = 0.0
        message.orientation.z = 0.0
        message.orientation.w = 1.0

        message.angular_velocity.x = angular_velocity.x
        message.angular_velocity.y = angular_velocity.y
        message.angular_velocity.z = angular_velocity.z
        message.linear_acceleration.x = linear_acceleration.x
        message.linear_acceleration.y = linear_acceleration.y
        message.linear_acceleration.z = linear_acceleration.z

        # PX4 GZBridge использует только векторы gyro/accel, но заполненные
        # ковариации делают сообщение полноценным для других подписчиков.
        message.orientation_covariance.data.extend([-1.0])
        message.angular_velocity_covariance.data.extend([0.0] * 9)
        message.linear_acceleration_covariance.data.extend([0.0] * 9)
        return message

    @staticmethod
    def message_time_seconds(message) -> float:
        if hasattr(message, "header") and hasattr(message.header, "stamp"):
            return (
                float(message.header.stamp.sec)
                + float(message.header.stamp.nsec) / 1_000_000_000.0
            )
        return time.monotonic()

    def _relay_callback(self, source_message) -> None:
        if self.stop_event.is_set() or self._relay_publisher is None:
            return
        source_time = self.message_time_seconds(source_message)
        self._relay_received_source.set()
        attack_enabled = self._attack_activation_event.is_set()
        if attack_enabled and self._relay_started_at is None:
            self._relay_started_at = source_time
            self._relay_next_spoof_at = source_time
            self._relay_next_status_at = source_time + self.settings.status_interval_seconds

        elapsed = (
            source_time - self._relay_started_at
            if self._relay_started_at is not None
            else 0.0
        )
        attack_finished = bool(
            attack_enabled
            and self.settings.duration_seconds
            and elapsed >= self.settings.duration_seconds
        )
        if (
            attack_finished
            and not self.settings.wait_for_activation
            and elapsed
            >= self.settings.duration_seconds + self.settings.post_baseline_seconds
        ):
            self.stop_event.set()
            return
        schedule = self._relay_schedule or ActiveIdleSchedule()
        active = attack_enabled and not attack_finished and schedule.is_active(elapsed)
        if active != self._relay_previous_active:
            print(
                "▶ IMU spoofing активен"
                if active
                else "⏸ Передаётся штатный IMU без spoof offset"
            )
            self._relay_previous_active = active

        output = self._relay_message_type()
        output.CopyFrom(source_message)
        spoof_this_message = (
            active
            and self._relay_next_spoof_at is not None
            and source_time >= self._relay_next_spoof_at
        )
        if spoof_this_message:
            scale = self.settings.intensity_percent / 100.0
            output.angular_velocity.x += self.settings.angular_velocity_offset.x * scale
            output.angular_velocity.y += self.settings.angular_velocity_offset.y * scale
            output.angular_velocity.z += self.settings.angular_velocity_offset.z * scale
            output.linear_acceleration.x += self.settings.linear_acceleration_offset.x * scale
            output.linear_acceleration.y += self.settings.linear_acceleration_offset.y * scale
            output.linear_acceleration.z += self.settings.linear_acceleration_offset.z * scale
            self.spoofed_messages += 1
            self._relay_next_spoof_at = source_time + 1.0 / self.settings.publish_rate_hz
        self._relay_publisher.publish(output)
        self.published_messages += 1

        if (
            attack_enabled
            and
            self.settings.status_interval_seconds > 0.0
            and self._relay_next_status_at is not None
            and source_time >= self._relay_next_status_at
        ):
            state = "spoofing активен" if active else "штатный IMU"
            print(
                f"Статус IMU relay: {state}, прошло {elapsed:.1f} сек, "
                f"всего {self.published_messages}, spoof {self.spoofed_messages}"
            )
            self._relay_next_status_at = source_time + self.settings.status_interval_seconds

    def run_relay(self, node, imu_message_type, publisher) -> int:
        self._relay_publisher = publisher
        self._relay_message_type = imu_message_type
        self._relay_schedule = ActiveIdleSchedule(
            active_seconds=self.settings.active_seconds,
            idle_seconds=self.settings.idle_seconds,
        )
        node.subscribe(imu_message_type, self.settings.source_topic, self._relay_callback)
        print(
            f"IMU relay запущен: {self.settings.source_topic} -> {self.settings.topic}. "
            "Для остановки нажмите Ctrl+C."
        )
        if self.settings.wait_for_activation:
            print("IMU relay передаёт baseline и ждёт SIGUSR1 для начала spoofing.")
        wall_started_at = time.monotonic()
        while not self.stop_event.wait(0.25):
            if (
                not self._relay_received_source.is_set()
                and time.monotonic() - wall_started_at > 10.0
            ):
                raise RuntimeError(
                    f"за 10 сек не получено ни одного IMU-сообщения из {self.settings.source_topic}"
                )
        elapsed = 0.0
        if self._relay_started_at is not None:
            current = self._relay_next_spoof_at or self._relay_started_at
            elapsed = max(0.0, current - self._relay_started_at)
        print(
            f"IMU relay остановлен: всего {self.published_messages}, "
            f"spoof {self.spoofed_messages} сообщений за {elapsed:.2f} сек симуляции."
        )
        return 0

    @staticmethod
    def update_timestamp(message) -> None:
        now_nanoseconds = time.time_ns()
        message.header.stamp.sec = now_nanoseconds // 1_000_000_000
        message.header.stamp.nsec = now_nanoseconds % 1_000_000_000

    def print_summary(self) -> None:
        angular_velocity = self.settings.effective_angular_velocity
        linear_acceleration = self.settings.effective_linear_acceleration
        mode = "непрерывный"
        if self.settings.is_periodic:
            mode = (
                f"периодический: {self.settings.active_seconds:g} сек активно / "
                f"{self.settings.idle_seconds:g} сек пауза"
            )
        duration = (
            "до остановки пользователем"
            if self.settings.duration_seconds == 0.0
            else f"{self.settings.duration_seconds:g} сек"
        )

        print("\n=== Конфигурация IMU-публикатора ===")
        print(f"Топик: {self.settings.topic}")
        print(f"Интенсивность смещения: {self.settings.intensity_percent:g} %")
        print(
            "Итоговая угловая скорость, рад/с: "
            f"x={angular_velocity.x:g}, y={angular_velocity.y:g}, z={angular_velocity.z:g}"
        )
        print(
            "Итоговое линейное ускорение, м/с²: "
            f"x={linear_acceleration.x:g}, "
            f"y={linear_acceleration.y:g}, z={linear_acceleration.z:g}"
        )
        print(f"Частота: {self.settings.publish_rate_hz:g} Гц")
        if self.settings.source_topic:
            print("Источник времени: timestamp штатного IMU")
            print("Частота задаёт частоту применения offset; baseline передаётся полностью")
        elif self.settings.clock_topic:
            print(f"Источник времени: Gazebo {self.settings.clock_topic}")
        else:
            print("Источник времени: wall clock")
        print(f"Режим: {mode}")
        print(f"Длительность: {duration}")
        if self.settings.start_delay_seconds:
            print(f"Задержка старта: {self.settings.start_delay_seconds:g} сек")
        print("======================================\n")

    def run(self) -> int:
        self.print_summary()
        if self.settings.start_delay_seconds > 0.0:
            print(f"Ожидание старта: {self.settings.start_delay_seconds:g} сек...")
            if self._wait(self.settings.start_delay_seconds):
                print("IMU-публикатор остановлен до начала публикации.")
                return 0

        Node, IMU = self.load_gazebo_bindings()
        node = Node()
        if self.settings.clock_topic:
            try:
                from gz.msgs10.clock_pb2 import Clock
            except ImportError as exc:
                raise RuntimeError("не найден protobuf Gazebo Clock") from exc
            if not node.subscribe(Clock, self.settings.clock_topic, self._clock_callback):
                raise RuntimeError(
                    f"не удалось подписаться на Gazebo clock {self.settings.clock_topic}"
                )
            print(f"Ожидание Gazebo clock {self.settings.clock_topic}...")
            if self._wait_for_next_clock(None) is None:
                return 0
        publisher = node.advertise(self.settings.topic, IMU)
        if publisher is None:
            raise RuntimeError(f"не удалось создать publisher для топика {self.settings.topic}")

        if self.settings.source_topic:
            return self.run_relay(node, IMU, publisher)

        spoofed_message = self.build_message(IMU, spoofed=True)
        baseline_message = self.build_message(IMU, spoofed=False)
        schedule = ActiveIdleSchedule(
            active_seconds=self.settings.active_seconds,
            idle_seconds=self.settings.idle_seconds,
        )
        publish_interval = 1.0 / self.settings.publish_rate_hz
        clock_synchronized = self.settings.clock_topic is not None
        started_at = (
            self._current_simulation_time() if clock_synchronized else time.monotonic()
        )
        if started_at is None:
            raise RuntimeError("Gazebo clock не предоставил начальное время")
        current_time = started_at
        next_publish_at = started_at
        next_status_at = started_at + self.settings.status_interval_seconds
        previous_active: bool | None = None

        print("IMU-публикатор запущен. Для остановки нажмите Ctrl+C.")
        while not self.stop_event.is_set():
            if clock_synchronized:
                next_clock = self._wait_for_next_clock(current_time)
                if next_clock is None:
                    break
                current_time = next_clock
                now = current_time
            else:
                now = time.monotonic()
            elapsed = now - started_at
            if self.settings.duration_seconds and elapsed >= self.settings.duration_seconds:
                break

            active = schedule.is_active(elapsed)
            if active != previous_active:
                print(
                    "▶ IMU spoofing активен"
                    if active
                    else "⏸ IMU spoofing выключен; публикуется baseline"
                )
                previous_active = active
                next_publish_at = now

            if now >= next_publish_at:
                message = spoofed_message if active else baseline_message
                self.update_timestamp(message)
                publisher.publish(message)
                self.published_messages += 1
                if active:
                    self.spoofed_messages += 1
                # Не публикуем накопившийся долг пачкой. В simulation-clock
                # режиме следующий слот отсчитывается от фактического tick.
                next_publish_at = now + publish_interval

            if (
                self.settings.status_interval_seconds > 0.0
                and now >= next_status_at
            ):
                state = "spoofing активен" if active else "baseline"
                print(
                    f"Статус IMU: {state}, прошло {elapsed:.1f} сек, "
                    f"всего {self.published_messages}, spoof {self.spoofed_messages}"
                )
                next_status_at = time.monotonic() + self.settings.status_interval_seconds

            if self.settings.duration_seconds:
                remaining = self.settings.duration_seconds - elapsed
            else:
                remaining = None
            if not clock_synchronized:
                sleep_seconds = max(0.0, next_publish_at - time.monotonic())
                if remaining is not None:
                    sleep_seconds = min(sleep_seconds, max(0.0, remaining))
                self._wait(sleep_seconds)

        final_time = self._current_simulation_time() if clock_synchronized else time.monotonic()
        elapsed = (final_time if final_time is not None else current_time) - started_at
        print(
            f"IMU-публикатор остановлен: всего {self.published_messages}, "
            f"spoof {self.spoofed_messages} сообщений за {elapsed:.2f} сек."
        )
        return 0


def add_vector_arguments(
    parser: argparse.ArgumentParser,
    prefix: str,
    defaults: Vector3,
    help_prefix: str,
) -> None:
    parser.add_argument(
        f"--{prefix}-x", type=parse_finite_float, default=defaults.x, help=f"{help_prefix}, ось X"
    )
    parser.add_argument(
        f"--{prefix}-y", type=parse_finite_float, default=defaults.y, help=f"{help_prefix}, ось Y"
    )
    parser.add_argument(
        f"--{prefix}-z", type=parse_finite_float, default=defaults.z, help=f"{help_prefix}, ось Z"
    )


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Публикация настраиваемых IMU-измерений в Gazebo Harmonic.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--world-name", default=DEFAULT_WORLD_NAME, help="имя мира Gazebo")
    parser.add_argument("--model-name", default=DEFAULT_MODEL_NAME, help="имя модели")
    parser.add_argument("--short-topic", action="store_true", help="использовать короткий топик")
    parser.add_argument("--topic", help="явно задать полный топик публикации")
    parser.add_argument(
        "--clock-topic",
        help="синхронизировать частоту, duration и active/idle с Gazebo clock",
    )
    parser.add_argument(
        "--source-topic",
        help="читать штатный IMU, применять offset и передавать в выходной topic",
    )
    parser.add_argument(
        "--wait-for-activation",
        action="store_true",
        help="до SIGUSR1 передавать штатный IMU без offset",
    )
    parser.add_argument(
        "--post-baseline-seconds",
        type=parse_finite_float,
        default=0.0,
        help="после attack duration продолжать штатный IMU указанное время",
    )
    parser.add_argument(
        "--entity-name",
        help="entity_name внутри IMU; по умолчанию строится из model-name",
    )

    add_vector_arguments(
        parser,
        "reference-angular-velocity",
        Vector3(),
        "базовая угловая скорость, рад/с",
    )
    add_vector_arguments(
        parser,
        "reference-linear-acceleration",
        Vector3(0.0, 0.0, 9.81),
        "базовое линейное ускорение, м/с²",
    )
    add_vector_arguments(
        parser,
        "angular-velocity-offset",
        Vector3(0.0, 0.0, 0.5),
        "добавочное смещение угловой скорости при 100 %% интенсивности, рад/с",
    )
    add_vector_arguments(
        parser,
        "linear-acceleration-offset",
        Vector3(),
        "добавочное смещение линейного ускорения при 100 %% интенсивности, м/с²",
    )

    parser.add_argument(
        "--intensity",
        "--intensity-percent",
        dest="intensity_percent",
        type=parse_finite_float,
        default=DEFAULT_INTENSITY_PERCENT,
        help="масштаб добавочного смещения, %%",
    )
    parser.add_argument(
        "--rate-hz",
        type=parse_finite_float,
        default=DEFAULT_PUBLISH_RATE_HZ,
        help="частота публикации сообщений",
    )
    parser.add_argument("--active-seconds", type=parse_finite_float, default=0.0)
    parser.add_argument("--idle-seconds", type=parse_finite_float, default=0.0)
    parser.add_argument("--start-delay", type=parse_finite_float, default=0.0)
    parser.add_argument(
        "--duration",
        type=parse_finite_float,
        default=0.0,
        help="общая длительность; 0 означает работу до сигнала остановки",
    )
    parser.add_argument(
        "--status-interval",
        type=parse_finite_float,
        default=5.0,
        help="период вывода статистики; 0 отключает статус",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="проверить конфигурацию и Gazebo bindings без публикации",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="показать итоговую конфигурацию без импорта Gazebo",
    )
    return parser


def vector_from_args(args: argparse.Namespace, prefix: str) -> Vector3:
    attribute_prefix = prefix.replace("-", "_")
    return Vector3(
        x=getattr(args, f"{attribute_prefix}_x"),
        y=getattr(args, f"{attribute_prefix}_y"),
        z=getattr(args, f"{attribute_prefix}_z"),
    )


def settings_from_args(args: argparse.Namespace) -> ImuSpoofSettings:
    return ImuSpoofSettings(
        world_name=args.world_name,
        model_name=args.model_name,
        use_full_topic=not args.short_topic,
        topic_override=args.topic,
        reference_angular_velocity=vector_from_args(args, "reference-angular-velocity"),
        reference_linear_acceleration=vector_from_args(args, "reference-linear-acceleration"),
        angular_velocity_offset=vector_from_args(args, "angular-velocity-offset"),
        linear_acceleration_offset=vector_from_args(args, "linear-acceleration-offset"),
        intensity_percent=args.intensity_percent,
        publish_rate_hz=args.rate_hz,
        active_seconds=args.active_seconds,
        idle_seconds=args.idle_seconds,
        start_delay_seconds=args.start_delay,
        duration_seconds=args.duration,
        status_interval_seconds=args.status_interval,
        entity_name_override=args.entity_name,
        clock_topic=args.clock_topic,
        source_topic=args.source_topic,
        wait_for_activation=args.wait_for_activation,
        post_baseline_seconds=args.post_baseline_seconds,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    try:
        settings = settings_from_args(args)
        publisher = ImuSpoofPublisher(settings)
        if args.dry_run:
            publisher.print_summary()
            print("Dry-run завершён: Gazebo не запускался, сообщения не публиковались.")
            return 0
        if args.check:
            message_type = publisher.check_environment()
            publisher.print_summary()
            print(f"Проверка пройдена. Доступен тип сообщения: {message_type}")
            return 0

        signal.signal(signal.SIGINT, publisher.request_stop)
        signal.signal(signal.SIGTERM, publisher.request_stop)
        if hasattr(signal, "SIGUSR1"):
            signal.signal(signal.SIGUSR1, publisher.activate_attack)
        return publisher.run()
    except (RuntimeError, ValueError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nIMU-публикатор остановлен пользователем.")
        return 130


if __name__ == "__main__":
    sys.exit(main())