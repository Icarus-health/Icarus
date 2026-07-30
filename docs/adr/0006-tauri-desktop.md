# ADR 0006: Eigene Tauri-App statt Docker-Stack

**Status:** akzeptiert · **Datum:** 2026-07-30 · **Löst ab:** [ADR 0001](0001-ui-open-webui.md)

## Kontext

[ADR 0001](0001-ui-open-webui.md) wählte Open WebUI als Oberfläche, betrieben über Docker Compose. Das war für einen selbst gehosteten Serverbetrieb richtig.

Das Ziel ist aber eine **downloadbare App**, zuerst für macOS, später Windows. Damit wird der Docker-Stack zur falschen Betriebsform: Open WebUI ist eine Web-Anwendung, die einen Container-Runtime voraussetzt. Ein Nutzer, der Docker Desktop installieren muss, bevor er eine Notiz speichern kann, ist nicht die Zielgruppe dieses Projekts.

Geprüfte Alternativen:

**AnythingLLM Desktop forken.** MIT, fertige Electron-App für Mac und Windows, Ollama eingebaut, lokale Embeddings, offline lauffähig. Der schnellste Weg zu etwas Benutzbarem.

**Eigene Tauri-App.** Rust-Hülle mit System-WebView, Frontend in HTML/JS, Python-Sidecar für die Gedächtnisschicht.

## Entscheidung

**Eigene Tauri-App (v2) mit gebündeltem Python-Sidecar.**

Der Grund ist keine Technikvorliebe, sondern eine Eigentumsfrage. Die beiden Dinge, die Icarus von einem Chat-Frontend unterscheiden — das **überprüfbare Selbstmodell** (Säule 1) und der **Freigabe-Layer** (Säule 4) — sind keine Plugins. Sie greifen in jede Interaktion ein: Herkunft muss bei jeder Aussage sichtbar sein, jede Aktion muss durch die Klassifikation. In einer fremden Electron-App leben sie als Fremdkörper, und jedes Upstream-Update wird zur Konfliktarbeit.

Dazu kommt: Tauri nutzt die System-WebView statt ein eigenes Chromium mitzuliefern. Die Hülle bleibt bei ~10 MB statt ~150 MB — was angesichts der 944 MB des Sidecars ([ADR 0005](0005-cognee-statt-mem0.md)) nicht der Hauptposten ist, aber in die richtige Richtung zeigt.

## Aufbau

Die App startet den Sidecar als Kindprozess und redet über HTTP auf `127.0.0.1` mit ihm:

- Der Port wird beim Start vom Betriebssystem vergeben, nicht fest verdrahtet.
- Ein **Token wird bei jedem Start neu erzeugt** und per Umgebungsvariable übergeben. Ohne das könnte jeder lokale Prozess das Selbstmodell auslesen — auf einem Einzelplatzrechner ist genau das der relevante Angriffsweg.
- Der Sidecar bindet ausschließlich an Loopback. Es gibt bewusst keine Option, ihn zu öffnen.
- Beim Beenden wird der Kindprozess terminiert, sonst hält er die Datenbank offen.
- Nutzerdaten liegen im plattformüblichen App-Verzeichnis, damit das Selbstmodell ein App-Update überlebt.

## Konsequenzen

**Python muss ins Bundle.** Der Sidecar wird mit PyInstaller (oder gleichwertig) zu einer Binary gebaut und über `bundle.resources` eingebettet. Das ist der aufwendigste Teil der Auslieferung und der wahrscheinlichste Ort für plattformspezifische Überraschungen.

**Signierung und Notarisierung.** Eine macOS-App, die ein Nutzer herunterlädt, muss signiert und notarisiert sein, sonst blockt Gatekeeper. Das setzt ein Apple Developer Programm voraus (kostenpflichtig) und muss in die CI. Für Windows gilt Analoges mit einem Code-Signing-Zertifikat.

**Zwei Sprachen mehr.** Rust für die Hülle, JavaScript für das Frontend, zusätzlich zum Python des Sidecars. Für ein kleines Team ist das eine reale Last.

**Die Oberfläche ist Eigenarbeit.** Was Open WebUI und AnythingLLM fertig mitbringen — Modellauswahl, Sprache, Dateien, Werkzeuge — muss hier gebaut werden. Das ist der Preis der Entscheidung und zugleich ihr Zweck: der Report benennt UX-Vereinfachung als größten Zeitfresser und als eigentliche Produktdifferenzierung. Diesen Teil auszulagern hieße, den Kern auszulagern.

**Cross-Plattform kostet später.** Das Gerüst ist plattformneutral geschrieben, aber ein macOS-Bundle lässt sich nicht auf Linux bauen. Windows braucht einen eigenen Build-Runner.

**AnythingLLM bleibt die Ausweichoption**, falls sich die Eigenentwicklung als zu langsam erweist. Der Wechsel wird teurer, je mehr Oberfläche entsteht — die Entscheidung ist daher früh zu überprüfen, nicht spät.
