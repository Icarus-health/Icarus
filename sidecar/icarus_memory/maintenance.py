"""Exklusiver Wartungsmodus für konsistente Installationssicherungen.

Icarus verteilt seinen verbindlichen Zustand auf mehrere SQLite-Datenbanken.
Jede davon lässt sich einzeln konsistent sichern; ohne gemeinsame Schranke könnte
aber zwischen zwei Datenbanken noch eine Aufgabe, Notiz oder Einstellung
verändert werden. Das Ergebnis wäre formal lesbar, aber kein gemeinsamer
Zeitpunkt des Systems.

Der Gate erfüllt deshalb drei Zusagen:

* Bereits laufende Anfragen dürfen geordnet fertig werden.
* Sobald eine Sicherung oder Wiederherstellung wartet, werden keine neuen
  normalen Anfragen mehr angenommen.
* Genau eine Wartungsoperation erhält exklusiven Zugriff.

Die Schranke ist bewusst pro Prozess. Mehrere Sidecar-Prozesse auf demselben
Datenverzeichnis bleiben weiterhin unzulässig und gehören später in einen
separaten Instanz-Lock.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from threading import Condition
from typing import Iterator


@dataclass(frozen=True)
class MaintenanceState:
    active_requests: int
    maintenance: bool
    waiting_maintenance: int
    operation: str | None

    def to_dict(self) -> dict[str, int | bool | str | None]:
        return {
            "active_requests": self.active_requests,
            "maintenance": self.maintenance,
            "waiting_maintenance": self.waiting_maintenance,
            "operation": self.operation,
        }


class MaintenanceGate:
    """Leser-/Wartungsschranke mit Vorrang für wartende Wartungsoperationen."""

    def __init__(self) -> None:
        self._condition = Condition()
        self._active_requests = 0
        self._maintenance = False
        self._waiting_maintenance = 0
        self._operation: str | None = None

    def try_enter_request(self) -> bool:
        """Nimmt eine normale Anfrage an, sofern keine Wartung läuft oder wartet.

        Wartende Wartung blockiert neue Anfragen bereits vor ihrem eigentlichen
        Eintritt. Sonst könnte ein stetiger Strom kurzer Requests eine Sicherung
        unbegrenzt verhungern lassen.
        """
        with self._condition:
            if self._maintenance or self._waiting_maintenance:
                return False
            self._active_requests += 1
            return True

    def leave_request(self) -> None:
        with self._condition:
            if self._active_requests <= 0:
                raise RuntimeError("Wartungsschranke ohne aktive Anfrage verlassen.")
            self._active_requests -= 1
            if self._active_requests == 0:
                self._condition.notify_all()

    @contextmanager
    def exclusive(self, operation: str) -> Iterator[None]:
        """Wartet auf laufende Anfragen und führt genau eine Wartung exklusiv aus."""
        with self._condition:
            self._waiting_maintenance += 1
            try:
                while self._maintenance:
                    self._condition.wait()
                self._maintenance = True
                self._operation = operation
                while self._active_requests:
                    self._condition.wait()
            finally:
                self._waiting_maintenance -= 1

        try:
            yield
        finally:
            with self._condition:
                self._maintenance = False
                self._operation = None
                self._condition.notify_all()

    def state(self) -> MaintenanceState:
        with self._condition:
            return MaintenanceState(
                active_requests=self._active_requests,
                maintenance=self._maintenance,
                waiting_maintenance=self._waiting_maintenance,
                operation=self._operation,
            )


__all__ = ["MaintenanceGate", "MaintenanceState"]
