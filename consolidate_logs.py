#!/usr/bin/env python3
"""Безопасное объединение выбранных топиков из локальных PX4 ULog."""

from __future__ import annotations

import argparse
import fnmatch
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

from spoof_test.dataset import build_analysis_csv
from spoof_test.plots import PLOT_NAMES, render_flight_plots

LOGS_BASE = Path(__file__).resolve().parent / "logs"

# Только важные для спуфинга и джампинга топики
# Звёздочки в начале и конце позволяют найти файлы с любым префиксом/суффиксом
TOPICS_TO_KEEP = [
    "*vehicle_gps_position*",
    "*sensor_gps*",
    "*estimator_aid_src_fake_pos*",
    "*estimator_aid_src_fake_hgt*",
    "*estimator_aid_src_gnss_pos*",
    "*estimator_status*",
    "*estimator_innovations*",
    "*estimator_innovation_test_ratios*",
    "*estimator_gps_status*",
    "*estimator_states*",
    "*vehicle_local_position*",
    "*home_position*",
    "*position_setpoint_triplet*",
    "*mission_result*",
    "*vehicle_global_position*",
    "*vehicle_attitude*",
    "*vehicle_angular_velocity*",
    "*vehicle_imu*",
    "*vehicle_imu_status*",
    "*sensor_gyro*",
    "*sensor_accel*",
    "*sensors_status_imu*",
    "*vehicle_status*",
    "*failsafe_flags*",
]

def is_topic_kept(filename: str) -> bool:
    return any(fnmatch.fnmatch(filename, pattern) for pattern in TOPICS_TO_KEEP)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs-dir", type=Path, default=LOGS_BASE,
                        help="каталог логов (по умолчанию logs/ рядом со скриптом)")
    parser.add_argument("--folder", action="append", metavar="NAME",
                        help="имя папки эксперимента; можно указать несколько раз (по умолчанию все)")
    parser.add_argument("--dry-run", action="store_true", help="показать план, не создавать файлы")
    return parser


def select_folders(logs_dir: Path, names: list[str] | None) -> list[Path]:
    """Не позволяем обработать внешние каталоги через имя или symlink."""
    if not logs_dir.is_dir():
        raise ValueError(f"каталог логов не найден: {logs_dir}")
    if names is None:
        folders = sorted(path for path in logs_dir.iterdir()
                         if path.is_dir() and not path.is_symlink())
    else:
        folders = []
        for name in dict.fromkeys(names):
            if name in {"", ".", ".."} or Path(name).name != name:
                raise ValueError(f"укажите только имя папки внутри logs/: {name!r}")
            folder = logs_dir / name
            if folder.is_symlink() or not folder.is_dir():
                raise ValueError(f"папка не найдена или является ссылкой: {folder}")
            folders.append(folder)
    return folders


def process_folder(folder: Path, logs_dir: Path, *, dry_run: bool = False) -> bool:
    """Создаёт один CSV на ULog; исходные файлы не трогает."""
    ulg_files = sorted(path for path in folder.glob("*.ulg")
                       if path.is_file() and not path.is_symlink())
    stems = [f"{folder.name}_{ulg.stem}" for ulg in ulg_files]
    outputs = [logs_dir / f"{stem}.csv" for stem in stems]
    images = [logs_dir / f"{stem}_{kind}.png" for stem in stems for kind in PLOT_NAMES]
    print(f"\nПапка: {folder}")
    if not ulg_files:
        print("  Нет .ulg файлов; пропуск.")
        return False
    existing = [path for path in [*outputs, *images] if path.exists() or path.is_symlink()]
    if existing:
        print(f"  Результат уже существует, не перезаписываем: {existing[0]}", file=sys.stderr)
        return False
    print(f"  ULog: {len(ulg_files)}; CSV: {len(outputs)}; PNG: {len(images)}")
    if dry_run:
        return True

    if not shutil.which("ulog2csv"):
        print("  Не найден ulog2csv (установите pyulog).", file=sys.stderr)
        return False
    # Временные каталоги исключают примесь старых CSV между полётами.
    with tempfile.TemporaryDirectory(prefix=".spoof-gaz-csv-", dir=logs_dir) as temp_dir:
        temp = Path(temp_dir)
        staged = []
        staged_images = []
        for index, ulg in enumerate(ulg_files):
            target = temp / str(index)
            target.mkdir()
            try:
                subprocess.run(["ulog2csv", str(ulg), "-o", str(target)],
                               check=True, capture_output=True, text=True)
            except (OSError, subprocess.CalledProcessError) as exc:
                details = getattr(exc, "stderr", "") or ""
                print(f"  Ошибка конвертации {ulg.name}: {exc} {details}", file=sys.stderr)
                return False
            files = [path for path in target.glob("*.csv") if is_topic_kept(path.stem)]
            candidate = temp / f"{index}.csv"
            try:
                if not build_analysis_csv(files, ulg.stem, candidate):
                    raise ValueError(f"нет доступных временных рядов в {ulg.name}")
            except (ImportError, OSError, ValueError) as exc:
                print(f"  Ошибка построения CSV: {exc}", file=sys.stderr)
                return False
            staged.append(candidate)
            try:
                staged_images.extend(render_flight_plots(candidate, temp, stems[index]))
            except (ImportError, OSError, ValueError) as exc:
                print(f"  Ошибка построения графиков {ulg.name}: {exc}", file=sys.stderr)
                return False
        published = []
        try:
            # Эксклюзивное создание: никогда не перезаписываем существующие данные.
            for source, destination in [*zip(staged, outputs), *zip(staged_images, images)]:
                destination.hardlink_to(source)
                published.append(destination)
        except OSError as exc:
            for destination in published:
                destination.unlink(missing_ok=True)
            print(f"  Ошибка сохранения CSV: {exc}", file=sys.stderr)
            return False
    print(f"  Готово: {', '.join(map(str, [*outputs, *images]))}; исходные ULog сохранены.")
    return True


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        folders = select_folders(args.logs_dir, args.folder)
    except ValueError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2
    if not folders:
        print("Папок экспериментов нет.")
        return 0
    print(f"Каталог: {args.logs_dir}; папок: {len(folders)}")
    failures = sum(not process_folder(folder, args.logs_dir, dry_run=args.dry_run)
                   for folder in folders)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
