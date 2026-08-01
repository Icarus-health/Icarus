"""Der Prozess, der mitläuft.

Bis hierher passierte alles auf Zuruf: Aufnehmen, wenn jemand auf „Aufnehmen“
drückt; Verdichten, wenn jemand auf „Verdichten“ drückt. Für ein Werkzeug ist
das richtig. Für einen Assistenten, der ein Arbeitsleben begleiten soll, ist es
zu wenig — was nur passiert, wenn man daran denkt, passiert nicht.

## Was er darf und was nicht

Genau das, was die Verdichtung ohnehin darf: **ordnen, nicht behaupten.**

| Läuft von selbst | Passiert nie ohne Menschen |
| --- | --- |
| Ordner erneut einlesen (Digest verhindert Doppel) | Eine Aussage in den Bestand schreiben |
| Regelbasierte Vorschläge erzeugen | Einen Vorschlag annehmen |
| Alte Monate zusammenfassen (Quellen bleiben) | Eine Quelle löschen |
| Sicherung anlegen | Etwas Außenwirksames tun |

Der Zeitplan macht die Vorschlagsschlange voller, nicht den Bestand. Das ist
die einzige Eigenschaft, die diesen Prozess unbedenklich macht: Im schlimmsten
Fall entsteht Arbeit, die jemand ignoriert — nie ein falscher Fakt.

## Warum er standardmäßig aus ist

Zwei Gründe, und beide sind ernst.

**Kosten.** Die modellgestützte Ableitung ruft einen Anbieter. Ein Zeitplan, der
das ungefragt stündlich tut, gibt fremdes Geld aus. Deshalb ist nicht nur der
Zeitplan aus, sondern die Modellnutzung darin noch einmal getrennt zu schalten.

**Lärm.** Ein Prozess, der stündlich Unbrauchbares vorlegt, ist schlimmer als
keiner: Die Schlange wächst, niemand sieht mehr hinein, und dann ist auch das
Nützliche darin unsichtbar. Erst wenn die Vorschläge im Alltag taugen, gehört
der Takt hoch.

## Warum ein Thread und kein Cron

Icarus ist eine Desktop-App. Der Sidecar lebt, solange die App offen ist — ein
Systemdienst, der im Hintergrund weiterläuft, wäre eine andere Zusage als die,
die das Projekt gibt („alles bleibt auf diesem Rechner, und du siehst zu“).

Also: ein Thread im Sidecar. Er läuft, wenn die App läuft. Das ist ehrlich und
reicht: Wer die App eine Woche nicht öffnet, hat auch keine Vorschläge geprüft.

Im Container gilt dasselbe mit anderem Vorzeichen — dort läuft der Sidecar
dauerhaft, und der Zeitplan trägt tatsächlich durch die Nacht.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from .model import now

#: Kürzester zulässiger Abstand. Kein technisches Limit, sondern eine Bremse:
#: Alles darunter erzeugt Lärm, bevor jemand die erste Runde geprüft hat.
MIN_INTERVAL_MINUTES = 15

#: Voreinstellung, wenn jemand den Zeitplan einschaltet, ohne einen Takt zu
#: nennen. Vier Stunden heißt: ein paar Mal am Arbeitstag, nicht ständig.
DEFAULT_INTERVAL_MINUTES = 240

#: Wie oft der Thread aufwacht, um zu prüfen, ob etwas fällig ist. Klein genug,
#: dass eine Änderung am Takt schnell greift, groß genug, um nichts zu kosten.
TICK_SECONDS = 30.0


@dataclass
class JobResult:
    """Was ein einzelner Schritt getan hat."""

    name: str
    ok: bool = True
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunReport:
    started_at: datetime
    finished_at: datetime | None = None
    jobs: list[JobResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(j.ok for j in self.jobs)

    def to_dict(self) -> dict[str, Any]:
        def iso(v: datetime | None) -> str | None:
            return v.astimezone().isoformat() if v else None

        return {
            "started_at": iso(self.started_at),
            "finished_at": iso(self.finished_at),
            "ok": self.ok,
            "jobs": [j.to_dict() for j in self.jobs],
        }

    def summary(self) -> str:
        if not self.jobs:
            return "Nichts zu tun."
        return " · ".join(f"{j.name}: {j.detail or ('ok' if j.ok else 'Fehler')}"
                          for j in self.jobs)


class Scheduler:
    """Führt Aufnahme, Verdichtung und Sicherung nach Zeitplan aus.

    Die eigentliche Arbeit steckt in den übergebenen Funktionen. Diese Klasse
    kennt nur den Takt, den Zustand und die Regel, dass ein Fehler in einem
    Schritt die anderen nicht verhindert.
    """

    def __init__(
        self,
        run_ingest: Callable[[], list[JobResult]] | None = None,
        run_consolidation: Callable[[bool], JobResult] | None = None,
        run_backup: Callable[[], JobResult] | None = None,
        run_summary: Callable[[bool], JobResult] | None = None,
    ) -> None:
        self._run_ingest = run_ingest
        self._run_consolidation = run_consolidation
        self._run_summary = run_summary
        self._run_backup = run_backup

        self._enabled = False
        self._interval = timedelta(minutes=DEFAULT_INTERVAL_MINUTES)
        self._with_model = False

        self._last: RunReport | None = None
        self._last_at: datetime | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    # -- Einstellen --------------------------------------------------------

    def configure(
        self,
        enabled: bool | None = None,
        interval_minutes: int | None = None,
        with_model: bool | None = None,
    ) -> None:
        with self._lock:
            if enabled is not None:
                self._enabled = enabled
            if interval_minutes is not None:
                self._interval = timedelta(
                    minutes=max(MIN_INTERVAL_MINUTES, int(interval_minutes))
                )
            if with_model is not None:
                self._with_model = with_model

    @property
    def enabled(self) -> bool:
        return self._enabled

    def next_run(self) -> datetime | None:
        if not self._enabled:
            return None
        if self._last_at is None:
            return now()
        return self._last_at + self._interval

    def state(self) -> dict[str, Any]:
        return {
            "enabled": self._enabled,
            "interval_minutes": int(self._interval.total_seconds() // 60),
            "with_model": self._with_model,
            "min_interval_minutes": MIN_INTERVAL_MINUTES,
            "running": self._thread is not None and self._thread.is_alive(),
            "last_run": self._last.to_dict() if self._last else None,
            "next_run": (
                self.next_run().astimezone().isoformat() if self.next_run() else None
            ),
        }

    # -- Laufen ------------------------------------------------------------

    def run_once(self, with_model: bool | None = None) -> RunReport:
        """Ein Durchgang. Auch von Hand auslösbar.

        Jeder Schritt ist einzeln fehlertolerant. Ein Mailserver, der hakt, darf
        nicht verhindern, dass die Sicherung läuft — genau diese Kopplung macht
        Hintergrundprozesse unbrauchbar, weil sie irgendwann ganz ausfallen und
        niemand merkt, warum.
        """
        report = RunReport(started_at=now())
        modell = self._with_model if with_model is None else with_model

        if self._run_ingest is not None:
            try:
                report.jobs.extend(self._run_ingest())
            except Exception as exc:  # noqa: BLE001
                report.jobs.append(
                    JobResult("aufnahme", False, f"{type(exc).__name__}: {exc}")
                )

        if self._run_consolidation is not None:
            try:
                report.jobs.append(self._run_consolidation(modell))
            except Exception as exc:  # noqa: BLE001
                report.jobs.append(
                    JobResult("verdichtung", False, f"{type(exc).__name__}: {exc}")
                )

        if self._run_summary is not None:
            try:
                report.jobs.append(self._run_summary(modell))
            except Exception as exc:  # noqa: BLE001
                report.jobs.append(
                    JobResult("zusammenfassung", False, f"{type(exc).__name__}: {exc}")
                )

        if self._run_backup is not None:
            try:
                report.jobs.append(self._run_backup())
            except Exception as exc:  # noqa: BLE001
                report.jobs.append(
                    JobResult("sicherung", False, f"{type(exc).__name__}: {exc}")
                )

        report.finished_at = now()
        with self._lock:
            self._last = report
            self._last_at = report.finished_at
        return report

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        # Daemon: Der Thread darf das Beenden der App nicht aufhalten. Ein
        # abgebrochener Lauf ist folgenlos — er schreibt nur Vorschläge, und
        # der nächste Lauf holt alles nach.
        self._thread = threading.Thread(
            target=self._loop, name="icarus-zeitplan", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            # In kleinen Schritten warten statt einmal lang: Sonst hängt das
            # Beenden der App am Takt, und vier Stunden Wartezeit fühlen sich
            # wie ein Absturz an.
            if self._stop.wait(TICK_SECONDS):
                return
            if not self._enabled:
                continue
            faellig = self.next_run()
            if faellig is not None and now() >= faellig:
                try:
                    self.run_once()
                except Exception:  # noqa: BLE001 - der Thread darf nie sterben
                    # run_once fängt bereits jeden Schritt einzeln ab; was hier
                    # ankommt, wäre ein Fehler im Berichten selbst. Weiterlaufen
                    # ist trotzdem richtig: Ein Zeitplan, der nach einem
                    # Ausrutscher still aufhört, ist schlimmer als einer, der
                    # es erneut versucht.
                    with self._lock:
                        self._last_at = now()


# -- Die Schritte -----------------------------------------------------------
#
# Bewusst hier und nicht im Server: Sie sind ohne HTTP prüfbar.


def ingest_job(
    episodes: Any, roots: list[Path], adapters: dict[str, str]
) -> Callable[[], list[JobResult]]:
    """Liest die eingestellten Ordner erneut ein.

    Dass ein zweiter Lauf über denselben Vault nichts doppelt anlegt, ist keine
    Nettigkeit, sondern die Voraussetzung dafür, dass dieser Schritt überhaupt
    wiederholbar ist — sie steckt im Digest der Episodenschicht.
    """
    from .ingest import ingest_directory

    def run() -> list[JobResult]:
        ergebnisse: list[JobResult] = []
        for pfad, adapter in adapters.items():
            try:
                report = ingest_directory(episodes, pfad, adapter, roots=roots)
                ergebnisse.append(JobResult(
                    f"aufnahme:{Path(pfad).name}",
                    True,
                    f"{report.recorded} neu, {report.duplicates} bekannt",
                ))
            except Exception as exc:  # noqa: BLE001 - ein Ordner darf den Lauf nicht kippen
                ergebnisse.append(JobResult(
                    f"aufnahme:{Path(pfad).name}", False, f"{type(exc).__name__}: {exc}"
                ))
        return ergebnisse

    return run


def consolidation_job(consolidator: Any) -> Callable[[bool], JobResult]:
    def run(with_model: bool) -> JobResult:
        report = consolidator.run(with_model=with_model)
        return JobResult("verdichtung", not report.errors, report.summary())

    return run


def summary_job(summarizer: Any) -> Callable[[bool], JobResult]:
    """Fasst alte Monate zusammen — nach der Verdichtung, nicht davor.

    Die Reihenfolge ist keine Feinheit: Zusammenfassen archiviert die Quellen.
    Liefe es zuerst, verschwände Material aus der Verdichtung, das noch nie
    jemand angesehen hat. So sieht die Verdichtung erst alles, und erst danach
    wird gekürzt.
    """
    def run(with_model: bool) -> JobResult:
        report = summarizer.run(with_model=with_model)
        return JobResult("zusammenfassung", not report.errors, report.summary())

    return run


def backup_job(data_dir: Path) -> Callable[[], JobResult]:
    """Legt einen Snapshot an — der billigste Schritt mit dem größten Nutzen.

    Ein Gedächtnis, das zwanzig Jahre halten soll, hat genau einen
    katastrophalen Fehlerfall, und eine Sicherung, die nur läuft, wenn jemand
    daran denkt, verhindert ihn nicht.
    """
    from .backup import snapshot

    def run() -> JobResult:
        datenbank = data_dir / "self-model.sqlite3"
        if not datenbank.is_file():
            # Beim ersten Start gibt es noch nichts zu sichern, und im
            # Speicherbetrieb nie. Das als Fehler zu melden hieße, dass ein
            # neuer Nutzer bei jedem Lauf einen roten Schritt sieht — und wer
            # sich an rote Meldungen gewöhnt, übersieht die eine, die zählt.
            return JobResult("sicherung", True, "nichts zu sichern")
        pfad = snapshot(datenbank, data_dir / "sicherungen")
        return JobResult("sicherung", True, pfad.name)

    return run


__all__ = [
    "DEFAULT_INTERVAL_MINUTES",
    "MIN_INTERVAL_MINUTES",
    "TICK_SECONDS",
    "JobResult",
    "RunReport",
    "Scheduler",
    "backup_job",
    "consolidation_job",
    "ingest_job",
]
