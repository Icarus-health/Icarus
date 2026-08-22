"""Ein Modell anschließen — und zwar irgendeines.

Der Kern ist nicht, dass OpenAI funktioniert. Der Kern ist, dass jemand mit
einem Zugang bei OpenRouter, Groq, DeepSeek oder einem lokalen LM Studio
denselben Weg geht: Adresse eintragen, Modell aus der Liste wählen, prüfen.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from icarus_memory import config, providers


# -- Ein echter kleiner Server statt einer Attrappe --------------------------
#
# Eine Attrappe prüft, ob mein Code meine eigene Erwartung erfüllt. Ein echter
# Server über eine echte Steckverbindung prüft, ob er mit HTTP zurechtkommt.


class Endpunkt(BaseHTTPRequestHandler):
    antwort: dict | None = {"data": [{"id": "modell-b"}, {"id": "modell-a"}, {"id": "modell-a"}]}
    status = 200

    def do_GET(self):  # noqa: N802 - von der Bibliothek vorgegeben
        if not self.path.endswith("/models"):
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if type(self).antwort is not None:
            self.wfile.write(json.dumps(type(self).antwort).encode())

    def log_message(self, *_):  # Ruhe im Testlauf
        pass


@pytest.fixture
def endpunkt():
    server = HTTPServer(("127.0.0.1", 0), Endpunkt)
    faden = threading.Thread(target=server.serve_forever, daemon=True)
    faden.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()


def test_modelle_kommen_sortiert_und_ohne_doppelte(endpunkt) -> None:
    Endpunkt.antwort = {"data": [{"id": "modell-b"}, {"id": "modell-a"}, {"id": "modell-a"}]}
    Endpunkt.status = 200

    assert providers.verfuegbare_modelle(endpunkt) == ["modell-a", "modell-b"]


def test_ein_endpunkt_ohne_liste_ist_kein_fehler(endpunkt) -> None:
    """Nicht jeder Dienst führt eine Liste. Dann bleibt es ein Tippfeld."""
    Endpunkt.status = 404
    Endpunkt.antwort = None

    assert providers.verfuegbare_modelle(endpunkt) == []


def test_krumme_antwort_kippt_nichts(endpunkt) -> None:
    Endpunkt.status = 200
    Endpunkt.antwort = {"data": "das ist keine Liste"}

    assert providers.verfuegbare_modelle(endpunkt) == []


def test_unerreichbar_ist_kein_absturz() -> None:
    assert providers.verfuegbare_modelle("http://127.0.0.1:1/v1") == []


# -- Der Anschluss selbst ----------------------------------------------------


def test_kompatibler_anbieter_braucht_eine_adresse(monkeypatch) -> None:
    """Ohne Adresse gibt es keinen Anbieter — statt stillschweigend OpenAI."""
    monkeypatch.setenv("ICARUS_PROVIDER", "kompatibel")
    monkeypatch.delenv("ICARUS_BASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    assert providers.from_env() is None


def test_kompatibler_anbieter_geht_an_die_eigene_adresse(monkeypatch) -> None:
    monkeypatch.setenv("ICARUS_PROVIDER", "kompatibel")
    monkeypatch.setenv("ICARUS_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("ICARUS_MODEL", "meta-llama/llama-3.1-70b-instruct")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    anbieter = providers.from_env()

    assert anbieter is not None
    assert anbieter.model == "meta-llama/llama-3.1-70b-instruct"
    assert "openrouter" in anbieter.base_url


def test_ein_lokaler_server_ohne_schluessel_geht_trotzdem(monkeypatch) -> None:
    """LM Studio und llama.cpp verlangen keinen Schlüssel — und ein leerer
    Kopf lässt manche von ihnen mit einem 401 antworten. Deshalb ein
    Platzhalter statt nichts."""
    monkeypatch.setenv("ICARUS_PROVIDER", "kompatibel")
    monkeypatch.setenv("ICARUS_BASE_URL", "http://localhost:1234/v1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    anbieter = providers.from_env()

    assert anbieter is not None
    # Bewusst am privaten Feld: der Schlüssel ist nirgends öffentlich, und
    # genau das soll so bleiben. Geprüft wird nur, dass einer gesetzt ist.
    assert anbieter._key


def test_der_katalog_und_die_auswahl_bleiben_beieinander() -> None:
    """Jeder wählbare Anbieter muss auch eine Beschriftung haben.

    Sonst steht in der Auswahl irgendwann ein nackter Schlüsselwert wie
    „kompatibel“ — und das ist genau die Sorte Technik, die vorne nichts zu
    suchen hat.
    """
    for name in config.PROVIDERS:
        assert name in config.PROVIDER_LABELS, f"Ohne Beschriftung: {name!r}"
