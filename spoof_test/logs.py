"""Поиск и копирование ULog текущего запуска PX4."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from .paths import LOG_DESTINATION, PX4_LOG_SOURCE


class Px4LogCollector:
    """Копирует только самый новый ULog, созданный текущим тестом."""

    def __init__(
        self,
        source_directory: Path = PX4_LOG_SOURCE,
        destination_directory: Path = LOG_DESTINATION,
    ) -> None:
        self.source_directory = source_directory
        self.destination_directory = destination_directory

    def collect_latest_since(self, started_at_epoch: float) -> Path | None:
        if not self.source_directory.is_dir():
            print(f"Папка с PX4-логами не существует: {self.source_directory}")
            return None

        candidates: list[Path] = []
        for log_file in self.source_directory.rglob("*.ulg"):
            try:
                if log_file.stat().st_mtime >= started_at_epoch - 2.0:
                    candidates.append(log_file)
            except FileNotFoundError:
                continue

        if not candidates:
            print("Новый .ulg-лог текущего запуска не найден.")
            return None

        latest_log = max(candidates, key=lambda path: path.stat().st_mtime)
        destination_name = f"{latest_log.parent.name}_{latest_log.stem}"
        destination = self.destination_directory / destination_name
        if destination.exists():
            destination = destination.with_name(f"{destination.name}_{int(time.time())}")

        destination.mkdir(parents=True, exist_ok=False)
        destination_file = destination / latest_log.name
        print(f"Копирование {latest_log} -> {destination_file}")
        shutil.copy2(latest_log, destination_file)
        print(f"Логи сохранены в: {destination}")
        return destination