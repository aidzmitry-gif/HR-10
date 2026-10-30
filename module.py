"""Модуль HR (Кадры) — реализация ModuleContract."""
from __future__ import annotations

from core.runtime.contract import ModuleContract, Widget
from core.runtime.core import Core
from modules.hr import profile_routes, routes, worktime_reminders, worktime_routes


class HrModule(ModuleContract):
    name = "hr"
    version = "0.1.0"
    api_prefix = "/hr"

    def register(self, core: Core) -> None:
        core.include_router(routes.router, prefix=self.api_prefix)
        core.include_router(profile_routes.router, prefix=self.api_prefix)
        core.include_router(worktime_routes.router, prefix=self.api_prefix)
        core.on_tick(worktime_reminders.tick)
        core.register_widget(Widget("hr", "HR", source="hr.employees"))


def get_module() -> ModuleContract:
    return HrModule()
