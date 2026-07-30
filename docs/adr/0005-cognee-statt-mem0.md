# ADR 0005: cognee statt Mem0 als Gedächtnisschicht

**Status:** akzeptiert · **Datum:** 2026-07-30 · **Löst ab:** [ADR 0002](0002-memory-mem0.md)

## Kontext

[ADR 0002](0002-memory-mem0.md) wählte Mem0. Diese Entscheidung fiel unter der Annahme eines selbst gehosteten Server-Stacks. Mit der Festlegung auf eine **downloadbare Desktop-App** ([ADR 0006](0006-tauri-desktop.md)) ändert sich die entscheidende Anforderung: Die Gedächtnisschicht muss **ohne Server, ohne Container und ohne Einrichtung** in einer App laufen, die ein Nicht-Techniker herunterlädt und startet.

Daran scheitert Mem0. Sein Self-Hosted-Server setzt Postgres mit pgvector voraus, also Docker. Docker Desktop als Voraussetzung für eine Consumer-App ist das Gegenteil des Projektziels.

## Entscheidung

**cognee** (Apache-2.0, Version 1.4.0) als Gedächtnisschicht, eingebettet als Python-Sidecar.

Ausschlaggebend war:

**Dateibasiert per Default.** cognees Standardablagen sind SQLite, LanceDB und KuzuDB — keine Server, keine Container, keine Einrichtung. Das `.env.template` formuliert es ausdrücklich: „Set this one variable and you're done. Default databases (SQLite, LanceDB, KuzuDB) are file-based, no setup needed."

**Graph-nativ passt zu Säule 1.** Das Selbstmodell besteht aus Kanten: `supersedes`, `derived_from`, Widerrufskaskaden. Auf einem flachen Faktenspeicher muss man diese Struktur nachbauen; auf einem Knowledge Graph ist sie die native Form.

**`forget` ist eine erstklassige Operation** neben `remember`, `recall` und `improve` — nicht ein nachgerüsteter Löschbefehl. Säule 1 verlangt genau das.

**Wächst mit.** Per Umgebungsvariable auf Postgres oder Neo4j umschaltbar, ohne Codeänderung. Derselbe Baustein trägt App und späteren Serverbetrieb.

**Anbieterunabhängig.** LLM-Zugriff über litellm, also auch Ollama und lokale Modelle.

## Konsequenzen

**Gemessene Größe: 944 MB.** Ein venv mit `cognee` und Abhängigkeiten. Das ist mehr als erwartet und der wesentliche Preis dieser Entscheidung. Entlastend: **kein torch, kein transformers, keine CUDA-Pakete** — der Großteil sind pyarrow, pylance/lancedb und numpy. Vor dem ersten Release ist zu prüfen, wie weit sich das durch Pruning und selektive Extras drücken lässt. Fällt das Ergebnis schlecht aus, ist die semantische Suche das erste, was optional wird — der Bestand funktioniert ohne sie.

**Installation dauert.** Gemessen 77 Sekunden. Für den Nutzer irrelevant, weil vorgebündelt, aber für CI-Zeiten relevant.

**cognee bekommt den Bestand nicht.** Das ist die wichtigste Einschränkung dieser Entscheidung, und sie ist bewusst so gebaut: Der **verbindliche Bestand liegt in SQLite**, adressierbar per ID, deterministisch lesbar, ohne Modellaufruf. cognee ist ausschließlich der **semantische Index**. Ein per LLM befüllter Graph ist verlustbehaftet und nicht deterministisch — als alleinige Quelle der Wahrheit für ein überprüfbares Selbstmodell ist er ungeeignet. Treffer aus cognee werden immer gegen den Bestand aufgelöst; der Graph kann keine Aussage erfinden, die im Bestand nicht existiert.

Damit überlebt das Selbstmodell auch einen Wechsel der Memory-Bibliothek. Fällt cognee weg, bleibt der Bestand vollständig und nur die Suche fällt auf Substringsuche zurück. Das ist Säule 2, praktisch umgesetzt statt behauptet.

**Reifegrad.** cognee deklariert sich als „Development Status :: 4 – Beta". Das ist vertretbar, weil die Abhängigkeit auf die Suchfunktion begrenzt ist.

**Kein veröffentlichter Benchmark.** cognee hat keinen LongMemEval-Wert publiziert; Mem0 liegt bei rund 49 %. Die Vergleiche, die cognee vorne sehen, stammen überwiegend aus cognees eigenem Blog und wurden hier **nicht** als neutrale Quelle gewertet. Die Entscheidung stützt sich auf die Betriebsform, nicht auf beanspruchte Abrufqualität.

**Python im Bundle.** cognees Kern ist Python; die TypeScript- und Rust-Clients sprechen gegen einen Server und sind kein eingebetteter Ersatz. Die App braucht daher ein gebündeltes Python — siehe [ADR 0006](0006-tauri-desktop.md).
