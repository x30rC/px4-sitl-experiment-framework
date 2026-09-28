"""Загрузка и запуск QGroundControl-миссии через MAVSDK."""

from __future__ import annotations

import asyncio
from pathlib import Path

from .paths import PROJECT_VENV_PYTHON


class MavsdkMissionService:
    """Инкапсулирует все обращения к MAVSDK и их тайм-ауты."""

    def __init__(
        self,
        timeout_seconds: float,
        system_address: str = "udpin://0.0.0.0:14540",
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.system_address = system_address

    async def _first_matching(self, stream, predicate):
        async def consume():
            async for item in stream:
                if predicate(item):
                    return item
            raise RuntimeError("поток MAVSDK завершился до ожидаемого состояния")

        return await asyncio.wait_for(consume(), timeout=self.timeout_seconds)

    @staticmethod
    def _health_is_ready(health) -> bool:
        return (
            health.is_local_position_ok
            and health.is_global_position_ok
            and health.is_home_position_ok
            and health.is_armable
        )

    async def _run_mission(self, plan_path: Path) -> bool:
        from mavsdk import System

        print(
            f"Запуск MAVSDK и подключение к {self.system_address} "
            f"(тайм-аут {self.timeout_seconds:g} сек)..."
        )
        drone = System()
        await asyncio.wait_for(
            drone.connect(system_address=self.system_address),
            timeout=self.timeout_seconds,
        )
        await self._first_matching(
            drone.core.connection_state(),
            lambda state: state.is_connected,
        )
        print("Дрон подключён.")

        print(
            "Ожидание локальной/глобальной позиции, home и разрешения арминга "
            f"(тайм-аут {self.timeout_seconds:g} сек)..."
        )
        await self._first_matching(
            drone.telemetry.health(),
            self._health_is_ready,
        )
        print("PX4 готов к армингу.")

        print(f"Загрузка миссии из {plan_path}...")
        imported_mission = await asyncio.wait_for(
            drone.mission_raw.import_qgroundcontrol_mission(str(plan_path)),
            timeout=self.timeout_seconds,
        )
        await asyncio.wait_for(
            drone.mission_raw.upload_mission(imported_mission.mission_items),
            timeout=self.timeout_seconds,
        )
        await asyncio.wait_for(
            drone.mission_raw.set_current_mission_item(0),
            timeout=self.timeout_seconds,
        )
        print(f"Миссия загружена: {len(imported_mission.mission_items)} элементов.")

        print("Арминг и запуск миссии...")
        await asyncio.wait_for(drone.action.arm(), timeout=self.timeout_seconds)
        await self._first_matching(drone.telemetry.armed(), lambda is_armed: is_armed)
        await asyncio.wait_for(
            drone.mission_raw.start_mission(),
            timeout=self.timeout_seconds,
        )
        from mavsdk.telemetry import FlightMode

        await self._first_matching(
            drone.telemetry.flight_mode(),
            lambda flight_mode: flight_mode == FlightMode.MISSION,
        )
        progress = await self._first_matching(
            drone.mission_raw.mission_progress(),
            lambda value: value.total == len(imported_mission.mission_items),
        )
        print(
            "Миссия запущена и подтверждена PX4: "
            f"режим MISSION, пункт {progress.current}/{progress.total}."
        )
        return True

    def upload_and_start(self, plan_path: Path) -> bool:
        try:
            import mavsdk  # noqa: F401
        except ImportError:
            print(
                "MAVSDK не найден; обязательную миссию запустить невозможно. "
                f"Установите зависимость через {PROJECT_VENV_PYTHON} -m pip install mavsdk"
            )
            return False

        try:
            return asyncio.run(self._run_mission(plan_path))
        except TimeoutError:
            print(
                f"Тайм-аут MAVSDK после {self.timeout_seconds:g} сек. "
                "PX4 не подтвердил готовность или запуск миссии."
            )
            return False
        except Exception as exc:
            print(f"Не удалось загрузить или запустить миссию: {exc}")
            return False