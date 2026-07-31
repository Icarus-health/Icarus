# Container-Verpackung des Sidecars — ein zusätzlicher Auslieferungsweg neben
# der Tauri-App, keine Ablösung. Siehe docs/adr/0007-docker-als-zweiter-weg.md
# für die Begründung und die Grenzen (kein Schlüsselbund, kein Computer-Use).
#
# Das Bild enthält absichtlich nur den Sidecar (icarus_memory), nicht die
# Tauri-App — die braucht eine Betriebssystem-Oberfläche, die ein Container
# nicht hat.

# -- Build-Stufe --------------------------------------------------------------
# Eigene Stufe, damit Build-Werkzeuge (pip-Cache, Wheel-Build) nicht im
# fertigen Bild landen.
FROM python:3.12-slim AS build

WORKDIR /build

# Nur die Sidecar-Metadaten zuerst, damit ein Layer-Cache greift, solange sich
# die Abhängigkeiten nicht ändern.
COPY sidecar/pyproject.toml sidecar/pyproject.toml
COPY sidecar/icarus_memory sidecar/icarus_memory

# Schlanke Variante ohne das Extra "cognee" (zieht ~950 MB nach, siehe
# docs/adr/0005-cognee-statt-mem0.md) — der Container ist für den
# Gedächtniskern gedacht, nicht für die semantische Suche.
RUN pip install --no-cache-dir --prefix=/install "./sidecar"

# -- Laufzeit-Stufe ------------------------------------------------------------
FROM python:3.12-slim AS runtime

# Nicht als root laufen: eigener, unprivilegierter Benutzer.
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin icarus

COPY --from=build /install /usr/local

# Die Oberfläche. Reine HTML/CSS/JS-Dateien ohne Bauschritt — das Frontend
# kommt bewusst ohne Framework aus, deshalb gibt es hier nichts zu übersetzen.
COPY app/src /opt/icarus/ui

# Ablageort des Selbstmodells (SQLite, einstellungen.json). Muss dem
# Anwendungsbenutzer gehören, sonst schlägt bereits das Anlegen der
# SQLite-Datei fehl.
ENV ICARUS_DATA_DIR=/daten
RUN mkdir -p /daten && chown icarus:icarus /daten
VOLUME ["/daten"]

# Ohne gesetztes Token läuft der Sidecar OFFEN — siehe server.py. Das Bild
# setzt bewusst keinen Vorgabewert, damit ein vergessenes Token aus einem
# leeren `docker run` sofort als "kein Zugriffsschutz" sichtbar wird, statt
# sich hinter einem Beispielwert zu verstecken.
# ICARUS_SIDECAR_TOKEN=

# Kein Dateizugriff ohne ausdrückliche Freigabe (siehe .env.example) — leer
# ist Absicht, kein Versehen.
ENV ICARUS_FILE_ROOTS=""

# Innerhalb des Containers muss auf alle Adressen gebunden werden, sonst greift
# die Portfreigabe nicht — 127.0.0.1 wäre hier das Loopback des Containers, nicht
# das des Hosts. Was von außen erreichbar ist, entscheidet allein die Freigabe in
# compose.yaml, und die muss `127.0.0.1:8765:8765` lauten.
ENV ICARUS_SIDECAR_HOST=0.0.0.0
ENV ICARUS_SIDECAR_PORT=8765

# Im Container gibt es keine Tauri-App, also liefert der Sidecar die Oberfläche
# selbst aus und man öffnet sie im Browser. In der App bleibt das ungenutzt.
ENV ICARUS_UI_DIR=/opt/icarus/ui

WORKDIR /home/icarus
USER icarus

EXPOSE 8765

# /health verlangt kein Token (siehe server.py) — der Healthcheck funktioniert
# also unabhängig davon, ob ICARUS_SIDECAR_TOKEN gesetzt ist.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${ICARUS_SIDECAR_PORT}/health', timeout=2)" || exit 1

ENTRYPOINT ["icarus-sidecar"]
