# Руководство по запуску

[Содержание](README.md) · [Установка](INSTALL.md) · [Код](ARCHITECTURE.md)

Все примеры предполагают установленный стенд из [INSTALL.md](INSTALL.md) и каталог `~/spoof_gaz`; если вы распаковали проект в `~/_spoof_gaz`, перейдите в него вместо этого. Работайте только со **своим** PX4 SITL, не с физическим аппаратом. `run_test.py` сам подбирает Linux venv; отдельные `g.py` и `i.py` можно проверять через системный `python3`, если Gazebo bindings установлены системно.

## Перед первым запуском

```bash
cd "$HOME/spoof_gaz"
python3 g.py --check
python3 i.py --check
python3 run_test.py --non-interactive --dry-run --spoof-sensor gps --skip-mission
```

`--dry-run` проверяет конфигурацию и показывает команды, но **не запускает** Gazebo и не подтверждает, что миссия полетит. При первом настоящем запуске убедитесь, что нет другого ручного экземпляра PX4/Gazebo: сценарий может завершить оставшиеся процессы симуляции **текущего пользователя**.

## Безопасная ступенчатая проверка

1. Без миссии, с короткой публикацией GPS (нет арминга):

   ```bash
   python3 run_test.py --non-interactive --spoof-sensor gps --skip-mission \
     --intensity 0 --duration 3 --wait-after-spoof 0
   ```

2. Короткий запуск штатной миссии с GPS на опорных координатах (интенсивность `0 %`, **не гарантирует** равенство координат реальному GNSS модели):

   ```bash
   python3 run_test.py --non-interactive --spoof-sensor gps \
     --intensity 0 --duration 5 --wait-after-spoof 1
   ```

   Ищите **подтверждения** `PX4 готов к армингу`, `Миссия загружена` и `режим MISSION`. Код завершения `0` подтверждает сценарий, а не завершение **всего** полётного задания: через пять секунд аппарат может ещё набирать высоту.

3. Только затем задавайте смещение. Интерактивно: `python3 run_test.py` и ответьте на вопросы (тип сенсора: `1=GPS`, `2=IMU`, `3=оба`). Без вопросов используйте `--non-interactive`.

## GPS и периодические фазы

```bash
python3 run_test.py --non-interactive --spoof-sensor gps \
  --intensity 85 --rate-hz 75 --duration 60 \
  --active-seconds 5 --idle-seconds 4
```

`intensity=0` публикует опорную позицию, `100` — целевую; координаты и высоту можно переопределить `--reference-lat/lon/alt` и `--target-lat/lon/alt`. Формула: `reference + (target - reference) * intensity / 100`. Если оба значения `active/idle` равны нулю, публикация непрерывна; если оба положительны, действуют циклы активной фазы и паузы. Задавать только одно положительное нельзя. В паузе GPS publisher **не публикует** ложные сообщения, это не означает принудительного возврата к реальной позиции.

`g.py` — самостоятельный публикатор: `python3 g.py --dry-run --intensity 50` проверяет настройки без Gazebo, `python3 g.py --duration 5 --rate-hz 20` публикует в штатный NavSat topic, если существует мир и модель. Самостоятельный запуск **не** загружает миссию и **не** запускает PX4.

## IMU

Полный IMU-режим доступен **только после применения и пересборки PX4 patch** из [INSTALL.md](INSTALL.md#4-imu-патч-px4-обязательно-для-imu-и-both). Для знакомства с параметрами без симуляции: `python3 i.py --dry-run`. Для проверки bindings: `python3 i.py --check`.

```bash
python3 run_test.py --non-interactive --spoof-sensor imu \
  --imu-intensity 85 --imu-angular-z 0.8 --imu-rate-hz 75 \
  --imu-active-seconds 3 --imu-idle-seconds 2 --imu-duration 12
```

В режиме `run_test.py` файл `i.py` не генерирует статический IMU вместо движущегося аппарата: он читает штатный топик Gazebo, пересылает **все** входные IMU сообщения в `/spoof_gaz/imu` и добавляет offset к части сообщений во время active-фазы. `--imu-rate-hz` задаёт частоту **применения offset**, не частоту пересылки baseline (~250 Гц у x500); в idle-фазе baseline продолжает идти. `--imu-angular-{x,y,z}` — смещения гироскопа в рад/с, `--imu-linear-{x,y,z}` — смещения акселерометра в м/с² при `100 %`. Векторы в Gazebo FLU, bridge переводит их в PX4 FRD. `i.py --duration 5` **без** `--source-topic` — отдельный генератор, не равноценный полному relay-тесту.

## Совместный режим

```bash
python3 run_test.py --non-interactive --spoof-sensor both \
  --intensity 85 --rate-hz 75 --active-seconds 5 --idle-seconds 4 --duration 20 \
  --imu-intensity 85 --imu-rate-hz 75 \
  --imu-active-seconds 5 --imu-idle-seconds 4 --imu-duration 20
```

Перед запуском миссии включается baseline IMU relay; после подтверждения `MISSION` он получает сигнал активации; затем начинается GPS-публикация. PX4 получает только выходной IMU-топик relay, **не** два конкурирующих IMU-потока. Если цель — только демонстрация публикации, добавьте `--skip-mission` (без полёта). `--skip-px4` предполагает, что PX4 уже запущен и что для IMU ему **заранее** задан `PX4_GZ_IMU_TOPIC=/spoof_gaz/imu`.

## Миссия и логи

Файл `<каталог проекта>/mission.plan` — QGroundControl Plan; нельзя подменять его простым CSV. Сценарий отправляет GCS heartbeat, подключает MAVSDK к `udpin://0.0.0.0:14540`, ждёт position/home/armable, загружает миссию, армит и подтверждает режим `MISSION`. При неуспехе обязательной миссии сценарий завершается ошибкой, а не выдаёт ложное сообщение об успехе. `--mission-timeout 60` позволяет увеличить тайм-аут **каждого** шага MAVSDK при медленном запуске; `--skip-mission` отключает загрузку, арминг и heartbeat.

После запуска с собственным PX4 новый `.ulg` копируется в `<каталог проекта>/logs/<дата_время>/`; при `--skip-px4` копирования нет. Чтобы проверить реальный взлёт/состояние оценивателя, анализируйте ULog PX4, а не только консоль. `consolidate_logs.py` — **необязательная** обработка логов: требует `pandas`, CLI `ulog2csv` из `pyulog`, жёстко смотрит в `~/spoof_gaz/logs` и **удаляет обработанные папки после успешного объединения CSV**. Не запускайте его, пока не сделали резервную копию исходных `.ulg`; для `_spoof_gaz` он без изменения константы `LOGS_BASE` смотрит не туда.

## Неполадки

| Сообщение / симптом | Действие |
| --- | --- |
| `Gazebo Python` не найден | [Установка](INSTALL.md#3-создание-собственного-виртуального-окружения): системные bindings + `--system-site-packages` |
| PX4 пишет `No connection to the GCS` | До начала миссии это возможно; после запуска heartbeat проверьте строку `GCS heartbeat запущен`, UDP `18570` и установленный `pymavlink` |
| `Тайм-аут MAVSDK` / после `GCS heartbeat` нет ответа | Проверить PX4/Gazebo, UDP `14540`, процессы другого запуска; увеличить `--mission-timeout`; отсутствие GUI само по себе не доказывает, что PX4 не запущен |
| `gazebo already running world: default` | Найдите другой экземпляр, завершите **свой** ручной запуск; скрипт очищает stale-процессы текущего пользователя перед управляемым запуском |
| IMU: `STALE`, `TIMEOUT` или нет `using IMU topic override` | Пересоберите патченный PX4, убедитесь, что relay запущен и source/output topics совпадают с моделью |
| `mission.plan` отсутствует | Поместить план рядом с `run_test.py` либо добавить `--skip-mission` |
| Ошибка периодического режима | Установить **оба** значения `active/idle` > 0, либо оба равными 0 |

Полная справка: `python3 run_test.py --help`, `python3 g.py --help`, `python3 i.py --help`.