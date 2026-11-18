#!/usr/bin/env python3
"""
Настраиваемый GPS-спуфер для PX4 SITL + Gazebo Harmonic.

Скрипт предназначен для локальных экспериментов в симуляторе. Он публикует
подменённые координаты NavSat с регулируемыми смещением, частотой и режимом
работы. По умолчанию сохранены прежние топик и координаты проекта.
"""

from __future__ import annotations

import argparse
import math
import signal
import sys
import time
from dataclasses import dataclass
from threading import Event
from typing import Sequence

from spoof_runtime import ActiveIdleSchedule, parse_finite_float

# Совместимость с кодом, который импортировал старое имя расписания.
AttackSchedule = ActiveIdleSchedule


# Значения по умолчанию сохраняют поведение старой версии скрипта.
DEFAULT_WORLD_NAME = "default"
DEFAULT_MODEL_NAME = "x500_0"
DEFAULT_REFERENCE_LAT = 47.398
DEFAULT_REFERENCE_LON = 8.545
DEFAULT_REFERENCE_ALT = 488.0
DEFAULT_TARGET_LAT = 47.399
DEFAULT_TARGET_LON = 8.546
DEFAULT_TARGET_ALT = 500.0
DEFAULT_PUBLISH_RATE_HZ = 100.0
DEFAULT_INTENSITY_PERCENT = 100.0


@dataclass(frozen=True)
class SpoofConfig:
    """Полная конфигурация публикации ложных GPS-данных."""

    world_name: str = DEFAULT_WORLD_NAME
    model_name: str = DEFAULT_MODEL_NAME
    use_full_topic: bool = True
    topic_override: str | None = None

    reference_lat: float = DEFAULT_REFERENCE_LAT
    reference_lon: float = DEFAULT_REFERENCE_LON
    reference_alt: float = DEFAULT_REFERENCE_ALT
    target_lat: float = DEFAULT_TARGET_LAT
    target_lon: float = DEFAULT_TARGET_LON
    target_alt: float = DEFAULT_TARGET_ALT

    intensity_percent: float = DEFAULT_INTENSITY_PERCENT
    publish_rate_hz: float = DEFAULT_PUBLISH_RATE_HZ
    active_seconds: float = 0.0
    idle_seconds: float = 0.0
    start_delay_seconds: float = 0.0
    duration_seconds: float = 0.0
    status_interval_seconds: float = 5.0
    time_mode: str = "async"

    @property
    def topic(self) -> str:
        if self.topic_override:
            return self.topic_override
        if self.use_full_topic:
            return (
                f"/world/{self.world_name}/model/{self.model_name}"
                "/link/base_link/sensor/navsat_sensor/navsat"
            )
        return f"/model/{self.model_name}/navsat"

    @property
    def effective_coordinates(self) -> tuple[float, float, float]:
        """Масштабирует смещение от опорной точки к целевой координате."""
        factor = self.intensity_percent / 100.0
        return (
            self.reference_lat + (self.target_lat - self.reference_lat) * factor,
            self.reference_lon + (self.target_lon - self.reference_lon) * factor,
            self.reference_alt + (self.target_alt - self.reference_alt) * factor,
        )

    @property
    def is_periodic(self) -> bool:
        return self.active_seconds > 0.0 and self.idle_seconds > 0.0

    def validate(self) -> None:
        errors: list[str] = []
        if self.time_mode not in {"sync", "async"}:
            errors.append("time_mode должен быть sync или async")
        if not self.world_name.strip():
            errors.append("имя мира не может быть пустым")
        if not self.model_name.strip():
            errors.append("имя модели не может быть пустым")
        if self.topic_override is not None and not self.topic_override.startswith("/"):
            errors.append("явно заданный топик должен начинаться с '/'")

        for name, value, minimum, maximum in (
            ("reference_lat", self.reference_lat, -90.0, 90.0),
            ("target_lat", self.target_lat, -90.0, 90.0),
            ("reference_lon", self.reference_lon, -180.0, 180.0),
            ("target_lon", self.target_lon, -180.0, 180.0),
        ):
            if not math.isfinite(value) or not minimum <= value <= maximum:
                errors.append(f"{name} должен быть в диапазоне [{minimum}, {maximum}]")

        for name, value in (
            ("reference_alt", self.reference_alt),
            ("target_alt", self.target_alt),
            ("intensity_percent", self.intensity_percent),
            ("publish_rate_hz", self.publish_rate_hz),
            ("active_seconds", self.active_seconds),
            ("idle_seconds", self.idle_seconds),
            ("start_delay_seconds", self.start_delay_seconds),
            ("duration_seconds", self.duration_seconds),
            ("status_interval_seconds", self.status_interval_seconds),
        ):
            if not math.isfinite(value):
                errors.append(f"{name} должен быть конечным числом")

        if not 0.0 <= self.intensity_percent <= 1000.0:
            errors.append("интенсивность должна быть в диапазоне 0..1000 %")
        if not 0.1 <= self.publish_rate_hz <= 1000.0:
            errors.append("частота публикации должна быть в диапазоне 0.1..1000 Гц")
        if self.active_seconds < 0.0 or self.idle_seconds < 0.0:
            errors.append("периоды активности и паузы не могут быть отрицательными")
        if (self.active_seconds == 0.0) != (self.idle_seconds == 0.0):
            errors.append(
                "для периодического режима active-seconds и idle-seconds должны быть "
                "оба больше нуля; для непрерывного режима оба должны быть равны нулю"
            )
        if self.start_delay_seconds < 0.0:
            errors.append("задержка старта не может быть отрицательной")
        if self.duration_seconds < 0.0:
            errors.append("длительность не может быть отрицательной")
        if self.status_interval_seconds < 0.0:
            errors.append("интервал статуса не может быть отрицательным")

        effective_lat, effective_lon, _ = self.effective_coordinates
        if not -90.0 <= effective_lat <= 90.0:
            errors.append("итоговая широта после применения интенсивности выходит за диапазон")
        if not -180.0 <= effective_lon <= 180.0:
            errors.append("итоговая долгота после применения интенсивности выходит за диапазон")

        if errors:
            raise ValueError("; ".join(errors))


class GpsSpoofer:
    """Создаёт Gazebo publisher и выполняет точный по времени цикл публикации."""

    def __init__(self, config: SpoofConfig) -> None:
        config.validate()
        self.config = config
        self.stop_event = Event()
        self.published_messages = 0
        self._clock_time = None
        self._clock_received_at = None

    def _clock_callback(self, message) -> None:
        stamp = message.sim
        now = stamp.sec + stamp.nsec / 1e9
        if self._clock_time is None or now > self._clock_time:
            self._clock_time = now
            self._clock_received_at = time.monotonic()

    @staticmethod
    def load_gazebo_bindings():
        """Ленивый импорт позволяет использовать --help/--dry-run из Windows."""
        try:
            from gz.msgs10.navsat_pb2 import NavSat
            from gz.transport13 import Node
        except ImportError as exc:
            raise RuntimeError(
                "не найдены Python bindings Gazebo (gz.msgs10/gz.transport13). "
                "Запустите скрипт внутри Ubuntu 24.04 WSL в окружении проекта"
            ) from exc
        return Node, NavSat

    @classmethod
    def check_environment(cls) -> str:
        _, navsat_type = cls.load_gazebo_bindings()
        return navsat_type.DESCRIPTOR.full_name

    def request_stop(self, *_args) -> None:
        self.stop_event.set()

    def _wait(self, seconds: float) -> bool:
        """Возвращает True, если ожидание было прервано сигналом остановки."""
        return self.stop_event.wait(max(0.0, seconds))

    @staticmethod
    def _set_message_timestamp(message) -> None:
        """Обновляет timestamp, если поле header присутствует в текущей версии NavSat."""
        if not hasattr(message, "header") or not hasattr(message.header, "stamp"):
            return
        now_ns = time.time_ns()
        message.header.stamp.sec = now_ns // 1_000_000_000
        message.header.stamp.nsec = now_ns % 1_000_000_000

    def print_summary(self) -> None:
        latitude, longitude, altitude = self.config.effective_coordinates
        mode = "непрерывный"
        if self.config.is_periodic:
            mode = (
                f"периодический: {self.config.active_seconds:g} сек активно / "
                f"{self.config.idle_seconds:g} сек пауза"
            )
        duration = (
            "до остановки пользователем"
            if self.config.duration_seconds == 0.0
            else f"{self.config.duration_seconds:g} сек"
        )

        print("\n=== Конфигурация GPS-спуфера ===")
        print(f"Топик: {self.config.topic}")
        print(
            "Опорные координаты: "
            f"lat={self.config.reference_lat:.8f}, "
            f"lon={self.config.reference_lon:.8f}, alt={self.config.reference_alt:.2f}"
        )
        print(
            "Целевые координаты: "
            f"lat={self.config.target_lat:.8f}, "
            f"lon={self.config.target_lon:.8f}, alt={self.config.target_alt:.2f}"
        )
        print(f"Интенсивность смещения: {self.config.intensity_percent:g} %")
        print(
            "Итоговые координаты: "
            f"lat={latitude:.8f}, lon={longitude:.8f}, alt={altitude:.2f}"
        )
        print(f"Частота: {self.config.publish_rate_hz:g} Гц")
        print(f"Источник времени: {'Gazebo clock' if self.config.time_mode == 'sync' else 'монотонные часы'}")
        print(f"Режим: {mode}")
        print(f"Длительность: {duration}")
        if self.config.start_delay_seconds:
            print(f"Задержка старта: {self.config.start_delay_seconds:g} сек")
        print("================================\n")

    def run(self) -> int:
        self.print_summary()

        if self.config.start_delay_seconds > 0.0:
            print(f"Ожидание старта: {self.config.start_delay_seconds:g} сек...")
            if self._wait(self.config.start_delay_seconds):
                print("GPS-спуфер остановлен до начала публикации.")
                return 0

        Node, NavSat = self.load_gazebo_bindings()
        node = Node()
        if self.config.time_mode == "sync":
            from gz.msgs10.clock_pb2 import Clock
            if not node.subscribe(Clock, f"/world/{self.config.world_name}/clock", self._clock_callback):
                raise RuntimeError("не удалось подписаться на Gazebo clock")
            deadline = time.monotonic() + 15
            while self._clock_time is None and not self.stop_event.is_set():
                if time.monotonic() >= deadline:
                    raise RuntimeError("Gazebo clock не передаёт время")
                self._wait(0.05)
            if self.stop_event.is_set():
                return 0
        publisher = node.advertise(self.config.topic, NavSat)
        if publisher is None:
            raise RuntimeError(f"не удалось создать publisher для топика {self.config.topic}")

        latitude, longitude, altitude = self.config.effective_coordinates
        message = NavSat()
        message.latitude_deg = latitude
        message.longitude_deg = longitude
        message.altitude = altitude

        schedule = ActiveIdleSchedule(
            active_seconds=self.config.active_seconds,
            idle_seconds=self.config.idle_seconds,
        )
        interval = 1.0 / self.config.publish_rate_hz
        started_at = self._clock_time if self.config.time_mode == "sync" else time.monotonic()
        next_publish_at = started_at
        next_status_at = time.monotonic() + self.config.status_interval_seconds
        previous_active: bool | None = None
        last_clock_time = None

        print("GPS-спуфер запущен. Для остановки нажмите Ctrl+C.")
        while not self.stop_event.is_set():
            if self.config.time_mode == "sync" and time.monotonic() - self._clock_received_at > 15:
                raise RuntimeError("Gazebo clock остановился")
            now = self._clock_time if self.config.time_mode == "sync" else time.monotonic()
            if self.config.time_mode == "sync" and now == last_clock_time:
                self._wait(0.002)
                continue
            last_clock_time = now
            elapsed = now - started_at
            if self.config.duration_seconds and elapsed >= self.config.duration_seconds:
                break

            active = schedule.is_active(elapsed)
            if active != previous_active:
                print("▶ Публикация активна" if active else "⏸ Пауза публикации")
                previous_active = active
                next_publish_at = now

            if active and now >= next_publish_at:
                if self.config.time_mode == "sync":
                    message.header.stamp.sec = int(now)
                    message.header.stamp.nsec = int((now - int(now)) * 1e9)
                else:
                    self._set_message_timestamp(message)
                publisher.publish(message)
                self.published_messages += 1

                next_publish_at += interval
                sleep_seconds = next_publish_at - (self._clock_time if self.config.time_mode == "sync" else time.monotonic())
                if sleep_seconds <= 0.0:
                    # Не пытаемся публиковать накопившийся «долг» пачкой.
                    next_publish_at = now
                    sleep_seconds = 0.0
            elif active:
                sleep_seconds = min(next_publish_at - now, 0.25)
            else:
                sleep_seconds = min(schedule.seconds_until_transition(elapsed), 0.25)

            if (
                self.config.status_interval_seconds > 0.0
                and time.monotonic() >= next_status_at
            ):
                state = "активен" if active else "пауза"
                print(
                    f"Статус: {state}, прошло {elapsed:.1f} сек, "
                    f"опубликовано {self.published_messages} сообщений"
                )
                next_status_at = time.monotonic() + self.config.status_interval_seconds

            if self.config.duration_seconds:
                remaining = self.config.duration_seconds - (now - started_at)
                sleep_seconds = min(sleep_seconds, max(0.0, remaining))
            if self.config.time_mode == "sync":
                sleep_seconds = min(sleep_seconds, 0.002)
            self._wait(sleep_seconds)

        elapsed = (self._clock_time - started_at if self.config.time_mode == "sync"
                   else time.monotonic() - started_at)
        print(
            f"GPS-спуфер остановлен: {self.published_messages} сообщений "
            f"за {elapsed:.2f} сек."
        )
        return 0


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Настраиваемая публикация ложных GPS-координат в Gazebo Harmonic.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--world-name", default=DEFAULT_WORLD_NAME, help="имя мира Gazebo")
    parser.add_argument("--model-name", default=DEFAULT_MODEL_NAME, help="имя модели")
    parser.add_argument("--short-topic", action="store_true", help="использовать короткий топик")
    parser.add_argument("--topic", help="явно задать полный топик публикации")

    parser.add_argument("--reference-lat", type=parse_finite_float, default=DEFAULT_REFERENCE_LAT)
    parser.add_argument("--reference-lon", type=parse_finite_float, default=DEFAULT_REFERENCE_LON)
    parser.add_argument("--reference-alt", type=parse_finite_float, default=DEFAULT_REFERENCE_ALT)
    parser.add_argument("--target-lat", type=parse_finite_float, default=DEFAULT_TARGET_LAT)
    parser.add_argument("--target-lon", type=parse_finite_float, default=DEFAULT_TARGET_LON)
    parser.add_argument("--target-alt", type=parse_finite_float, default=DEFAULT_TARGET_ALT)

    parser.add_argument(
        "--intensity",
        "--intensity-percent",
        dest="intensity_percent",
        type=parse_finite_float,
        default=DEFAULT_INTENSITY_PERCENT,
        help="масштаб смещения от опорных к целевым координатам, %%",
    )
    parser.add_argument(
        "--rate-hz",
        type=parse_finite_float,
        default=DEFAULT_PUBLISH_RATE_HZ,
        help="частота публикации сообщений",
    )
    parser.add_argument("--time-mode", choices=("sync", "async"), default="async")
    parser.add_argument(
        "--active-seconds",
        type=parse_finite_float,
        default=0.0,
        help="длительность активной фазы; 0 вместе с idle-seconds означает непрерывный режим",
    )
    parser.add_argument(
        "--idle-seconds",
        type=parse_finite_float,
        default=0.0,
        help="пауза между активными фазами",
    )
    parser.add_argument(
        "--start-delay", type=parse_finite_float, default=0.0, help="задержка старта, сек"
    )
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
        help="проверить конфигурацию и доступность Gazebo bindings без публикации",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="показать итоговую конфигурацию без импорта Gazebo и публикации",
    )
    return parser


def config_from_args(args: argparse.Namespace) -> SpoofConfig:
    return SpoofConfig(
        world_name=args.world_name,
        model_name=args.model_name,
        use_full_topic=not args.short_topic,
        topic_override=args.topic,
        reference_lat=args.reference_lat,
        reference_lon=args.reference_lon,
        reference_alt=args.reference_alt,
        target_lat=args.target_lat,
        target_lon=args.target_lon,
        target_alt=args.target_alt,
        intensity_percent=args.intensity_percent,
        publish_rate_hz=args.rate_hz,
        active_seconds=args.active_seconds,
        idle_seconds=args.idle_seconds,
        start_delay_seconds=args.start_delay,
        duration_seconds=args.duration,
        status_interval_seconds=args.status_interval,
        time_mode=args.time_mode,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    try:
        config = config_from_args(args)
        config.validate()
        spoofer = GpsSpoofer(config)

        if args.dry_run:
            spoofer.print_summary()
            print("Dry-run завершён: Gazebo не запускался, сообщения не публиковались.")
            return 0

        if args.check:
            message_type = spoofer.check_environment()
            spoofer.print_summary()
            print(f"Проверка пройдена. Доступен тип сообщения: {message_type}")
            return 0

        signal.signal(signal.SIGINT, spoofer.request_stop)
        signal.signal(signal.SIGTERM, spoofer.request_stop)
        return spoofer.run()
    except (RuntimeError, ValueError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nGPS-спуфер остановлен пользователем.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
