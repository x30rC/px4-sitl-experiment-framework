# Установка на новую машину

[Содержание](README.md) · [Запуск](USAGE.md) · [Код](ARCHITECTURE.md)

## 0. Что поддерживается

Проверенное окружение: Windows 11 + WSL2 с Ubuntu 24.04, Python 3.12, PX4 SITL (`gz_x500`) и Gazebo Harmonic (Sim 8, `gz.msgs10` / `gz.transport13`). Установка ниже также рассчитана на обычную Ubuntu 24.04 с рабочим графическим окружением. Это **не** инструкция для Windows Python, реального дрона или произвольной версии PX4. Для GUI Gazebo в WSL нужен работающий WSLg/графический сервер; headless-стенд требует дополнительной настройки PX4/Gazebo.

Потребуются доступ в сеть для установки пакетов и сборки PX4, `sudo`, место под PX4 и его сборку, а также доступное аппаратное ускорение графики для Gazebo GUI. Проект **не** поставляет сам PX4/Gazebo: исходники фреймворка доступны в публичном репозитории `x30rC/px4-sitl-experiment-framework`. Не копируйте Windows-venv: он не совместим с Linux.

## 1. Ubuntu / WSL2

На Windows установите WSL2 и дистрибутив Ubuntu 24.04 штатными средствами Windows; откройте терминал **Ubuntu**, обновите пакеты и подготовьте Python:

```bash
sudo apt update
sudo apt install -y git make cmake ninja-build python3 python3-venv python3-pip python3-dev
python3 --version
```

Для PX4 и Gazebo используйте [официальную инструкцию PX4 для Ubuntu](https://docs.px4.io/main/en/dev_setup/dev_env_linux_ubuntu) и [инструкцию Gazebo Harmonic для Ubuntu](https://gazebosim.org/docs/harmonic/install_ubuntu/). У PX4 есть скрипт установки зависимостей `Tools/setup/ubuntu.sh`; перед запуском ознакомьтесь с ним и требованиями нужной ревизии PX4. Пакеты Gazebo Harmonic и Python-bindings `python3-gz-msgs10`, `python3-gz-transport13` на проверенном стенде получены из Gazebo/OSRF Ubuntu repository, **не из pip**. Не смешивайте их с несовместимыми major-версиями Gazebo. Если PX4 setup ещё не настроил OSRF-репозиторий, подключите его по официальной инструкции Gazebo:

```bash
sudo apt install -y curl lsb-release gnupg
sudo curl https://packages.osrfoundation.org/gazebo.gpg --output /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg] https://packages.osrfoundation.org/gazebo/ubuntu-stable $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/gazebo-stable.list > /dev/null
sudo apt update
```

Проверить доступность пакетов и bindings:

```bash
apt-cache policy gz-harmonic python3-gz-msgs10 python3-gz-transport13
sudo apt install -y gz-harmonic python3-gz-msgs10 python3-gz-transport13
python3 -c 'from gz.msgs10.imu_pb2 import IMU; from gz.transport13 import Node; print("Gazebo Python OK")'
```

Если `apt-cache` не находит пакеты, сначала настройте официальный репозиторий Gazebo для **Ubuntu noble**. Не подменяйте отсутствующие системные Python bindings случайным `pip install gz`.

## 2. PX4 и исходники проекта

PX4 должен находиться **строго в `~/PX4-Autopilot`**: этот путь задан в `spoof_test/paths.py`. На проверенном стенде использована ревизия `0bb36a30ed4390f327c04f8f5d7c5cdcb8e9d235`; для других ревизий патч IMU ниже может потребовать адаптации. Чтобы воспроизвести совместимость патча на новой машине:

```bash
cd "$HOME"
git clone --recursive https://github.com/PX4/PX4-Autopilot.git
cd "$HOME/PX4-Autopilot"
git checkout 0bb36a30ed4390f327c04f8f5d7c5cdcb8e9d235
git submodule update --init --recursive
bash Tools/setup/ubuntu.sh
```

Установщик PX4 может потребовать подтверждения, перезапуска WSL/Ubuntu и загрузки зависимостей; следуйте сообщениям скрипта. После перезапуска соберите и проверьте SITL:

```bash
test -d "$HOME/PX4-Autopilot" && echo 'PX4 найден'
cd "$HOME/PX4-Autopilot"
make px4_sitl gz_x500
```

Дождитесь запуска мира и PX4, затем остановите этот **ручной** тест `Ctrl+C` до запуска полного сценария. Первое построение может занимать значительное время. Теперь получите исходники фреймворка (пример использует каталог `~/spoof_gaz`; если он уже существует, выберите другой каталог или предварительно разберитесь с его содержимым):

```bash
cd "$HOME"
git clone https://github.com/x30rC/px4-sitl-experiment-framework.git spoof_gaz
cd "$HOME/spoof_gaz"
test -f run_test.py && test -f mission.plan && test -f doc/px4-gz-imu-topic.patch
```

Если папка называется `_spoof_gaz`, замените в командах `~/spoof_gaz` на `~/_spoof_gaz`, в том числе в пути к патчу в шаге 4. Файлы миссии/логов выбираются из **запущенной копии**; PX4 по-прежнему ищется в `~/PX4-Autopilot`.

## 3. Создание собственного виртуального окружения

Не пользуйтесь запасным venv из соседней копии на новой машине. Создайте venv с доступом к **системным** Python-bindings Gazebo:

```bash
cd "$HOME/spoof_gaz"
python3 -m venv --system-site-packages venv
./venv/bin/python -m pip install --upgrade pip
./venv/bin/python -m pip install -r requirements.txt
./venv/bin/python -c 'import mavsdk, pymavlink; from gz.msgs10.imu_pb2 import IMU; from gz.transport13 import Node; print("Зависимости доступны")'
```

`mavsdk` и `pymavlink` нужны для обязательной миссии и GCS heartbeat. Для **необязательной** пакетной CSV-обработки логов (`consolidate_logs.py`) дополнительно потребуются `pandas` и `pyulog` (включая утилиту `ulog2csv`):

```bash
./venv/bin/python -m pip install pandas pyulog
```

Не переносите и не используйте повреждённый `venv/bin/python` (например, пустой файл); удаляйте **только локальный venv, который вы собираетесь пересоздать**, затем повторяйте `python3 -m venv --system-site-packages venv`. Сценарий `python3 run_test.py` сам переключается на работоспособный venv, но на новой машине лучше всегда создавать локальный. `source venv/bin/activate` не обязателен.

## 4. IMU: патч PX4 (обязательно для `imu` и `both`)

Для GPS-режима изменения исходного PX4 не нужны. Для **IMU** этот проект требует, чтобы PX4 принимал IMU из отдельного `/spoof_gaz/imu`, иначе штатный и подменённый потоки конфликтуют. Приложенный патч добавляет чтение `PX4_GZ_IMU_TOPIC` и защиту от повторного IMU timestamp в одном tick. Применяйте к указанной выше ревизии PX4 или предварительно проверьте совместимость вашей ветки:

```bash
cd "$HOME/PX4-Autopilot"
git apply --check "$HOME/spoof_gaz/doc/px4-gz-imu-topic.patch"
git apply "$HOME/spoof_gaz/doc/px4-gz-imu-topic.patch"
cmake --build build/px4_sitl_default --target px4 -j2
```

Если `git apply --check` не проходит, **не** применяйте патч с `--reject` вслепую: ваша версия PX4 отличается или он уже установлен. Проверьте `git diff` и наличие `PX4_GZ_IMU_TOPIC` в `src/modules/simulation/gz_bridge/GZBridge.cpp`; при необходимости адаптируйте патч к своей ветке и соберите её заново. Патч меняет **другой репозиторий** (`~/PX4-Autopilot`), `pip install` сам по себе это не делает. `run_test.py` устанавливает переменную окружения автоматически в IMU/both; для `--skip-px4` внешний PX4 нужно запустить с тем же значением заранее.

## 5. Проверка установки

```bash
cd "$HOME/spoof_gaz"
python3 g.py --check
python3 i.py --check
./venv/bin/python -m unittest -v test_spoof_gaz.py
python3 run_test.py --dry-run --non-interactive --spoof-sensor gps --skip-mission
```

После успешного dry-run запустите **сначала без арминга**:

```bash
python3 run_test.py --non-interactive --spoof-sensor gps --skip-mission --duration 3 --wait-after-spoof 0
```

Успех означает не только код `0`, но и работающий Gazebo, корректные сообщения публикатора и ULog в `logs/`. Затем выполните короткий полёт и только потом экспериментируйте с IMU: примеры в [USAGE.md](USAGE.md).

## Если установка не удалась

| Симптом | Что проверить |
| --- | --- |
| `PX4 не найден` | Наличие `~/PX4-Autopilot`, версия и сборка `gz_x500` |
| `gz.msgs10` / `gz.transport13` не найден | Пакеты Gazebo bindings установлены в Ubuntu; venv создан с `--system-site-packages`; `python3 i.py --check` |
| `mavsdk` / `pymavlink` не найден | `./venv/bin/python -m pip show mavsdk pymavlink`; правильная копия проекта и venv |
| `gazebo already running` | Завершить собственный ручной запуск; автоматическая очистка управляемого сценария может остановить **другой ваш** локальный PX4/Gazebo |
| IMU не работает при работающем GPS | Применён ли патч **к тому PX4, который запускается**, и выполнена ли пересборка; в выводе ищите `using IMU topic override` |
| GUI не виден | Проверить графическое окружение WSLg/Ubuntu отдельно от подключения MAVSDK; использовать официальную диагностику Gazebo |

См. также [диагностику запуска](USAGE.md#неполадки).