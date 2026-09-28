# spoof_gaz

> **Публикация:** этот репозиторий содержит только исходный код, пример миссии,
> документацию и отчёт. PX4 и Gazebo устанавливаются отдельно; виртуальное
> окружение и результаты испытаний (`logs/`) не входят в репозиторий. Начните
> с [пошаговой установки](doc/INSTALL.md).

**Новый пользователь?** Пошаговая [установка](doc/INSTALL.md),
[руководство по запуску](doc/USAGE.md) и [документация кода](doc/ARCHITECTURE.md)
собраны в каталоге [doc](doc/README.md). Для IMU на новой машине обязателен
[патч PX4](doc/px4-gz-imu-topic.patch), описанный в установке.

Инструменты для воспроизводимых локальных экспериментов с датчиками в
**PX4 SITL + Gazebo Harmonic** под **WSL2 Ubuntu 24.04 LTS**.

Проект содержит:

- полный сценарий запуска PX4, миссии и GPS/IMU-подмены;
- отдельный настраиваемый GPS-публикатор;
- отдельный настраиваемый IMU-публикатор;
- сохранение нового PX4 ULog после теста;
- unit-тесты чистой логики без запуска симулятора.

> Проект предназначен только для локального симулятора и исследовательских
> стендов, которыми вы имеете право управлять. Не используйте скрипты для
> воздействия на реальные аппараты или чужую инфраструктуру.

---

## 1. Поддерживаемое окружение

Проверенная конфигурация:

- Windows 11;
- WSL2;
- Ubuntu 24.04 LTS;
- Python 3.12;
- PX4 SITL;
- Gazebo Harmonic / Gazebo Sim 8;
- Python bindings `gz.msgs10` и `gz.transport13`;
- MAVSDK Python и `pymavlink` в Linux venv — требуются только для миссии.

Сценарии `run_test.py`, `g.py` и `i.py` следует запускать **в терминале
Ubuntu WSL**, а не через Windows Python.

---

## 2. Пути проекта и автоматический venv

PX4 сохраняет фиксированный путь, а остальные файлы определяются относительно
фактически запущенного `run_test.py`. Поэтому поддерживаются каталоги
`~/spoof_gaz`, `~/_spoof_gaz` и другие копии проекта.

| Назначение | Путь |
|---|---|
| PX4 Autopilot | `~/PX4-Autopilot` |
| проект | каталог, содержащий запущенный `run_test.py` |
| GPS-публикатор | `<каталог проекта>/g.py` |
| IMU-публикатор | `<каталог проекта>/i.py` |
| QGroundControl-миссия | `<каталог проекта>/mission.plan` |
| сохранённые ULog | `<каталог проекта>/logs` |
| Python venv | локальный `<каталог проекта>/venv` или найденный соседний рабочий venv |

`run_test.py` автоматически перезапускает себя через существующий исполняемый
Linux venv. В частности, запуск из `~/_spoof_gaz` может переиспользовать
`~/spoof_gaz/venv`, но `mission.plan`, `g.py` и логи всё равно берутся из
`~/_spoof_gaz`. Ручной `source venv/bin/activate` не требуется.

Повреждённый venv, например пустой или неисполняемый `venv/bin/python`,
автоматически игнорируется.

---

## 3. Структура проекта

```text
~/spoof_gaz/
├── run_test.py                 # точка входа полного GPS/IMU-теста
├── g.py                        # отдельный GPS-публикатор
├── i.py                        # отдельный IMU-публикатор
├── spoof_runtime.py            # общее расписание active/idle и CLI-типы
├── spoof_test/
│   ├── __init__.py
│   ├── paths.py                # текущая копия проекта, PX4 и поиск venv
│   ├── settings.py             # GPS/IMU и общие настройки теста
│   ├── processes.py            # ProcessSupervisor
│   ├── mission.py              # MavsdkMissionService
│   ├── logs.py                 # Px4LogCollector
│   ├── workflow.py             # GpsSpoofTestWorkflow
│   └── cli.py                  # argparse и интерактивная настройка
├── mission.plan
├── consolidate_logs.py
├── test_spoof_gaz.py
├── logs/
└── venv/
```

`run_test.py` не содержит бизнес-логику. Он сохраняет прежнее имя файла и
импортирует реализацию из пакета `spoof_test`.

### Основные классы

| Класс | Ответственность |
|---|---|
| `GpsSpoofSettings` | проверка GPS-параметров и команда для `g.py` |
| `SimulationTestSettings` | задержки, миссия, режимы полного теста |
| `ProcessSupervisor` | запуск и завершение PX4/дочерних процессов |
| `MavsdkMissionService` | соединение, загрузка, арминг и запуск миссии |
| `Px4LogCollector` | поиск и копирование нового ULog |
| `GpsSpoofTestWorkflow` | последовательность полного GPS/IMU-теста |
| `ImuSpoofSettings` | настройки IMU relay, offset и расписания |
| `ImuSpoofPublisher` | relay штатного `gz.msgs.IMU` с периодическим offset |

### Локальное расширение PX4 для IMU

Полный IMU-тест использует отдельный выходной топик `/spoof_gaz/imu`, чтобы PX4
не получал одновременно штатные и модифицированные сообщения. В локальном PX4
файл

```text
~/PX4-Autopilot/src/modules/simulation/gz_bridge/GZBridge.cpp
```

поддерживает переменную `PX4_GZ_IMU_TOPIC`. `run_test.py` задаёт её
автоматически для режимов `imu` и `both`. После изменения PX4 bridge нужно один
раз пересобрать:

```bash
cd ~/PX4-Autopilot
cmake --build build/px4_sitl_default --target px4 -j2
```

---

## 4. Проверка окружения

Откройте Ubuntu 24.04 WSL:

```bash
cd ~/spoof_gaz
python3 --version
python3 g.py --check
python3 i.py --check
```

Ожидаемые типы сообщений:

```text
gz.msgs.NavSat
gz.msgs.IMU
```

Проверка MAVSDK в venv:

```bash
~/spoof_gaz/venv/bin/python -c "import mavsdk; print('MAVSDK OK')"
```

Если MAVSDK отсутствует:

```bash
~/spoof_gaz/venv/bin/python -m pip install mavsdk
```

Без MAVSDK миссия не запускается. Для сенсорной проверки без арминга используйте
`--skip-mission`.

---

## 5. Полный тест через run_test.py

### 5.1 Быстрый интерактивный запуск

```bash
cd ~/spoof_gaz
python3 run_test.py
```

При обычном запуске из терминала сценарий сначала спрашивает тип подмены
`GPS`, `IMU` или `оба`, затем параметры выбранных сенсоров.

Для GPS запрашиваются интенсивность, частота, длительность и active/idle.
Для IMU дополнительно задаются gyro/accel offsets по трём осям.

Явное включение интерактивного режима:

```bash
python3 run_test.py --interactive
```

### 5.2 Неинтерактивный запуск

Для скриптов, CI и воспроизводимых прогонов используйте
`--non-interactive`:

```bash
python3 run_test.py \
  --non-interactive \
  --spoof-sensor gps \
  --intensity 50 \
  --rate-hz 30 \
  --duration 45
```

### 5.3 Периодическая GPS-публикация

Четыре секунды публикации и шесть секунд паузы в течение 90 секунд:

```bash
python3 run_test.py \
  --non-interactive \
  --intensity 75 \
  --rate-hz 50 \
  --active-seconds 4 \
  --idle-seconds 6 \
  --duration 90
```

Для непрерывного режима оба параметра должны быть равны нулю:

```bash
--active-seconds 0 --idle-seconds 0
```

Нельзя задавать только один из периодов.

### 5.4 Проверка конфигурации без запуска

```bash
python3 run_test.py \
  --dry-run \
  --non-interactive \
  --intensity 40 \
  --rate-hz 25 \
  --duration 20
```

Команда покажет итоговые координаты, пути и точную команду запуска `g.py`, но
не запустит PX4 или Gazebo.

### 5.5 Полный IMU-тест

Пример периодического gyro offset по Z:

```bash
python3 run_test.py \
  --non-interactive \
  --spoof-sensor imu \
  --imu-intensity 85 \
  --imu-rate-hz 75 \
  --imu-active-seconds 3 \
  --imu-idle-seconds 2 \
  --imu-duration 12 \
  --imu-angular-z 0.8
```

`i.py` непрерывно передаёт штатный поток IMU в PX4. Во время active-фазы
offset применяется с частотой `--imu-rate-hz`; остальные сообщения и все
сообщения idle-фазы передаются без изменения. Поэтому основной IMU-поток PX4
не прерывается и сохраняет штатную частоту около 250 Гц.

### 5.6 Одновременный GPS + IMU

```bash
python3 run_test.py \
  --non-interactive \
  --spoof-sensor both \
  --intensity 85 --rate-hz 75 \
  --active-seconds 5 --idle-seconds 4 --duration 60 \
  --imu-intensity 85 --imu-rate-hz 75 \
  --imu-active-seconds 5 --imu-idle-seconds 4 --imu-duration 60
```

### 5.7 Использование уже запущенного PX4

```bash
python3 run_test.py \
  --non-interactive \
  --skip-px4 \
  --skip-mission \
  --duration 20
```

`--skip-px4` означает, что управление и остановка существующего процесса PX4
остаются на пользователе.

Для IMU внешний PX4 должен быть заранее запущен с:

```bash
PX4_GZ_IMU_TOPIC=/spoof_gaz/imu make px4_sitl gz_x500
```

### 5.8 Запуск без миссии

```bash
python3 run_test.py --non-interactive --skip-mission
```

Это полезно для проверки сенсорной публикации без арминга и полёта.

### 5.9 Параметры run_test.py

| Параметр | Значение |
|---|---|
| `--interactive` | принудительно включить вопросы перед тестом |
| `--non-interactive` | использовать только CLI и значения по умолчанию |
| `--spoof-sensor gps\|imu\|both` | выбрать тип подмены |
| `--intensity` | интенсивность GPS-смещения, `%` |
| `--rate-hz` | частота запускаемого `g.py`, Гц |
| `--active-seconds` | длительность активной фазы |
| `--idle-seconds` | длительность паузы |
| `--duration` | общая длительность GPS-публикации |
| `--reference-lat/lon/alt` | опорные GPS-координаты |
| `--target-lat/lon/alt` | целевые GPS-координаты |
| `--imu-intensity` | масштаб IMU offset, `%` |
| `--imu-rate-hz` | частота применения IMU offset; baseline передаётся полностью |
| `--imu-active-seconds` | длительность active-фазы IMU |
| `--imu-idle-seconds` | длительность idle-фазы IMU |
| `--imu-duration` | длительность IMU attack в simulation time |
| `--imu-angular-x/y/z` | gyro offset при 100% интенсивности, рад/с |
| `--imu-linear-x/y/z` | accelerometer offset при 100% интенсивности, м/с² |
| `--px4-startup-delay` | ожидание начального запуска PX4 |
| `--stabilization-delay` | пауза перед GPS-публикацией |
| `--wait-after-spoof` | запись эффекта после завершения публикации |
| `--mission-timeout` | тайм-аут каждого этапа MAVSDK |
| `--skip-mission` | не загружать `mission.plan` |
| `--skip-px4` | не запускать новый процесс PX4 |
| `--dry-run` | только проверить настройки |

Полная актуальная справка:

```bash
python3 run_test.py --help
```

---

## 6. GPS-публикатор g.py

`g.py` можно запускать независимо от полного сценария.

### 6.1 Модель интенсивности GPS

Итоговая координата вычисляется линейно между опорной и целевой точкой:

```text
effective = reference + (target - reference) * intensity / 100
```

Примеры:

- `0 %` — опорная координата;
- `50 %` — середина между опорной и целевой точками;
- `100 %` — целевая координата;
- `150 %` — продолжение смещения за целевую точку.

### 6.2 Пример отдельного запуска

```bash
python3 g.py \
  --intensity 60 \
  --rate-hz 40 \
  --active-seconds 3 \
  --idle-seconds 2 \
  --duration 30
```

### 6.3 Пользовательские координаты

```bash
python3 g.py \
  --reference-lat 47.398 \
  --reference-lon 8.545 \
  --reference-alt 488 \
  --target-lat 47.400 \
  --target-lon 8.550 \
  --target-alt 520 \
  --intensity 50 \
  --duration 20
```

### 6.4 Полезные служебные режимы

```bash
python3 g.py --help
python3 g.py --dry-run --intensity 50
python3 g.py --check
```

По умолчанию используется топик:

```text
/world/default/model/x500_0/link/base_link/sensor/navsat_sensor/navsat
```

---

## 7. IMU-публикатор i.py

В полном workflow `i.py` работает как relay-transform:

1. подписывается на штатный IMU модели;
2. копирует каждое `gz.msgs.IMU`;
3. в active-фазе добавляет настроенный offset к части сообщений;
4. публикует результат в отдельный `/spoof_gaz/imu`, который слушает PX4.

Так сохраняются реальное движение, шум, ориентация, ускорение и полная штатная
частота потока. Для самостоятельных экспериментов без `--source-topic` остаётся
доступен генератор статических IMU-сообщений.

- `header.stamp`;
- `entity_name`;
- единичный quaternion ориентации `(0, 0, 0, 1)`;
- угловая скорость;
- линейное ускорение;
- ковариации.

PX4 GZBridge использует из сообщения векторы `angular_velocity` и
`linear_acceleration`.

### 7.1 Система координат

Gazebo IMU публикует измерения в системе **FLU**:

- X — вперёд;
- Y — влево;
- Z — вверх.

PX4 bridge преобразует FLU в **FRD**:

- X — вперёд;
- Y — вправо;
- Z — вниз.

Поэтому базовое удельное ускорение неподвижного аппарата в `i.py` задано как:

```text
Gazebo FLU: (0, 0, +9.81) м/с²
PX4 FRD:    (0, 0, -9.81) м/с²
```

### 7.2 Модель интенсивности IMU

Для IMU интенсивность масштабирует **добавочное смещение**, а не весь базовый
вектор:

```text
effective = reference + offset * intensity / 100
```

Это означает:

- `0 %` — только базовое измерение;
- `50 %` — половина заданного смещения;
- `100 %` — полное смещение;
- `200 %` — удвоенное смещение.

Значения по умолчанию:

```text
reference angular velocity:   (0, 0, 0) рад/с
angular velocity offset:      (0, 0, 0.5) рад/с
reference linear acceleration:(0, 0, 9.81) м/с²
linear acceleration offset:   (0, 0, 0) м/с²
publish rate:                 250 Гц
```

Таким образом, при `100 %` сохраняется прежняя идея вращения вокруг Z со
скоростью `0.5 рад/с`, но знак гравитации соответствует Gazebo FLU.

Штатный IMU модели `x500_base` публикует с частотой `250 Гц`. В relay-режиме
`--rate-hz` регулирует только частоту применения offset. Все входные сообщения
по-прежнему передаются PX4, поэтому значение `75 Гц` не уменьшает частоту
основного IMU-потока до 75 Гц.

### 7.3 Базовый запуск

```bash
python3 i.py --duration 20
```

### 7.4 Периодический режим

```bash
python3 i.py \
  --source-topic /world/default/model/x500_0/link/base_link/sensor/imu_sensor/imu \
  --topic /spoof_gaz/imu \
  --intensity 70 \
  --rate-hz 100 \
  --active-seconds 2 \
  --idle-seconds 3 \
  --duration 30
```

### 7.5 Смещение гироскопа

Добавить при `100 %` скорости `0.2`, `-0.1`, `0.8 рад/с`:

```bash
python3 i.py \
  --angular-velocity-offset-x 0.2 \
  --angular-velocity-offset-y -0.1 \
  --angular-velocity-offset-z 0.8 \
  --intensity 100 \
  --duration 20
```

### 7.6 Смещение акселерометра

```bash
python3 i.py \
  --linear-acceleration-offset-x 1.5 \
  --linear-acceleration-offset-y 0.0 \
  --linear-acceleration-offset-z -2.0 \
  --intensity 50 \
  --duration 20
```

В этом примере фактически добавится половина смещения:

```text
(+0.75, 0, -1.0) м/с²
```

### 7.7 Изменение базовых измерений

```bash
python3 i.py \
  --reference-angular-velocity-x 0 \
  --reference-angular-velocity-y 0 \
  --reference-angular-velocity-z 0 \
  --reference-linear-acceleration-x 0 \
  --reference-linear-acceleration-y 0 \
  --reference-linear-acceleration-z 9.81 \
  --duration 10
```

### 7.8 Топик и модель

Штатный source-топик модели `x500_0`:

```text
/world/default/model/x500_0/link/base_link/sensor/imu_sensor/imu
```

Для другой модели:

```bash
python3 i.py --model-name x500_1 --duration 10
```

`entity_name` автоматически станет:

```text
x500_1::base_link::imu_sensor
```

Его можно переопределить параметром `--entity-name`.

Явный выходной топик:

```bash
python3 i.py \
  --source-topic /world/default/model/x500_0/link/base_link/sensor/imu_sensor/imu \
  --topic /custom/imu \
  --duration 10
```

### 7.9 Служебные режимы IMU

```bash
python3 i.py --help
python3 i.py --dry-run --intensity 50
python3 i.py --check
```

---

## 8. Mission.plan и MAVSDK

Полный сценарий ищет миссию рядом с фактически запущенным `run_test.py`:

```text
<каталог проекта>/mission.plan
```

Последовательность:

1. запуск локального GCS heartbeat на PX4 SITL UDP-порту `18570`;
2. подключение MAVSDK к `udpin://0.0.0.0:14540` с тайм-аутом самого `connect()`;
3. ожидание подключения дрона;
4. ожидание локальной/глобальной позиции, home и `is_armable`;
5. импорт QGroundControl `.plan`;
6. загрузка через `mission_raw` и выбор первого пункта;
7. арминг с подтверждением состояния `armed`;
8. запуск через `mission_raw.start_mission()`;
9. подтверждение режима `MISSION` и прогресса миссии.

Каждый этап ограничен `--mission-timeout`. При ошибке обязательной миссии
сценарий завершается с ненулевым кодом, а не сообщает ложный успешный результат.

GCS heartbeat нужен потому, что актуальный PX4 при включённом `NAV_DLL_ACT`
штатно запрещает арминг без наземной станции. Скрипт не использует force-arm и
не ослабляет параметры safety/failsafe PX4.

Для безопасной проверки без арминга:

```bash
python3 run_test.py --non-interactive --skip-mission
```

---

## 9. ULog

После теста, в котором `run_test.py` сам запускал PX4:

1. процессы GPS/IMU/PX4/Gazebo завершаются целыми process group;
2. в PX4 rootfs ищется новый `.ulg`, созданный после начала текущего теста;
3. копируется только найденный файл, а не вся историческая папка;
4. создаётся отдельный каталог в `<каталог проекта>/logs`.

Пример:

```text
<каталог проекта>/logs/2026-09-22_22_11_59/22_11_59.ulg
```

При `--skip-px4` автоматическое копирование нового ULog отключено, поскольку
сценарий не владеет жизненным циклом уже запущенного PX4.

---

## 10. Тестирование проекта

### 10.1 Unit-тесты

Unit-тесты не запускают PX4 или Gazebo:

```bash
cd ~/spoof_gaz
./venv/bin/python -m unittest -v test_spoof_gaz.py
```

В текущей версии выполняются **24 unit-теста**.

Проверяются:

- GPS-интенсивность;
- GPS-топик;
- active/idle-расписание;
- IMU-интенсивность;
- знак базовой гравитации в Gazebo FLU;
- динамический `entity_name`;
- валидация периодического режима;
- формирование команды `g.py`;
- формирование IMU relay-команды и activation controls;
- выбор `gps` / `imu` / `both`;
- актуальный MAVSDK `udpin` endpoint;
- совместимость старых импортируемых имён `run_test.py`.

### 10.2 Синтаксическая проверка

```bash
cd ~/spoof_gaz
./venv/bin/python -m compileall -q g.py i.py run_test.py spoof_runtime.py spoof_test test_spoof_gaz.py
```

### 10.3 Короткая реальная публикация без PX4

```bash
python3 g.py --topic /spoof_gaz/gps_test --rate-hz 10 --duration 1
python3 i.py --topic /spoof_gaz/imu_test --rate-hz 20 --duration 1
```

### 10.4 Проверенные интеграционные прогоны

- GPS: `85 %`, `75 Гц`, `60 сек`, periodic `5 сек active / 4 сек idle`;
- IMU: relay штатного сенсора, `85 %`, offset Z `0.8 рад/с`, `75 Гц`,
  periodic `3/2`, `12 сек`;
- в обоих случаях миссия загружена, PX4 армирован, подтверждён режим `MISSION`,
  ULog сохранён, после cleanup не осталось PX4/Gazebo/MAVSDK процессов;
- в итоговом IMU-прогоне не было duplicate timestamp, sensor timeout или
  failsafe после запуска relay.

---

## 11. Типовой рабочий процесс

### Проверка после изменений

```bash
cd ~/spoof_gaz
./venv/bin/python -m compileall -q g.py i.py run_test.py spoof_runtime.py spoof_test test_spoof_gaz.py
./venv/bin/python -m unittest -v test_spoof_gaz.py
python3 g.py --check
python3 i.py --check
```

### Проверка полной команды без запуска

```bash
python3 run_test.py \
  --dry-run \
  --non-interactive \
  --skip-mission \
  --intensity 60 \
  --rate-hz 25 \
  --active-seconds 2 \
  --idle-seconds 3 \
  --duration 30
```

### Короткий полный тест без миссии

```bash
python3 run_test.py \
  --non-interactive \
  --skip-mission \
  --px4-startup-delay 15 \
  --stabilization-delay 0 \
  --wait-after-spoof 0 \
  --duration 2 \
  --intensity 50 \
  --rate-hz 20
```

---

## 12. Диагностика

### `gz.msgs10` или `gz.transport13` не найден

Убедитесь, что команда выполняется внутри Ubuntu WSL:

```bash
python3 g.py --check
python3 i.py --check
```

Windows Python обычно не содержит Gazebo bindings.

### MAVSDK не найден

```bash
~/spoof_gaz/venv/bin/python -m pip install mavsdk
```

### PX4 не найден

Проверьте зафиксированный путь:

```bash
test -d ~/PX4-Autopilot && echo OK
```

### Не найден `mission.plan`

Поместите QGroundControl Plan рядом с `run_test.py`:

```text
<каталог проекта>/mission.plan
```

или запустите с `--skip-mission`.

### Периодический режим отклонён валидатором

Правильно:

```bash
--active-seconds 2 --idle-seconds 3
```

или:

```bash
--active-seconds 0 --idle-seconds 0
```

Неправильно:

```bash
--active-seconds 2 --idle-seconds 0
```

### Остались процессы после ручного прерывания

Проверка:

```bash
ps -ef | grep -E 'px4|gz sim|g.py|i.py' | grep -v grep
```

Обычно `ProcessSupervisor` сначала отправляет `SIGTERM`, ждёт завершения и
только затем применяет принудительный `SIGKILL`.

Перед новым управляемым запуском `run_test.py` также находит и останавливает
stale-процессы PX4, `gz sim` и `mavsdk_server` текущего пользователя. Это
устраняет ситуацию `gazebo already running world: default` после аварийного
завершения предыдущего теста.

---

## 13. Справка CLI

Всегда используйте встроенную справку как источник актуального списка
параметров:

```bash
python3 run_test.py --help
python3 g.py --help
python3 i.py --help
```