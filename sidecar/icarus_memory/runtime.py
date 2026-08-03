"""Produktions-Einstieg des Icarus-Sidecars.

`server.py` bleibt die fachliche HTTP-Anwendung und kann in Unit-Tests direkt
gebaut werden. Dieser Laufzeitrahmen ergänzt die betriebliche Zusage, die erst
mit mehreren Datenbanken notwendig wurde: vollständige Sicherung und Restore
laufen exklusiv gegenüber normalen API-Anfragen.

Container, installierter Konsolenbefehl und PyInstaller-Binary verwenden diesen
Einstieg. Damit gibt es keinen zweiten Sicherheits- oder Datenpfad — nur eine
Schranke um dieselben Endpunkte und denselben Scheduler.
"""

from __future__ import annotations

import atexit
from functools import wraps
from typing import Any, Callable
from weakref import WeakKeyDictionary

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import server
from .graph_privacy import install_graph_privacy
from .maintenance import MaintenanceGate
from .private_beta import install_private_beta
from .proactive import install_proactive
from .world_intelligence import install_world_intelligence

_ORIGINAL_CREATE_APP = server.create_app
_ORIGINAL_WIRE_SCHEDULER = server._wire_scheduler  # noqa: SLF001
_GATES: WeakKeyDictionary[FastAPI, MaintenanceGate] = WeakKeyDictionary()
_MAINTENANCE_PATHS = {
    ("POST", "/backups"): "backup",
    ("POST", "/backups/restore"): "restore",
}


def _wrap_scheduled_backup(app: FastAPI, gate: MaintenanceGate) -> None:
    scheduler = getattr(app.state, "scheduler", None)
    job = getattr(scheduler, "_run_backup", None)
    if job is None or getattr(job, "__icarus_maintenance_wrapped__", False):
        return

    @wraps(job)
    def guarded_backup() -> Any:
        with gate.exclusive("scheduled_backup"):
            return job()

    guarded_backup.__icarus_maintenance_wrapped__ = True  # type: ignore[attr-defined]
    scheduler._run_backup = guarded_backup  # noqa: SLF001


def _wire_scheduler(app: FastAPI) -> None:
    """Erhält die Schranke auch nach Änderungen an den Einstellungen.

    `server._wire_scheduler()` ersetzt den Backup-Job, wenn der Agent nach einer
    Einrichtung oder einem Restore neu gebaut wird. Direkt danach wird derselbe
    neue Job wieder durch die Wartungsschranke geführt.
    """
    _ORIGINAL_WIRE_SCHEDULER(app)
    gate = _GATES.get(app)
    if gate is not None:
        _wrap_scheduled_backup(app, gate)


# `server._build_agent()` schaut diese Funktion im Modul zur Laufzeit nach.
# Einmaliges Ersetzen hält deshalb auch spätere Neuverdrahtungen konsistent.
server._wire_scheduler = _wire_scheduler  # type: ignore[attr-defined]  # noqa: SLF001


def _ensure_shutdown_registration(app: FastAPI) -> None:
    """Überbrückt FastAPI-Versionen mit und ohne `add_event_handler`.

    Neuere FastAPI-/Starlette-Versionen haben den alten Komfortweg entfernt.
    Der Sidecar ist ein einzelner Prozess; als belastbarer Mindestvertrag wird
    das Aufräumen deshalb am Prozessende registriert. Ältere Versionen nutzen
    weiterhin zusätzlich ihren nativen Shutdown-Hook.
    """
    if hasattr(app, "add_event_handler"):
        return

    def add_event_handler(event_type: str, handler: Callable[[], Any]) -> None:
        if event_type == "shutdown":
            atexit.register(handler)

    app.add_event_handler = add_event_handler  # type: ignore[attr-defined]


def create_app(*args: Any, **kwargs: Any) -> FastAPI:
    app = _ORIGINAL_CREATE_APP(*args, **kwargs)
    _ensure_shutdown_registration(app)

    # Graph, Modellrouting, Browser und dauerhafte Workflows werden hier am
    # echten Produktionsweg montiert. Das geschieht vor der Wartungsschicht,
    # damit sämtliche neuen Routen anschließend dieselbe exklusive
    # Backup-/Restore-Garantie erhalten.
    data_dir = server._data_dir()  # noqa: SLF001
    runtime = install_private_beta(app, data_dir)
    install_graph_privacy(runtime)
    install_proactive(app, data_dir)

    # `find_project` löst Kennung und Namen sicher auf. Die Weltquellen-API
    # erwartet einen optionalen Lookup mit None statt einer Ausnahme.
    if not hasattr(app.state.workspace, "get_project"):
        app.state.workspace.get_project = app.state.workspace.find_project
    install_world_intelligence(app, data_dir)

    gate = MaintenanceGate()
    _GATES[app] = gate
    app.state.maintenance = gate
    _wrap_scheduled_backup(app, gate)

    @app.middleware("http")
    async def maintenance_middleware(
        request: Request,
        call_next: Callable[[Request], Any],
    ):
        # Healthchecks müssen auch während einer längeren Sicherung erkennen,
        # dass der Prozess lebt. Der angemeldete Health-Endpunkt zeigt unten
        # zusätzlich den Wartungszustand.
        if request.url.path == "/health":
            response = await call_next(request)
            state = gate.state()
            response.headers["x-icarus-maintenance"] = (
                state.operation or "waiting" if state.maintenance else "none"
            )
            return response

        operation = _MAINTENANCE_PATHS.get((request.method, request.url.path))
        if operation is not None:
            with gate.exclusive(operation):
                return await call_next(request)

        if not gate.try_enter_request():
            state = gate.state()
            return JSONResponse(
                status_code=503,
                headers={"Retry-After": "2"},
                content={
                    "detail": (
                        "Icarus führt gerade eine vollständige Sicherung oder "
                        "Wiederherstellung durch. Bitte gleich noch einmal versuchen."
                    ),
                    "maintenance": True,
                    "operation": state.operation,
                },
            )

        try:
            return await call_next(request)
        finally:
            gate.leave_request()

    return app


def main() -> None:
    # `server.main()` verwendet den Namen `create_app` aus seinem eigenen Modul.
    # Der Produktionsstart zeigt deshalb auf den Laufzeitrahmen, ohne den
    # Servercode oder seine CLI-Argumente zu duplizieren.
    server.create_app = create_app
    server.main()


__all__ = ["create_app", "main"]
