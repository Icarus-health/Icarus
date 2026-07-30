# ADR 0001: Open WebUI als Bedienoberfläche

**Status:** akzeptiert · **Datum:** 2026-07-30

## Kontext

Icarus braucht eine Oberfläche, die für Nicht-Techniker bedienbar ist, mehrere Modelle austauschbar anspricht und Sprache, Dateien und Werkzeuge mitbringt. Eine eigene Oberfläche zu bauen wäre der teuerste denkbare Einstieg — der Report schätzt allein die UX-Vereinfachung als größten Zeitfresser des Projekts ein.

Ernsthaft geprüft wurden Open WebUI, AnythingLLM und LibreChat.

## Entscheidung

**Open WebUI, gepinnt auf `v0.11.0`** (`ghcr.io/open-webui/open-webui`), Host-Port 3000.

Gründe:

- Reifste Oberfläche im untersuchten Feld: Sprache, Knowledge Bases, Werkzeuge, MCP, Export, Offline-Betrieb.
- Modellunabhängig per Konstruktion — lokal über Ollama oder jede OpenAI-kompatible API. Das erfüllt Säule 2 auf der Modellseite bereits im Skelett.
- Nimmt genau die Arbeit ab, die am teuersten und am wenigsten differenzierend ist.

## Konsequenzen

**Lizenz.** Open WebUI steht nicht mehr rein unter einer klassischen OSI-Lizenz; der Kern enthält Bestandteile unter der Open WebUI License. Das ist eine bewusst eingegangene Einschränkung. Sie ist vertretbar, weil die Oberfläche die am leichtesten austauschbare Komponente ist — Gedächtnis und Selbstmodell liegen außerhalb.

**Ausweichoption.** Wird die Lizenzlage zum Problem, ist AnythingLLM (MIT) die Alternative. Der Wechsel wird umso teurer, je mehr eigene Logik in die Oberfläche wandert — ein Grund, Orchestrierung und Policy bewusst *neben* die Oberfläche zu legen und nicht hinein.

**Doppeltes Gedächtnis.** Open WebUI bringt ein eigenes Memory-Feature mit, das nichts von Mem0 weiß. Ohne Gegenmaßnahme entstehen zwei konkurrierende Gedächtnisse. Die Verdrahtung ist manuelle Konfiguration (siehe [01-architektur.md](../01-architektur.md)); das Abschalten des eingebauten Memory ist eine offene Aufgabe.

**Pinning.** Kein `:main` und kein `:latest`. Der Tag wird bewusst und nachvollziehbar angehoben.
