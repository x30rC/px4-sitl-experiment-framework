# Устройство кода и руководство разработчика

[Содержание](README.md) · [Установка](INSTALL.md) · [Запуск](USAGE.md)

## Краткая схема

```text
run_test.py -> spoof_test.cli -> settings -> spoof_test.workflow
                                         |-> processes -> make px4_sitl gz_x500
                                         |-> gcs -> MAVLink GCS heartbeat -> PX4:18570
                                         |-> mission -> MAVSDK -> PX4:14540
                                         |-> g.py -> Gazebo NavSat topic -> PX4 GZ bridge
                                         |-> i.py (source Gazebo IMU -> /spoof_gaz/imu)
                                         |          -> патченный PX4 GZ bridge
                                         `-> logs -> PX4 ULog -> logs/
```

`run_test.py` — тонкая точка входа с прежними именами классов для совместимости; новая логика живёт в пакете `spoof_test`. Миссия, GPS и IMU независимы на уровне классов настроек, но единый workflow обеспечивает корректный порядок запуска и остановки.

| Путь относительно проекта | Роль и ключевые объекты |
| --- | --- |
| `spoof_test/cli.py` | `build_argument_parser`, `settings_from_arguments`, вопросы интерактивного режима, `restart_in_project_venv_if_needed`, `main` |
| `spoof_test/paths.py` | `PROJECT_ROOT` из местоположения пакета, фиксированный `PX4_ROOT = ~/PX4-Autopilot`, поиск рабочего venv, пути mission/logs |
| `spoof_test/settings.py` | `GpsSpoofSettings`, `ImuSpoofSettings`, `SimulationTestSettings`: валидация, формирование команд дочерних скриптов |
| `spoof_test/workflow.py` | `GpsSpoofTestWorkflow`: проверка окружения, запуск, активация, контроль результатов и `finally`/cleanup |
| `spoof_test/processes.py` | `ProcessSupervisor`: process group, обнаружение stale PX4/Gazebo/MAVSDK, остановка дочерних процессов, ожидание |
| `spoof_test/mission.py` | `MavsdkMissionService`: импорт QGC `.plan`, `mission_raw`, арминг, подтверждение flight mode/progress с тайм-аутами |
| `spoof_test/gcs.py` | `GcsHeartbeatService`: MAVLink `MAV_TYPE_GCS` каждые ~1 сек до конца теста |
| `spoof_test/logs.py` | `Px4LogCollector`: ищет новый `.ulg` с момента старта и копирует в текущую копию проекта |
| `spoof_runtime.py` | `ActiveIdleSchedule` и `parse_finite_float`, чистая логика без Gazebo |
| `g.py` | `SpoofConfig`, `GpsSpoofer`: NavSat-публикация и расписание active/idle |
| `i.py` | `ImuSpoofSettings`, `ImuSpoofPublisher`: самостоятельный IMU generator **или** relay-transform исходного Gazebo IMU |
| `test_spoof_gaz.py` | Python `unittest`: конфигурация, CLI, расчёты и отдельные элементы workflow без старта симулятора |
| `consolidate_logs.py` | Необязательная и **разрушающая** постобработка ULog → CSV: см. [предупреждение](USAGE.md#миссия-и-логи) |

## Как работает полный запуск

1. `cli.main()` при необходимости перезапускается через локальный исполняемый venv (`sys.prefix`, а не путь symlink-интерпретатора); читает CLI/ввод пользователя, создаёт immutable dataclass-настройки.
2. Workflow валидирует окружение и конфигурацию. При `--dry-run` только печатает команды и **выходит до старта PX4**.
3. При обычном запуске supervisor завершает stale-процессы данного симулятора текущего пользователя и запускает `make px4_sitl gz_x500`. Ручные экземпляры другого своего эксперимента тоже могут попасть под очистку: не запускайте параллельные тесты.
4. Для `imu` / `both` до миссии запускается **один** relay IMU с `--wait-for-activation` и выходным топиком `/spoof_gaz/imu`. Для запущенного workflow PX4 получает `PX4_GZ_IMU_TOPIC` из настроек. Этот контракт реализует прилагаемый [PX4 patch](px4-gz-imu-topic.patch), он **не входит** в обычный upstream PX4.
5. Если миссия включена: `GcsHeartbeatService.start()` → MAVSDK connect (`udpin://0.0.0.0:14540`) → health (local/global/home/armable) → QGC import/upload → arm и подтверждение → `mission_raw.start_mission()` → подтверждение `MISSION` и progress. При отказе миссии GPS/IMU attack не стартует.
6. После стабилизации запускается `g.py` (если выбран GPS); для IMU отправляется `SIGUSR1` уже работающему relay — без смены IMU-потока при арминге. Supervisor ожидает заданную длительность и код завершения дочерних процессов.
7. `finally` останавливает heartbeat и процессы; в IMU-режиме PX4 останавливается **раньше** relay, чтобы не получить sensor timeout. Если PX4 был запущен workflow, `Px4LogCollector` копирует только лог этого запуска.

`--skip-mission` пропускает загрузку плана и арминг; `--skip-px4` передаёт управление внешнему SITL и не копирует ULog. При IMU + `--skip-px4` внешний процесс должен быть запущен с тем же `PX4_GZ_IMU_TOPIC`, иначе PX4 подпишется на неправильный поток.

## Данные датчиков и время

**GPS:** `g.py` вычисляет координаты из reference/target и `%` смещения. В активной фазе публикует `gz.msgs.NavSat` в `/world/default/model/x500_0/link/base_link/sensor/navsat_sensor/navsat`, в паузе прекращает свою публикацию. Это не замена фильтра EKF и не гарантия, что PX4 примет каждую посылку.

**IMU:** штатный сенсор x500 выдаёт `gz.msgs.IMU` на `/world/default/model/x500_0/link/base_link/sensor/imu_sensor/imu`. `i.py` копирует исходное сообщение, его timestamp и динамические компоненты, при активности добавляет заданный в FLU offset к выбранным сообщениям, затем публикует **в отдельном** `/spoof_gaz/imu`. Остальные сообщения пересылаются без изменения: частота применения offset не должна уменьшать основной baseline-поток. Активные/пассивные интервалы relay привязаны ко времени симуляции (Gazebo clock / исходные IMU timestamps), не к интервалу ожидания CLI. Настоящая ориентация/шум из исходного сообщения сохраняются. Самостоятельный `python3 i.py` без source-topic — статический генератор для обучения, а не полный relay-тест полёта.

`ActiveIdleSchedule` возвращает активность по `elapsed % (active + idle)`; оба периода должны быть положительными или оба нулевыми. Значение `0/0` означает постоянную активную фазу. В Gazebo→PX4 bridge координаты IMU переводятся из FLU (Y влево, Z вверх) в FRD (Y вправо, Z вниз). Патч PX4 выбирает выходной топик и игнорирует повторные IMU callbacks в одном PX4 simulation tick для строгого возрастания timestamp.

## Где менять поведение

- Новая CLI-опция: объявить в `spoof_test/cli.py`, передать в соответствующий dataclass из `spoof_test/settings.py`, добавить в `build_command()` при настройке дочернего процесса. Для самостоятельного публикатора добавить аргумент в его `build_argument_parser()`.
- Другая модель/мир: GPS/IMU topic зависят от имени модели. В полном workflow IMU `source_topic` и `PX4_GZ_IMU_TOPIC` должны совпасть с фактическими Gazebo topics; проверять через `gz topic -l`. Для другого PX4 пути сейчас требуется изменение `PX4_ROOT` в `spoof_test/paths.py`.
- Изменение MAVSDK-протокола: `spoof_test/mission.py`, не смешивать MAVSDK onboard-connection и GCS heartbeat в `spoof_test/gcs.py`. Не отключайте PX4 preflight checks ради успешного теста.
- Изменение потоков/cleanup: `spoof_test/workflow.py` и `spoof_test/processes.py`; при IMU особенно важно сохранять relay до завершения PX4 и не подписывать bridge на два разных IMU topic одновременно.
- PX4 patch: приложенный diff предназначен для указанной в установке ревизии PX4. После изменений bridge пересобирайте PX4; этот код живёт **за пределами** Python-проекта.

## Проверки перед отправкой изменений

```bash
cd "$HOME/spoof_gaz"
./venv/bin/python -m compileall -q g.py i.py run_test.py spoof_runtime.py spoof_test test_spoof_gaz.py
./venv/bin/python -m unittest -v test_spoof_gaz.py
python3 g.py --check
python3 i.py --check
python3 run_test.py --dry-run --non-interactive --spoof-sensor both --skip-mission
```

`test_spoof_gaz.py` содержит проверки, зависящие от обнаруженного рабочего venv: на новой машине **сначала** выполните [шаг создания venv](INSTALL.md#3-создание-собственного-виртуального-окружения). Unit-тесты и dry-run не проверяют сетевую связность Gazebo, фактический полёт и применение патча PX4. После изменения их проверяют коротким SITL-прогоном из [USAGE.md](USAGE.md#безопасная-ступенчатая-проверка) и анализом нового `.ulg`.