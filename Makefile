# Icarus — Entwicklungskommandos
#
# `make help` listet alle Ziele. Wer Icarus nur benutzen will, braucht davon
# genau eines: `make start`.

PY      ?= python3
VENV    := .venv
BIN     := $(VENV)/bin
SCHEMA  := schema/self-model.schema.json
EXAMPLE := schema/beispiel-profil.json

# Erzeugte Datei mit Token und Passphrase. Liegt neben dem Repo, nie darin —
# siehe .gitignore.
ENVDATEI := .icarus.env
COMPOSE  := docker compose --env-file $(ENVDATEI)
ADRESSE  := http://127.0.0.1:8765
BETA_ENVDATEI := .icarus-beta.env
BETA_COMPOSE := docker compose -p icarus-beta --env-file $(BETA_ENVDATEI) -f compose.beta.yaml
BETA_ADRESSE := http://127.0.0.1:8877

.DEFAULT_GOAL := help
.PHONY: help start stop logs url beta-start beta-stop beta-logs beta-url \
        venv sidecar-dev sidecar-run mcp-config container \
        test validate-schema frontend-check check verify \
        app-dev app-build sidecar-binary icon clean

help: ## Diese Übersicht anzeigen
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# -- Loslegen ---------------------------------------------------------------
#
# `make start` ist der einzige Befehl, den jemand kennen muss, der Icarus
# benutzen und nicht bauen will. compose.yaml allein verlangt vier Schritte und
# drei Dinge, die man wissen muss — Token erzeugen, Passphrase erzeugen, einen
# Bind-Mount einkommentieren und ICARUS_FILE_ROOTS dazu passend setzen. Nichts
# davon kann ein Mensch besser als das Programm. Also macht es das Programm.
#
# Was es NICHT macht: die Sicherheitszusagen aufweichen. Das Token wird erzeugt,
# nicht auf einen Vorgabewert gesetzt; das `:?` in compose.yaml bleibt scharf.
# Der Notizordner wird nur eingebunden, wenn er ausdrücklich genannt wurde, und
# dann nur lesend.

# Token und Passphrase werden einmal erzeugt und danach wiederverwendet.
#
# Das Wiederverwenden ist keine Bequemlichkeit: Die Passphrase entschlüsselt
# schluessel.icarus im Datenvolume (siehe secrets.py). Ein zweiter Start mit
# einer neuen Passphrase macht jeden dort hinterlegten API-Schlüssel unlesbar —
# der Nutzer sähe eine leere Einrichtung und keinen Grund dafür.
$(ENVDATEI):
	@umask 077; { \
	  echo "# Von 'make start' erzeugt — nicht ins Git, nicht weitergeben."; \
	  echo "#"; \
	  echo "# Beide Werte müssen erhalten bleiben. Die Passphrase entschlüsselt"; \
	  echo "# die Schlüsseldatei im Datenvolume; ist sie weg, sind die dort"; \
	  echo "# hinterlegten API- und Mailpasswörter unlesbar."; \
	  echo "ICARUS_SIDECAR_TOKEN=$$(openssl rand -hex 32)"; \
	  echo "ICARUS_SECRETS_PASSPHRASE=$$(openssl rand -hex 32)"; \
	} > $@
	@chmod 600 $@
	@echo "Token und Passphrase erzeugt und in $@ abgelegt (nur für dich lesbar)."

# Die Beta erhält eigene Geheimnisse und ein eigenes Volume. Damit kann der
# Testkanal nie durch einen Container-Neustart an den Bestand von `make start`
# geraten. Das Bild selbst kommt ausschließlich aus dem CI-veröffentlichten
# GHCR-Tag `beta`.
$(BETA_ENVDATEI):
	@umask 077; { \
	  echo "# Von 'make beta-start' erzeugt — nicht ins Git, nicht weitergeben."; \
	  echo "ICARUS_SIDECAR_TOKEN=$$(openssl rand -hex 32)"; \
	  echo "ICARUS_SECRETS_PASSPHRASE=$$(openssl rand -hex 32)"; \
	} > $@
	@chmod 600 $@
	@echo "Eigene Zugangsdaten für den Betakanal in $@ erzeugt."

# NOTIZEN geht über die Umgebung in die Rezeptur, nicht über `$(NOTIZEN)` im
# Text. Eingesetzt würde der Pfad zu Shell-Quelltext, und ein Ordner wie
# „Sörens Notizen“ enthält ein Apostroph — das beendet die Zeichenkette und der
# Befehl bricht mit einem Syntaxfehler ab, den niemand sich erklären kann.
start: export NOTIZEN := $(NOTIZEN)
start: $(ENVDATEI) ## Icarus starten. Ordner freigeben: make start NOTIZEN=~/Notizen
	@set -e; \
	pfad="$$NOTIZEN"; \
	case "$$pfad" in "~"|"~/"*) pfad="$$HOME$${pfad#\~}";; esac; \
	if [ -n "$$pfad" ]; then \
	  aufgeloest=$$(cd "$$pfad" 2>/dev/null && pwd) || { \
	    echo "Den Ordner \"$$NOTIZEN\" gibt es nicht. Es wurde nichts gestartet."; \
	    exit 1; }; \
	  echo "Notizordner: $$aufgeloest — wird nur lesend eingebunden."; \
	  echo; \
	  yaml=$$(printf '%s' "$$aufgeloest" | sed "s/'/''/g"); \
	  printf "services:\n  sidecar:\n    environment:\n      - ICARUS_FILE_ROOTS=/notizen\n    volumes:\n      - '%s:/notizen:ro'\n" "$$yaml" \
	    | $(COMPOSE) -f compose.yaml -f - up -d --build; \
	else \
	  echo "Ohne Notizordner — Icarus läuft, kann aber keine Dateien lesen."; \
	  echo "Mit Ordner:  make start NOTIZEN=~/Documents/Obsidian"; \
	  echo; \
	  $(COMPOSE) up -d --build; \
	fi
	@printf 'Warte darauf, dass der Sidecar antwortet '
	@bereit=nein; \
	 for _ in $$(seq 1 90); do \
	   if curl -fsS $(ADRESSE)/health >/dev/null 2>&1; then bereit=ja; break; fi; \
	   printf '.'; sleep 1; \
	 done; \
	 echo; echo; \
	 if [ "$$bereit" = nein ]; then \
	   echo "Der Sidecar hat nach 90 Sekunden nicht geantwortet."; \
	   echo "Was er selbst dazu sagt:  make logs"; \
	   exit 1; \
	 fi
	@$(MAKE) --no-print-directory _fertig

# Abschlussmeldung. Eigenes Ziel, damit `make url` dieselbe Adresse aus
# derselben Quelle zieht — eine URL, die von Hand zusammengesetzt wird, ist die
# URL, die irgendwann nicht mehr stimmt.
.PHONY: _fertig
_fertig:
	@echo "Icarus läuft. Diese Adresse im Browser öffnen:"
	@echo
	@$(MAKE) --no-print-directory url
	@echo
	@echo "Beim ersten Öffnen führt ein Assistent durch die Einrichtung."
	@echo "Der Reihe nach: Modell, Ordner, Mail und Kalender, Zeitplan."
	@echo "Jeder Schritt ist überspringbar; Icarus funktioniert auch ohne alle."
	@echo
	@echo "  make url     die Adresse noch einmal ausgeben"
	@echo "  make logs    mitlesen, was der Sidecar tut"
	@echo "  make stop    anhalten; Gedächtnis und Schlüssel bleiben"
	@echo
	@echo "Ausführlich: docs/15-loslegen.md"

url: $(ENVDATEI) ## Adresse samt Token ausgeben (zum Öffnen im Browser)
	@token=$$(sed -n 's/^ICARUS_SIDECAR_TOKEN=//p' $(ENVDATEI)); \
	 echo "  $(ADRESSE)/?token=$$token"

logs: $(ENVDATEI) ## Mitlesen, was der Sidecar tut (Strg-C beendet nur das Mitlesen)
	@$(COMPOSE) logs -f

stop: $(ENVDATEI) ## Icarus anhalten
	@$(COMPOSE) down
	@echo
	@echo "Angehalten. Das Gedächtnis liegt weiter im Docker-Volume, die"
	@echo "Schlüssel in $(ENVDATEI). 'make start' knüpft dort wieder an."

beta-start: $(BETA_ENVDATEI) ## Privaten Betakanal getrennt auf Port 8877 starten/aktualisieren
	@$(BETA_COMPOSE) pull
	@$(BETA_COMPOSE) up -d
	@printf 'Warte darauf, dass der Betakanal antwortet '
	@bereit=nein; \
	 for _ in $$(seq 1 90); do \
	   if curl -fsS $(BETA_ADRESSE)/health >/dev/null 2>&1; then bereit=ja; break; fi; \
	   printf '.'; sleep 1; \
	 done; \
	 echo; \
	 if [ "$$bereit" = nein ]; then \
	   echo "Der Betakanal hat nach 90 Sekunden nicht geantwortet. Was er selbst sagt: make beta-logs"; \
	   exit 1; \
	 fi
	@echo "Betakanal läuft. Diese Adresse im Browser öffnen:"
	@$(MAKE) --no-print-directory beta-url

beta-url: $(BETA_ENVDATEI) ## Adresse samt Token des getrennten Betakanals ausgeben
	@token=$$(sed -n 's/^ICARUS_SIDECAR_TOKEN=//p' $(BETA_ENVDATEI)); \
	 echo "  $(BETA_ADRESSE)/?token=$$token"

beta-logs: $(BETA_ENVDATEI) ## Meldungen des Betakanals mitlesen
	@$(BETA_COMPOSE) logs -f

beta-stop: $(BETA_ENVDATEI) ## Betakanal anhalten, Daten bleiben erhalten
	@$(BETA_COMPOSE) down
	@echo "Betakanal angehalten. Sein eigener Datenbestand bleibt erhalten."

# -- Sidecar ----------------------------------------------------------------

venv: ## Virtuelle Umgebung für den Sidecar anlegen
	@test -d $(VENV) || $(PY) -m venv $(VENV)
	@$(BIN)/pip install --quiet --upgrade pip

sidecar-dev: venv ## Sidecar samt Entwicklungsabhängigkeiten installieren (ohne cognee)
	$(BIN)/pip install -e "sidecar[dev]"
	@echo
	@echo "Installiert ohne cognee — die semantische Suche fällt auf Substringsuche"
	@echo "zurück. Für den vollen Umfang:  make sidecar-full"

sidecar-full: venv ## Sidecar mit cognee installieren (zieht ~950 MB nach)
	$(BIN)/pip install -e "sidecar[cognee,dev]"

sidecar-run: ## Sidecar lokal starten (127.0.0.1:8765)
	ICARUS_DATA_DIR=$${ICARUS_DATA_DIR:-./.icarus-data} $(BIN)/icarus-sidecar

container: ## Container-Bild lokal bauen
	docker build -t icarus:local .

# Zum Starten gibt es `make start`. Das frühere `container-run` erzeugte bei
# jedem Aufruf neue Schlüssel — damit war die verschlüsselte Schlüsseldatei nach
# dem ersten Neustart unlesbar, ohne dass irgendwo stand, warum.

mcp-config: ## Konfigurationsschnipsel für die MCP-Tür ausgeben
	@echo 'In die Konfiguration des Assistenten (Claude Desktop, Claude Code, …).'
	@echo
	@echo 'Aus dieser Arbeitskopie:'
	@echo '{ "mcpServers": { "icarus": {'
	@echo '  "command": "$(abspath $(BIN))/icarus-mcp"'
	@echo '} } }'
	@echo
	@echo 'Aus der gebündelten App — eine Binary, zwei Rollen:'
	@echo '{ "mcpServers": { "icarus": {'
	@echo '  "command": "/Applications/Icarus.app/Contents/Resources/icarus-sidecar",'
	@echo '  "args": ["--mcp"]'
	@echo '} } }'
	@echo
	@echo 'Der Sidecar muss laufen — er hinterlegt Port und Token in'
	@echo 'verbindung.json. Siehe docs/07-mcp-tuer.md.'

test: ## Tests des Selbstmodells
	$(BIN)/python -m pytest sidecar/tests -q

validate-schema: ## Beispielprofil gegen das Selbstmodell-Schema validieren
	@$(BIN)/python -c "import json,jsonschema; \
s=json.load(open('$(SCHEMA)')); d=json.load(open('$(EXAMPLE)')); \
jsonschema.Draft202012Validator.check_schema(s); \
jsonschema.Draft202012Validator(s).validate(d); \
print('$(EXAMPLE) ist gegen $(SCHEMA) valide.')"

check: test validate-schema ## Alle Prüfungen des Sidecars
	@cd app/src-tauri && cargo check

frontend-check: ## Syntax aller produktiven JavaScript- und Browser-Testdateien
	@set -e; \
	for datei in $$(find app/src app/e2e -type f \
		\( -name '*.js' -o -name '*.mjs' \) | sort); do \
		node --check "$$datei"; \
	done

verify: check frontend-check ## Lokaler Standardnachweis vor jedem PR

# -- Desktop-App ------------------------------------------------------------

icon: ## App-Icon neu erzeugen
	$(PY) app/src-tauri/icons/generate_icon.py

app-dev: ## App im Entwicklungsmodus starten (braucht sidecar-dev auf dem PATH)
	cd app && npm run tauri dev

sidecar-binary: ## Sidecar zu einer eigenständigen Binary bündeln
	$(BIN)/pip install --quiet pyinstaller
	$(BIN)/pyinstaller packaging/icarus-sidecar.spec \
		--distpath app/src-tauri/binaries \
		--workpath build/pyinstaller --noconfirm
	@echo "Binary liegt in app/src-tauri/binaries/icarus-sidecar"
	@echo "Semantische Suche mitliefern: ICARUS_BUNDLE_COGNEE=1 make sidecar-binary"

app-build: sidecar-binary ## App bündeln (auf macOS: .dmg und .app)
	cd app && npm run tauri build

# -- Sicherheit und Sicherung ----------------------------------------------

secrets-migrate: ## Schlüssel aus .env in den Schlüsselbund übernehmen
	@$(BIN)/python -c "\
from pathlib import Path; \
from icarus_memory.secrets import Keychain, migrate_env_file; \
kc = Keychain(); \
print('Schlüsselspeicher:', kc.backend); \
migrated = migrate_env_file(Path('.env'), kc); \
print('Übernommen:', ', '.join(migrated) or 'nichts'); \
print('Die .env kann nun bereinigt werden.') if migrated else None"

backup: ## Snapshot des Selbstmodells anlegen
	@$(BIN)/python -c "\
import os; from pathlib import Path; \
from icarus_memory.backup import snapshot; \
d = Path(os.environ.get('ICARUS_DATA_DIR', './.icarus-data')); \
print('Snapshot:', snapshot(d / 'self-model.sqlite3', d / 'sicherungen'))"

clean: ## Build-Artefakte entfernen
	rm -rf build $(VENV) app/src-tauri/target app/src-tauri/binaries/icarus-sidecar
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

# -- Modellqualifikation ----------------------------------------------------

QUALIFICATION_SUBMISSIONS ?=
QUALIFICATION_COST_EUR ?= 0
QUALIFICATION_REPORT ?= /tmp/icarus-qualifikation.json

.PHONY: qualify-execution-model
qualify-execution-model: ## Suite prüfen und optional Einreichungen bewerten
	$(PY) -m unittest discover -s tools/qualification -p 'test_*.py' -q
	$(PY) tools/qualification/qualify.py validate
	@if [ -n "$(QUALIFICATION_SUBMISSIONS)" ]; then \
	  $(PY) tools/qualification/qualify.py grade \
	    --submissions "$(QUALIFICATION_SUBMISSIONS)" \
	    --cost-eur "$(QUALIFICATION_COST_EUR)" \
	    --report "$(QUALIFICATION_REPORT)"; \
	else \
	  echo "Suite geprüft. Für einen Lauf QUALIFICATION_SUBMISSIONS setzen."; \
	fi
