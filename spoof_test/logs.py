"""Поиск и копирование ULog текущего запуска PX4."""

from __future__ import annotations

import shutil
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

    def snapshot_existing(self) -> set[Path]:
        """Список ULog до запуска: PX4 может выставлять mtime не по wall clock."""
        if not self.source_directory.is_dir():
            return set()
        return set(self.source_directory.rglob("*.ulg"))

    def collect_latest_since(
        self, started_at_epoch: float, *, existing_logs: set[Path] | None = None
    ) -> Path | None:
        if not self.source_directory.is_dir():
            print(f"Папка с PX4-логами не существует: {self.source_directory}")
            return None

        candidates: list[Path] = []
        for log_file in self.source_directory.rglob("*.ulg"):
            try:
                if (existing_logs is not None and log_file not in existing_logs) or (
                    existing_logs is None and log_file.stat().st_mtime >= started_at_epoch - 2.0
                ):
                    candidates.append(log_file)
            except FileNotFoundError:
                continue

        if not candidates:
            print("Новый .ulg-лог текущего запуска не найден.")
            return None

        latest_log = max(candidates, key=lambda path: path.stat().st_mtime)
        destination_name = f"{latest_log.parent.name}_{latest_log.stem}"
        destination = self.destination_directory / destination_name
        suffix = 1
        while destination.exists():
            destination = self.destination_directory / f"{destination_name}_{suffix}"
            suffix += 1

        destination.mkdir(parents=True, exist_ok=False)
        destination_file = destination / latest_log.name
        print(f"Копирование {latest_log} -> {destination_file}")
        shutil.copy2(latest_log, destination_file)
        print(f"Логи сохранены в: {destination}")
        return destination
