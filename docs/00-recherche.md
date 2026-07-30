# Open-Source-Landschaft für einen langfristigen persönlichen digitalen Zwilling

> **Stand der Erhebung: Juli 2026.** Alle Stern- und Reifeangaben sind Momentaufnahmen und altern schnell.
>
> Dieses Dokument hält die Ausgangsrecherche fest. Die daraus abgeleiteten Entscheidungen stehen in den [ADRs](adr/); die tatsächlich gebaute Architektur in [01-architektur.md](01-architektur.md). Wo Recherche und Entscheidung auseinandergehen, gilt die ADR.
>
> **Zwei Empfehlungen dieses Reports wurden inzwischen revidiert.** Letta entfiel nach Prüfung der Primärquellen ([ADR 0003](adr/0003-kein-letta.md)). Mem0 und Open WebUI entfielen mit der Festlegung auf eine downloadbare Desktop-App: beide setzen Docker voraus ([ADR 0005](adr/0005-cognee-statt-mem0.md), [ADR 0006](adr/0006-tauri-desktop.md)). Gebaut wird auf **cognee** in einer **Tauri-App**.
>
> Der Report bleibt unverändert erhalten. Eine Recherche, die man nachträglich an das Ergebnis anpasst, ist als Beleg wertlos — dasselbe Prinzip, das das Selbstmodell auf seine eigenen Daten anwendet.

## Kurzfassung

Es gibt mehrere Open-Source-Projekte, die wichtige Teile der Vision abbilden — aber **kein Projekt liefert den vollständigen „20-Jahre-digitaler-Zwilling"-Stack aus einer Hand.**

Am nächsten an der Zielrichtung liegen **Open WebUI** als modellunabhängige Bedienoberfläche, **Mem0** als eigenständige Memory-Schicht, **Khoj** als persönliches „Second Brain" und **Agent Zero** als Computer-Use- und Tool-Execution-Schicht.

Für das konkrete Ziel — ein einfaches, nicht-technisches Frontend, das verschiedene Modelle und Werkzeuge unter einer stabilen persönlichen Gedächtnisstruktur bündelt — ist die sinnvollste Strategie **nicht**, von null zu bauen, sondern ein **komponierter Baukasten**.

Der wichtigste Befund: **Langfristige Memory-Qualität ist kein UI-Problem, sondern ein Architekturproblem** aus Gedächtnis-Tiers, Retrieval, Zeitlogik, Wissensaktualisierung, Provenienz und Nutzerkontrolle. Die Forschung zeigt, dass Assistenten bei langfristigem, mehrsitzigem Erinnern schnell einbrechen, wenn keine explizite Memory-Architektur vorhanden ist.

**Fazit:** Der Aufwand lohnt sich fachlich — aber nicht als „neues LLM-Produkt", sondern als **persönliches AI Operating Layer**.

---

## ⚠️ Korrektur: Letta entfällt

Die ursprüngliche Recherche empfahl **Letta** als „beste Basis für stateful agent core". **Diese Empfehlung ist überholt** und wurde bei der Umsetzung gegen die Primärquellen geprüft:

- Die `AGENTS.md` des Repos `letta-ai/letta` beginnt mit „**This repository is deprecated**" und beschreibt es als „in maintenance mode and … no longer where active development happens". Das Repo enthält den **Legacy-Server** hinter der V1-API.
- Aktive Entwicklung ist nach `letta-ai/letta-code` gewandert — eine TypeScript-CLI, kein selbst hostbares Memory-Backend. Die im Report zitierten Aktivitätssignale beziehen sich mit hoher Wahrscheinlichkeit auf dieses andere Repo.
- Der genannte Nachfolger für Self-Hosting, der **App Server**, ist an Lettas Cloud gekoppelt: `letta server` baut laut Dokumentation eine ausgehende WebSocket-Verbindung zu **Constellation** auf, und die Authentifizierung läuft über OAuth beziehungsweise `LETTA_API_KEY` je nach Plan-Tarif.

Das widerspricht der Prämisse „lokal, portabel, anbieterunabhängig" direkt. **Letta ist deshalb nicht Teil der Architektur.** Ausführlich in [ADR 0003](adr/0003-kein-letta.md).

Die Tabelle unten ist als Momentaufnahme der Recherche erhalten geblieben — die Letta-Zeile ist entsprechend zu lesen.

---

## Vergleich der relevantesten Projekte

Kein Beliebtheitsranking, sondern eine Bewertung für den Zweck „digitaler Zwilling / persönliches AI-OS".

| Projekt | Lizenz | Architektur | Modellstrategie | Privacy und Portabilität | UX-Eignung | Urteil |
|---|---|---|---|---|---|---|
| **Open WebUI** | gemischt, Kern inkl. Open WebUI License | Per-user Memory, Knowledge Bases, Tools, MCP, Context Compaction, Notes/Channels | Lokal via Ollama oder jede OpenAI-kompatible API | Export als JSON/PDF/Markdown, Offline-Betrieb möglich | Beste UX-Shell im Feld | **Gewählt als Oberfläche** |
| **Mem0** | Apache-2.0 | Faktenextraktion, hybrides Retrieval, Graph Memory, Zeitstempel, Expiration | OpenAI, Anthropic, Groq, Ollama; self-hosted oder Cloud | Export/Import, CLI; Doku warnt vor sensiblen Daten im Klartext | Kaum Endnutzer-UI | **Gewählt als Memory-Schicht** |
| ~~Letta~~ | Apache-2.0 | Memory Blocks, Archival Memory, Compaction, AgentFile | Modellagnostisch | `.af` als Exportformat | Entwicklerorientiert | **Verworfen** — siehe Korrektur oben |
| **Khoj** | AGPL-3.0 | „Second Brain": Dokumenten-/Websuche, Embeddings + Reranking | Lokal oder via Ollama/OpenAI-kompatibel | Self-hosting, offline möglich | Wissenssystem, kein AI-OS | Muster für Säule 3 |
| **AnythingLLM** | MIT | LanceDB-Defaults, Workspaces, Agent Flows, Scheduled Jobs | Cloud oder lokal, Desktop mit Ollama | Chat-Export CSV/JSON/JSONL | Sehr stark, Desktop-first | **Ausweichoption zur Oberfläche** |
| **LibreChat** | MIT | User Memory über Vektoreinträge, separate RAG-API | Custom Endpoints inkl. Ollama | Import von ChatGPT-Verläufen | Gute Multi-LLM-Zentrale | Chat-Hub, kein Twin-Core |
| **Supermemory** | MIT | Graph-/Learning-Engine, User Profiles, automatische Vergessenslogik | Lokaler Default-Embedder, optional Cloud-Modelle | Lokaler Modus, Migration von Mem0 dokumentiert | Stark | Architektonisch stark, **Governance vorab prüfen** |
| **Open Interpreter** | Apache-2.0 | Keine tiefe Memory-Architektur; Harness und Portabilität | Provider-agnostisch, ACP | Portabilität über gemeinsame Standards | Werkzeug, kein Produkt | Starker Ausführungsbaustein |
| **Agent Zero** | MIT | Projekte mit getrennten Memories, echter Linux-Desktop, Browser-DOM | Lokale oder entfernte LLMs | Dockerisiert, editierbare Internals | Arbeitsmotor | **Beste Computer-Use-Engine**, aber erst nach Säule 4 |
| **OpenHands** | MIT | Agent Server, Automation Server, Canvas | „Bring your own model" via LiteLLM | Gute Orchestrierung | Dev-/Ops-orientiert | Backend-Orchestrator, kein Endprodukt |

### Kommerzielle Annäherungen

**Personal AI** positioniert sich als AI-Memory-Plattform mit „Memory Core" und identitätsgebundener Erinnerung. **Limitless** verfolgte das „always-on personal memory"-Paradigma über eine Capture-Hardware-Schiene und wurde Ende 2025 von Meta übernommen — ein Beleg für Nachfrage und Plattformrisiko zugleich. **Supermemory** sitzt dazwischen: Produkt, App und MCP-basierte Universal-Memory-Schicht.

## Was die Forschung sagt

**MemGPT** — aus dem Letta hervorging — betrachtet LLMs als eine Art Betriebssystem mit **kontextnahen und persistenten Memory-Tiers**, um die Beschränkung des Kontextfensters zu umgehen. Das deckt sich mit der Intuition, dass ein solches System nicht nur RAG, sondern mehrschichtiges Gedächtnis mit aktiver Speicherverwaltung braucht.

**Generative Agents** baut auf **Beobachtung, Planung und Reflexion**: Erfahrungen werden gesammelt, zu höherwertigen „Reflections" verdichtet und situationsabhängig abgerufen. Für einen digitalen Zwilling heißt das: nicht bloß Erinnerungen, sondern **Verdichtungsschichten** — Präferenzen, Routinen, Selbstmodell, langfristige Pläne.

**LoCoMo** und **LongMemEval** zeigen, wie viel schwieriger langfristiges persönliches Erinnern ist als normales Chatten. LongMemEval zerlegt das Problem in Informationsextraktion, Multi-Session-Reasoning, temporales Reasoning, Wissensupdates und Abstention. Ein brauchbarer Zwilling muss **Zeit, Wissensänderungen, Widersprüche und mehrere Sitzungen** nativ beherrschen — sonst entsteht nur die Illusion von Persönlichkeit.

**LongMemEval-V2** liegt der „Kollege seit 20 Jahren"-Vorstellung am nächsten: Es fragt nicht nur nach Faktenerinnerung, sondern ob Agenten über Zeit zu kompetenten Kollegen in angepassten Umgebungen werden — samt Workflow-Wissen, Umweltbesonderheiten und „gotchas".

**Memori** argumentiert, dass eine LLM-agnostische Memory-Schicht als **Datenstrukturierungsproblem** zu verstehen ist, nicht als „mehr Kontext reinschieben": semantische Tripel, kompakte Zusammenfassungen, strukturierte Retrieval-Pfade.

Zusammen stützen diese Arbeiten die Kombination aus **Faktenstore + Graph + Verdichtungsebene + Provenienz** — und damit die Entscheidung, das [Selbstmodell-Schema](02-selbstmodell.md) explizit zu bauen statt es einem Retrieval-System zu überlassen.

## Lücken zur Vision

**Identität über sehr lange Zeiträume.** Heutige Systeme speichern Erinnerungen und Präferenzen, aber kaum eines hat ein versioniertes Selbstmodell aus „Wer bin ich?", „Was ist veraltet?", „Woraus wurde das abgeleitet?".

**Provenienz und Datenhygiene.** In einem 20-Jahres-System muss beantwortbar sein: Woher stammt diese Erinnerung, wann wurde sie bestätigt, wodurch überschrieben, ist sie abgelaufen? Mem0 bietet Zeitstempel und Expiration, Open WebUI exportiert Chats — ein domänenübergreifendes Provenienzmodell ist in **keinem** untersuchten System vollständig umgesetzt.

**Echte Modellunabhängigkeit bei gleichbleibender Persönlichkeit.** Viele Oberflächen können mehrere Modelle ansprechen. Das ist nicht dasselbe wie ein einheitlicher Gedächtniskern, der zwischen Cloud-Modellen, lokalen Modellen und späteren Backends migriert.

**Nicht-Techniker-UX.** Technisch ist viel vorhanden, die Installations- und Bedienrealität bleibt zu steil. AnythingLLM und Open WebUI haben den größten Vorsprung. Die Komplexität verschwindet nicht, sie wird nur besser verpackt — und genau das ist der Kern der Produktdifferenzierung.

**Sicherheit in einem handelnden System.** Sobald der Assistent Mails sendet, Termine ändert oder auf das Dateisystem zugreift, sind Freigaben, Rollensteuerung und Audit nicht optional. Die Projekte haben Bausteine, aber keine alltagstaugliche Endlösung. Siehe [03-delegation.md](03-delegation.md).

## Schlussurteil

Ja — aber nur, wenn das Problem als **UX- und Memory-Orchestrierungsproblem** behandelt wird, nicht als Modellproblem. Dort ist die Lücke echt. Die großen Anbieter bauen Modelle und Tooling; der Raum für ein einfaches, langlebiges, persönliches AI-Betriebssystem ist noch offen.

Die Gegenhypothese muss man mitdenken: Wer hier gewinnt, gewinnt nicht mit dem größten Modell, sondern mit der **vertrauenswürdigsten, portabelsten und einfachsten Oberfläche über viele Jahre**. Der schnellste Weg dorthin ist Komposition statt Neuentwicklung ab null.

---

## Quellen

Die ursprüngliche Fassung dieses Dokuments enthielt maschinenlesbare Zitatmarken ohne aufgelöste Ziele. Sie wurden entfernt und durch die folgende kuratierte Liste ersetzt.

### Projekte

| Projekt | Quelle |
|---|---|
| Open WebUI | https://github.com/open-webui/open-webui · https://docs.openwebui.com |
| Mem0 | https://github.com/mem0ai/mem0 · https://docs.mem0.ai |
| cognee (gewählt) | https://github.com/topoteretes/cognee · https://docs.cognee.ai |
| Tauri | https://tauri.app |
| Letta (Legacy-Server) | https://github.com/letta-ai/letta · https://docs.letta.com |
| Letta Code (aktive Entwicklung) | https://github.com/letta-ai/letta-code |
| Khoj | https://github.com/khoj-ai/khoj |
| AnythingLLM | https://github.com/mintplex-labs/anything-llm |
| LibreChat | https://github.com/danny-avila/LibreChat |
| Supermemory | https://github.com/supermemoryai/supermemory |
| Open Interpreter | https://github.com/openinterpreter/open-interpreter |
| Agent Zero | https://github.com/agent0ai/agent-zero |
| OpenHands | https://github.com/OpenHands/OpenHands |
| mcpo (MCP-zu-OpenAPI-Proxy) | https://github.com/open-webui/mcpo |

### Forschung

| Arbeit | Quelle |
|---|---|
| MemGPT: Towards LLMs as Operating Systems | https://arxiv.org/abs/2310.08560 |
| Generative Agents: Interactive Simulacra of Human Behavior | https://arxiv.org/abs/2304.03442 |
| LoCoMo — Evaluating Very Long-Term Conversational Memory | https://arxiv.org/abs/2402.17753 |
| LongMemEval — Benchmarking Long-Term Interactive Memory | https://arxiv.org/abs/2410.10813 |

LongMemEval-V2 und Memori sind im Fließtext genannt; für beide sollte vor weiterer Verwendung die aktuelle Primärquelle nachgetragen werden. Die Aussagen zur Deprecation von Letta stammen aus `AGENTS.md` und `README.md` des Repos `letta-ai/letta` sowie aus der Self-Hosting-Dokumentation unter docs.letta.com, jeweils abgerufen am 30. Juli 2026.
