# spoof_gaz — PX4 SITL Experiment Framework

Инструменты для воспроизводимых локальных экспериментов с GPS, IMU,
магнитометром и барометром в **PX4 SITL + Gazebo Harmonic**.
Рабочая среда: **WSL2 Ubuntu 24.04, Python 3.12**, модель `gz_x500`.
Проект предназначен для собственного симулятора.

## Документация

| Документ | Содержание |
| --- | --- |
| [Установка](doc/INSTALL.md) | Зависимости, PX4, Gazebo, venv и применение патчей |
| [Запуск](doc/USAGE.md) | Меню, чистый полёт, датчики, режимы времени, серии, CSV/PNG и диагностика |
| [Устройство проекта](doc/ARCHITECTURE.md) | Модули, жизненный цикл эксперимента, изменение кода и проверки |
| [Содержание документации](doc/README.md) | Навигация и правила хранения файлов |

PX4 и Gazebo устанавливаются отдельно. Для IMU, mag и baro необходимы
соответствующие [патчи PX4](patches/README.md).

## Быстрый запуск

После [установки](doc/INSTALL.md) выполните **в терминале Ubuntu WSL**:

```bash
cd "$HOME/_spoof_gaz"
python3 run_test.py
```

Меню позволяет запустить чистый полёт или выбрать датчики и параметры подмены.
`run_test.py` автоматически использует рабочий Linux venv; файлы миссии и
результаты берутся относительно текущей копии проекта.

Проверка настроек без запуска симулятора и миссии:

```bash
python3 run_test.py --non-interactive --dry-run --skip-mission --skip-px4 \
  --spoof-sensor gps,imu,mag,baro --time-mode sync
```

Чистый полёт длительностью 60 секунд:

```bash
python3 run_test.py --non-interactive --spoof-sensor none --baseline-duration 60
```

GPS с периодами 5 секунд публикации и 4 секунды паузы:

```bash
python3 run_test.py --non-interactive --spoof-sensor gps \
  --intensity 85 --rate-hz 75 --duration 60 \
  --active-seconds 5 --idle-seconds 4
```

Датчики: `gps`, `imu`, `mag`, `baro`, сочетания через запятую;
`both` означает `gps,imu`, `none` — чистый полёт.
`--time-mode sync` использует время Gazebo, `async` (по умолчанию в CLI) —
монотонные часы процесса. Успешный запуск подтверждает выполнение сценария,
достижение конечного waypoint проверяется отдельно по данным полёта.

## Возможности

- Интерактивное меню и воспроизводимые команды CLI.
- Миссия QGroundControl через MAVSDK с проверкой готовности PX4 и GCS heartbeat.
- Независимые параметры датчиков, постоянная и периодическая подмена.
- Relay IMU/mag/baro с сохранением штатного потока вне активной фазы.
- Отдельная Gazebo partition и проверка входных потоков перед каждым прогоном.
- Серии `--repeats` и случайные маршруты с фиксируемым `--route-seed`.
- Визуальная метка конечной точки в Gazebo.
- Автоматическое сохранение CSV и трёх PNG после управляемого запуска PX4.
- Unit-тесты без запуска PX4 и Gazebo.

## Структура

```text
_spoof_gaz/
├── run_test.py           # полный сценарий / меню
├── gps_publisher.py      # GPS-публикатор
├── imu_publisher.py      # IMU-публикатор и relay
├── sensor_relay.py       # relay магнитометра и барометра
├── spoof_runtime.py      # расписание active/idle и CLI-типы
├── spoof_test/           # настройки, миссия, процессы, маршруты и обработка данных
├── consolidate_logs.py  # ручной экспорт сохранённых ULog
├── mission.plan         # шаблон QGroundControl Plan
├── requirements.txt     # Python-зависимости
├── test_*.py            # тесты
├── doc/                 # только Markdown-документация
└── patches/             # патчи внешнего PX4 и порядок их применения
```

`venv/`, `logs/`, CSV, ULog, кэш Python и служебные файлы Windows исключены
из Git. Результаты экспериментов хранятся локально. После успешного
автоматического экспорта временная копия ULog и план случайного маршрута
удаляются; при ошибке экспорта сохраняются для восстановления. Ручной
`consolidate_logs.py` сохраняет исходные ULog. Подробности —
[миссия и логи](doc/USAGE.md#миссия-и-логи).

Прежние `g.py` и `i.py` заменены на `gps_publisher.py` и `imu_publisher.py`.

## Проверка

```bash
./venv/bin/python -m unittest discover -p 'test_*.py' -v
python3 gps_publisher.py --check
python3 imu_publisher.py --check
python3 run_test.py --help
python3 sensor_relay.py --help
```

Unit-тесты проверяют настройки, меню, маршруты, relay, выбор часов,
ожидание датчиков, маркер цели и сохранение данных. Проверка фактического
полёта требует установленного стенда; последовательность приведена
в [руководстве](doc/USAGE.md#безопасная-ступенчатая-проверка).
