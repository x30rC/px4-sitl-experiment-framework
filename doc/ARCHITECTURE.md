# Устройство кода и руководство разработчика

[Содержание](README.md) · [Установка](INSTALL.md) · [Запуск](USAGE.md)

## Поток данных

```text
run_test.py -> spoof_test.cli -> settings / sensor_settings -> workflow
  |-> processes -> make px4_sitl gz_x500 -> PX4 + Gazebo
  |-> readiness -> реальные входные потоки датчиков Gazebo
  |-> goal_marker -> визуальная метка waypoint
  |-> gcs -> MAVLink GCS heartbeat -> PX4:18570
  |-> mission -> MAVSDK -> PX4:14540
  |-> gps_publisher.py -> штатный Gazebo NavSat topic -> PX4
  |-> imu_publisher.py -> /spoof_gaz/imu -> патченный PX4 bridge
  |-> sensor_relay.py -> /spoof_gaz/mag или /spoof_gaz/baro -> PX4 bridge
  `-> logs -> копия нового PX4 ULog -> consolidate_logs.py
                                       |-> dataset -> один CSV на ULog
                                       `-> plots -> trajectory/altitude/diagnostics PNG
```

`run_test.py` — точка входа, сохраняющая прежние импортируемые имена классов.
Оркестрация находится в `spoof_test/`, самостоятельные публикаторы — в корне.
Патчи внешнего PX4 находятся в [patches](../patches/README.md).

## Модули

| Путь | Назначение |
| --- | --- |
| `spoof_test/cli.py` | Парсер, меню, интерактивная настройка, сбор настроек и перезапуск через venv |
| `spoof_test/paths.py` | Корень текущей копии, фиксированный `~/PX4-Autopilot`, поиск venv, пути миссии/логов |
| `spoof_test/settings.py` | `GpsSpoofSettings`, `ImuSpoofSettings`, `SimulationTestSettings`: валидация и команды публикаторов |
| `spoof_test/sensor_settings.py` | `RelaySettings` для mag/baro: единицы измерения, топики, переменные bridge и CLI |
| `spoof_test/workflow.py` | `GpsSpoofTestWorkflow`: отдельные прогоны, запуск, активация, контроль и cleanup |
| `spoof_test/processes.py` | `ProcessSupervisor`: process groups, stale PX4/Gazebo/MAVSDK, ожидание и остановка |
| `spoof_test/readiness.py` | Ожидание реальных сообщений GPS и выбранных датчиков до миссии |
| `spoof_test/sim_clock.py` | `SimulationClock`: ожидание окна по времени Gazebo и контроль остановки часов |
| `spoof_test/mission.py` | MAVSDK, QGC Plan, ожидание health/armable, арминг и подтверждение старта миссии |
| `spoof_test/gcs.py` | GCS heartbeat до конца прогона, UDP `18570` |
| `spoof_test/goal_marker.py` | Конечный waypoint и статическая метка Gazebo без столкновений |
| `spoof_test/routes.py` | Воспроизводимые варианты направления/дистанции без изменения шаблона миссии |
| `spoof_test/logs.py` | Снимок существующих ULog и копирование нового лога в текущую копию проекта |
| `spoof_runtime.py` | `ActiveIdleSchedule` и `parse_finite_float`, без Gazebo-зависимостей |
| `gps_publisher.py` | `SpoofConfig`, `GpsSpoofer`: координаты NavSat, active/idle и выбор часов |
| `imu_publisher.py` | `ImuSpoofPublisher`: статический генератор или relay исходного IMU с offset |
| `sensor_relay.py` | Копирование и изменение сообщений mag/baro, активация через `SIGUSR1` |
| `consolidate_logs.py` | Выбор ULog, временный экспорт, один CSV и три PNG без перезаписи готовых результатов |
| `spoof_test/dataset.py` | Временное выравнивание диагностических потоков и сравнение с ground truth SITL |
| `spoof_test/plots.py` | Файлы графиков по CSV, без открытия GUI |

Имена `g.py` и `i.py` заменены на `gps_publisher.py` и `imu_publisher.py`.
`doc/` содержит только Markdown. `logs/`, `venv/`, CSV/ULog, кэш Python
и служебные файлы Windows исключены из Git.

## Жизненный цикл прогона

1. CLI выбирает исполняемый Linux venv и создаёт настройки. Путь к миссии и
   логам определяется текущим проектом. Повреждённый venv пропускается.
2. Workflow проверяет настройки и окружение. `--dry-run` выводит команды и
   координаты, завершаясь до запуска процессов. Чтобы проверить команды без
   установленного PX4/MAVSDK, добавьте `--skip-px4 --skip-mission`.
3. При управляемом запуске supervisor очищает stale-процессы симулятора текущего
   пользователя, создаёт уникальный `GZ_PARTITION` и запускает PX4. Это может
   завершить другой собственный ручной эксперимент: параллельные запуски
   не поддерживаются. После начальной задержки `readiness` ждёт до 60 секунд
   реальные сообщения GPS и выбранных датчиков.
4. Для IMU/mag/baro создаются baseline relay до миссии. PX4 получает переменные
   `PX4_GZ_IMU_TOPIC`, `PX4_GZ_MAG_TOPIC`, `PX4_GZ_BARO_TOPIC` выбранных датчиков
   и подписывается на выходные топики relay. В режиме `none` overrides снимаются,
   процессы подмены не создаются.
5. При управляемом PX4 и включённой миссии создаётся визуальная метка цели.
   Ошибка маркера выдаёт предупреждение. Затем запускается GCS heartbeat,
   MAVSDK подключается к `udpin://0.0.0.0:14540`, ждёт health, импортирует и
   загружает план, армит, подтверждает режим `MISSION` и прогресс. Ошибка
   обязательной миссии прерывает сценарий до активации подмены.
6. После стабилизации стартует GPS publisher; relay получают `SIGUSR1`, сохраняя
   тот же поток датчика. Окно эксперимента контролируется выбранными часами и
   состоянием дочерних процессов. В `none` ожидается `--baseline-duration`.
7. В `finally` останавливаются heartbeat и процессы. Для relay PX4 завершается
   раньше потока датчиков, чтобы избежать sensor timeout. Восстанавливается
   прежнее значение `GZ_PARTITION`.
8. После управляемого PX4 копируется новый ULog, при случайном маршруте — также
   использованный QGC Plan. Создаются CSV и три PNG в `logs/`. Только после
   успешного экспорта временная папка с ULog и Plan удаляется. При ошибке она
   сохраняется, код завершения ненулевой. Ручной конвертер исходники не удаляет.

`--skip-mission` пропускает heartbeat, план и арминг; `--skip-px4` оставляет
внешний SITL под управлением пользователя и отключает автоматический сбор
лога. Для внешнего PX4 все overrides выбранных relay должны совпадать и
присутствовать в окружении обоих процессов. Partition также должна совпадать.

При `--repeats N` перед каждым прогоном перезапускаются PX4/Gazebo и сервисы.
Меняется только конечный waypoint поддерживаемого QGC Plan, взлёт и высота
сохраняются. `--route-seed` позволяет повторить набор координат при том же
шаблоне и числе прогонов. Первая ошибка останавливает серию. Завершение по
времени не подтверждает достижение waypoint.

## Датчики и время

GPS публикуется в штатный NavSat topic x500. Координаты вычисляются по формуле
`reference + (target - reference) * intensity / 100`. В idle publisher не
публикует ложных сообщений; он не заставляет EKF вернуться к истинной позиции.

IMU relay копирует исходное сообщение, timestamp, ориентацию и шум. Offset
добавляется к части сообщений с частотой `--imu-rate-hz`; остальные
передаются без изменения. Полная частота потока не снижается. Значения gyro
задаются в рад/с, accel — в м/с², в Gazebo FLU; bridge переводит их в PX4 FRD.
Без `--source-topic` самостоятельный IMU publisher — статический генератор.

Mag/baro relay также сохраняют поток и меняют только выбранные измерения:
`field_tesla` в Tesla и давление в Pa. Все смещения масштабируются на
`intensity / 100`; параметры по умолчанию описаны в [USAGE.md](USAGE.md).

`--time-mode sync` привязывает GPS к Gazebo clock, relay — к timestamp входных
сообщений. `async` использует монотонные часы процесса; это значение по
умолчанию полного CLI. Relay в обоих режимах требуют входные сообщения.
Остановка Gazebo clock или потока надолго приводит к ошибке, а не успеху.

`ActiveIdleSchedule` проверяет `elapsed % (active + idle)`. Оба периода
положительны или оба равны нулю; `0/0` означает постоянную активность.
Подробности выравнивания CSV, возраст измерений и ограничения ground truth
описаны в [руководстве по логам](USAGE.md#миссия-и-логи).

## Где менять поведение

- CLI и меню: `cli.py`, затем dataclass в `settings.py` или `sensor_settings.py`
  и передача аргументов в команду публикатора.
- Мир и модель: топики в настройках и `readiness.py`, согласованные overrides
  PX4; проверить фактические имена через `gz topic -l`.
- Подключение MAVSDK: `mission.py`; GCS heartbeat поддерживается в `gcs.py`.
- Запуск/остановка: `workflow.py` и `processes.py`; сохранять relay до остановки
  PX4, избегать двух конкурирующих потоков одного датчика.
- Экспорт и графики: `consolidate_logs.py`, `dataset.py`, `plots.py`.
- GZ bridge: патчи в `patches/`, применяемые к внешнему PX4 с пересборкой.

## Проверки перед отправкой

```bash
cd "$HOME/_spoof_gaz"
./venv/bin/python -m unittest discover -p 'test_*.py' -v
python3 gps_publisher.py --check
python3 imu_publisher.py --check
python3 sensor_relay.py --help
python3 run_test.py --dry-run --non-interactive --spoof-sensor gps,imu,mag,baro \
  --skip-mission --skip-px4 --time-mode sync
```

`test_*.py` проверяют конфигурацию, CLI/меню, маршруты, сохранение исходных
ULog при ручной конвертации, CSV/PNG, relay, часы, readiness и маркер цели.
Тесты используют Gazebo Python bindings, но не запускают физический симулятор.
Для проверок venv сначала выполните [его установку](INSTALL.md#3-создание-собственного-виртуального-окружения).
Unit-тесты и dry-run не подтверждают фактический полёт, сетевую связность или
применение патчей. Для этого нужен отдельный SITL-прогон из [USAGE.md](USAGE.md#безопасная-ступенчатая-проверка).
