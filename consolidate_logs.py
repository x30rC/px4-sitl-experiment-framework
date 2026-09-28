#!/usr/bin/env python3
"""
Скрипт для обработки папок с ULOG-логами:
- Конвертирует все .ulg в CSV.
- Фильтрует только нужные топики (для анализа спуфинга).
- Объединяет отфильтрованные CSV в один файл.
- Сохраняет итоговый CSV и удаляет исходную папку.
"""

import os
import sys
import shutil
import subprocess
import pandas as pd
import fnmatch
from pathlib import Path

LOGS_BASE = Path.home() / "spoof_gaz" / "logs"

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
]

def is_topic_kept(filename):
    for pattern in TOPICS_TO_KEEP:
        if fnmatch.fnmatch(filename, pattern):
            return True
    return False

def convert_ulog_to_csv(ulog_file):
    print(f"🔄 Конвертация {ulog_file.name} ...")
    try:
        subprocess.run(["ulog2csv", str(ulog_file)], check=True, capture_output=True, text=True)
        print(f"✅ Конвертация завершена для {ulog_file.name}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Ошибка конвертации {ulog_file.name}: {e.stderr}")
        return False

def process_folder(folder_path):
    print(f"\n📁 Обработка папки: {folder_path.name}")
    ulg_files = list(folder_path.glob("*.ulg"))
    if not ulg_files:
        print("   ⚠️ Нет .ulg файлов, пропускаем.")
        return False

    for ulg in ulg_files:
        convert_ulog_to_csv(ulg)

    all_csv = list(folder_path.glob("*.csv"))
    if not all_csv:
        print("   ❌ Нет CSV-файлов для обработки.")
        return False

    filtered = [f for f in all_csv if is_topic_kept(f.stem)]
    if not filtered:
        print("   ⚠️ Нет CSV-файлов, соответствующих фильтру.")
        # Если хотите, можно оставить папку нетронутой, но по умолчанию мы не удаляем её
        return False

    print(f"   📊 Найдено {len(filtered)} CSV для объединения (из {len(all_csv)}).")

    all_dfs = []
    file_names = []
    for file in filtered:
        try:
            df = pd.read_csv(file)
            all_dfs.append(df)
            file_names.append(file.stem)
        except Exception as e:
            print(f"   ⚠️ Ошибка чтения {file.name}: {e}")

    if not all_dfs:
        print("   ❌ Нет данных для объединения.")
        return False

    combined = pd.concat(all_dfs, ignore_index=True)
    lengths = [len(df) for df in all_dfs]
    topic_column = []
    for name, length in zip(file_names, lengths):
        topic_column.extend([name] * length)
    combined.insert(0, "topic", topic_column)

    output_filename = folder_path.name + "_telemetry.csv"
    output_path = LOGS_BASE / output_filename
    combined.to_csv(output_path, index=False)
    print(f"   ✅ Итоговый CSV сохранён: {output_path}")

    print(f"   🧹 Удаление папки {folder_path.name} ...")
    shutil.rmtree(folder_path, ignore_errors=True)
    print("   ✅ Папка удалена.")
    return True

def main():
    if not LOGS_BASE.exists():
        print(f"❌ Папка с логами не найдена: {LOGS_BASE}")
        sys.exit(1)

    folders = [d for d in LOGS_BASE.iterdir() if d.is_dir()]
    if not folders:
        print("ℹ️ Нет папок для обработки.")
        sys.exit(0)

    print(f"🔍 Найдено {len(folders)} папок с логами.\n")
    for folder in folders:
        process_folder(folder)

    print("\n🏁 Обработка всех папок завершена.")

if __name__ == "__main__":
    main()
