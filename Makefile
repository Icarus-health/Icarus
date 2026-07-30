# Icarus — Entwicklungskommandos
#
# `make help` listet alle Ziele.

PY      ?= python3
VENV    := .venv
BIN     := $(VENV)/bin
SCHEMA  := schema/self-model.schema.json
EXAMPLE := schema/beispiel-profil.json

.DEFAULT_GOAL := help
.PHONY: help venv sidecar-dev sidecar-run test validate-schema check \
        app-dev app-build sidecar-binary icon clean

help: ## Diese Übersicht anzeigen
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

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
