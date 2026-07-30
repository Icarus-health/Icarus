# Icarus — Entwicklungskommandos
#
# `make help` listet alle Ziele.

COMPOSE ?= docker compose
SCHEMA  := schema/self-model.schema.json
EXAMPLE := schema/beispiel-profil.json

MEM0_URL       ?= http://localhost:8888
OPEN_WEBUI_URL ?= http://localhost:3000

.DEFAULT_GOAL := help
.PHONY: help check-env up down restart logs ps config smoke validate-schema clean bootstrap-hinweis

help: ## Diese Übersicht anzeigen
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

check-env: ## Prüfen, ob .env existiert und ein Postgres-Passwort gesetzt ist
	@test -f .env || { \
		echo "FEHLER: .env fehlt. Anlegen mit:  cp .env.example .env"; exit 1; }
	@grep -qE '^POSTGRES_PASSWORD=.+' .env || { \
		echo "FEHLER: POSTGRES_PASSWORD ist in .env nicht gesetzt."; \
		echo "        Wert erzeugen mit:  openssl rand -base64 24"; exit 1; }

up: check-env ## Stack starten (baut Mem0 beim ersten Mal, das dauert)
	$(COMPOSE) up -d --wait
	@echo
	@echo "Open WebUI : $(OPEN_WEBUI_URL)"
	@echo "Mem0 API   : $(MEM0_URL)/docs"

down: ## Stack stoppen, Daten bleiben erhalten
	$(COMPOSE) down

restart: down up ## Stack neu starten

logs: ## Logs aller Services verfolgen
	$(COMPOSE) logs -f

ps: ## Status der Services
	$(COMPOSE) ps

config: ## Compose-Datei validieren, ohne etwas zu starten
	$(COMPOSE) config --quiet && echo "docker-compose.yml ist valide."

smoke: ## Prüfen, ob der laufende Stack antwortet
	@echo "== Services =="
	@$(COMPOSE) ps
	@echo
	@echo "== Mem0 OpenAPI =="
	@curl -fsS -o /dev/null -w "  $(MEM0_URL)/docs -> %{http_code}\n" $(MEM0_URL)/docs
	@echo "== Open WebUI =="
	@curl -fsS -o /dev/null -w "  $(OPEN_WEBUI_URL) -> %{http_code}\n" $(OPEN_WEBUI_URL)
	@echo "== Anwendungsdatenbank =="
	@$(COMPOSE) exec -T postgres psql -U "$${POSTGRES_USER:-postgres}" -lqt \
		| cut -d'|' -f1 | grep -qw "$${APP_DB_NAME:-mem0_app}" \
		&& echo "  $${APP_DB_NAME:-mem0_app} existiert." \
		|| { echo "  FEHLER: Anwendungsdatenbank fehlt — lief init-db.sh?"; exit 1; }

validate-schema: ## Beispielprofil gegen das Selbstmodell-Schema validieren
	@python3 -c 'import jsonschema' 2>/dev/null || { \
		echo "jsonschema fehlt. Installieren mit:  pip install jsonschema"; exit 1; }
	@python3 -c "import json,jsonschema; \
s=json.load(open('$(SCHEMA)')); d=json.load(open('$(EXAMPLE)')); \
jsonschema.Draft202012Validator.check_schema(s); \
jsonschema.Draft202012Validator(s).validate(d); \
print('$(EXAMPLE) ist gegen $(SCHEMA) valide.')"

clean: ## Stack stoppen UND alle Daten löschen (Volumes inklusive)
	$(COMPOSE) down -v --remove-orphans

bootstrap-hinweis: ## Wie man den ersten Mem0-Admin und API-Key anlegt
	@echo "Mem0 ist standardmäßig authentifiziert (AUTH_DISABLED=false)."
	@echo "Der erste Admin und der erste API-Key werden über den Mem0-Server"
	@echo "angelegt. Zwei Wege:"
	@echo
	@echo "  1. Browser:  $(MEM0_URL)/docs  (OpenAPI-Oberfläche)"
	@echo "  2. Dashboard: make dashboard-up, dann http://localhost:3001"
	@echo
	@echo "Den erzeugten API-Key danach als ADMIN_API_KEY in .env eintragen."
	@echo "Er lässt sich nachträglich nicht wieder auslesen."

.PHONY: dashboard-up
dashboard-up: check-env ## Zusätzlich das Mem0-Dashboard auf Port 3001 starten
	$(COMPOSE) --profile dashboard up -d --wait
