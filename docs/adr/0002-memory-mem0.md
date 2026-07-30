# ADR 0002: Mem0 als Memory-Schicht

**Status:** ABGELÖST durch [ADR 0005](0005-cognee-statt-mem0.md) · **Datum:** 2026-07-30

> Mem0s Server braucht Postgres mit pgvector, also Docker. Für eine App, die
> ein Nicht-Techniker herunterlädt und startet, ist das die falsche
> Betriebsform. Das Dokument bleibt als Begründung erhalten.

## Kontext

Säule 2 verlangt ein Gedächtnis, das unabhängig von einzelnen KI-Anbietern verwaltet wird. Nach dem Wegfall von Letta ([ADR 0003](0003-kein-letta.md)) blieben als ernsthafte offene Kandidaten Mem0 und Supermemory.

## Entscheidung

**Mem0**, Apache-2.0, gebaut aus dem Unterverzeichnis `server` des Repos `mem0ai/mem0`, gepinnt auf Commit `760dca6f391277d79c3c7d2096c1bf1d037526c3`. REST/OpenAPI auf Host-Port 8888, Daten in eigenem Postgres mit pgvector.

Gegen **Supermemory** sprach nicht die Technik — die ist stark —, sondern die Governance-Unklarheit: Im Juli 2026 gab es eine öffentliche Nachfrage, wo der Server-Quellcode liegt. Für eine Komponente, die das Gedächtnis über Jahre hält, ist das vor einer Kernabhängigkeit zu klären, nicht danach.

## Konsequenzen

**Aus der Quelle gebaut, nicht als fertiges Image.** Das veröffentlichte `mem0/mem0-api-server` ist **arm64-only** und damit auf amd64 unbrauchbar. Der Build nutzt Docker-Git-Context mit Subdirectory-Syntax:

```
context: https://github.com/mem0ai/mem0.git#<sha>:server
```

Das vermeidet Vendoring oder Submodule. Es funktioniert, weil `server/Dockerfile` self-contained ist: Es zieht `mem0ai` von PyPI und braucht das Repo-Root nicht — anders als `server/dev.Dockerfile`, das den Repo-Root als Context erwartet und Mem0 editierbar installiert.

**Reproduzierbarkeit ist begrenzt.** Zwei Einschränkungen, die bewusst in Kauf genommen werden:

- Mem0s `server/requirements.txt` pinnt `mem0ai>=0.1.48` nur nach unten. Derselbe Commit kann zu unterschiedlichen Images führen.
- Das Upstream-Dockerfile startet uvicorn mit `--reload`, einem Entwicklungs-Default.

Beides ist für ein Skelett vertretbar und vor einem Produktivbetrieb zu beheben — am ehesten durch ein eigenes Dockerfile mit vollständig gepinnten Abhängigkeiten.

**Sicherheit.** `AUTH_DISABLED` bleibt `false`. Der erste Admin und API-Key werden über den laufenden Server erzeugt (`make bootstrap-hinweis`). Die Mem0-Dokumentation warnt ausdrücklich davor, sensible Daten im Klartext abzulegen — relevant für alles, was das Selbstmodell als `special_category` markiert.

**Telemetrie aus.** Mem0 sendet per Default ein anonymes Onboarding-Event. Für ein System, dessen Zweck die Hoheit über die eigenen Daten ist, ist `MEM0_TELEMETRY=false` der richtige Default.

**Zwei Datenbanken.** Die Default-Datenbank dient pgvector als Speicher für Memories; `mem0_app` hält Nutzer, Auth und API-Keys und wird beim ersten Start durch `docker/postgres/init-db.sh` angelegt.

**Offene Frage.** Ob sich das [Selbstmodell](../02-selbstmodell.md) — Ersetzungsketten, kaskadierende Löschung — sauber auf Mem0 abbilden lässt, ist ungeprüft. Fällt die Antwort negativ aus, braucht Säule 1 einen eigenen Speicher neben Mem0.
