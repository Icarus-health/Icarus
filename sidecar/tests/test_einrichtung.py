"""Tests der Einrichtung.

Die entscheidende Frage ist nicht, ob Werte gespeichert werden, sondern ob
danach **etwas anders funktioniert** — ohne Neustart. Ein Assistent, der
„gespeichert" sagt und beim ersten echten Gebrauch scheitert, ist schlimmer als
keiner.
"""

from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from icarus_memory import MemoryBackend, SelfModelStore
from icarus_memory import config
from icarus_memory.audit import AuditLog
from icarus_memory.episodes import EpisodeStore
from icarus_memory.secrets import Keychain
from icarus_memory.server import create_app
from icarus_memory.tasks import TaskStore
from icarus_memory.workspace import WorkspaceStore

#: Umgebungsvariablen, die die Einrichtung setzt. Zwischen den Tests weg, sonst
#: färbt ein Test auf den nächsten ab — und die Vorrangregel wäre nicht prüfbar.
ENV_NAMES = (
    "ICARUS_PROVIDER", "ICARUS_MODEL", "ICARUS_BASE_URL", "ICARUS_FILE_ROOTS",
    "ICARUS_IMAP_HOST", "ICARUS_IMAP_PORT", "ICARUS_SMTP_HOST",
    "ICARUS_SMTP_PORT", "ICARUS_MAIL_USER", "ICARUS_MAIL_FROM",
    "ICARUS_CALDAV_URL", "ICARUS_CALDAV_USER",
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "LLM_API_KEY",
    "ICARUS_MAIL_PASSWORD", "ICARUS_CALDAV_PASSWORD",
)


@pytest.fixture(autouse=True)
def saubere_umgebung():
    """Nimmt die Umgebung vorher **und nachher** zurück.

    Die Einrichtung schreibt absichtlich in `os.environ` — daraus lesen
    `providers.from_env()`, `MailConfig.from_env()` und
    `file_roots_from_env()`. Genau deshalb muss dieser Test aufräumen: Ohne das
    sieht die nächste Testdatei einen eingerichteten Mailzugang, den sie nie
    gesetzt hat, und scheitert an einer Stelle, die nichts damit zu tun hat.
    """
    vorher = {name: os.environ.get(name) for name in ENV_NAMES}
    for name in ENV_NAMES:
        os.environ.pop(name, None)
    yield
    for name, wert in vorher.items():
        if wert is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = wert


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("ICARUS_DATA_DIR", str(tmp_path))
    app = create_app(
        SelfModelStore(MemoryBackend(), subject_id="test"),
        audit=AuditLog(tmp_path / "audit.sqlite3"),
        tasks=TaskStore(tmp_path / "tasks.sqlite3"),
        workspace=WorkspaceStore(tmp_path / "workspace.sqlite3"),
        episodes=EpisodeStore(tmp_path / "episodes.sqlite3"),
    )
    return TestClient(app)


# -- Die Datei --------------------------------------------------------------


def test_erster_start_braucht_nichts(client: TestClient) -> None:
    """Ohne Modell, ohne Schlüssel, ohne Konnektor muss die App laufen.

    Das ist die Zusicherung, an der ein Produkt beim ersten Öffnen scheitert
    oder nicht.
    """
    r = client.get("/setup")
    assert r.status_code == 200
    state = r.json()

    assert state["settings"]["onboarded"] is False
    assert state["settings"]["provider"] == ""
    assert state["status"]["chat"] is False
    assert state["status"]["file_roots"] == []
    assert client.get("/health").json()["status"] == "ok"


def test_kein_ordner_ist_voreingestellt(client: TestClient) -> None:
    """Ein Vorgabewert wie das Home-Verzeichnis wäre die Bequemlichkeit, die
    den Schutz aufhebt. Auch der Assistent schlägt keinen vor."""
    assert client.get("/setup").json()["settings"]["file_roots"] == []


def test_einstellungen_ueberleben_den_neustart(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ICARUS_DATA_DIR", str(tmp_path))

    settings = config.Settings(provider="ollama", model="llama3.1", onboarded=True)
    settings.file_roots = [str(tmp_path / "Notizen")]
    config.save(tmp_path, settings)

    wieder = config.load(tmp_path)
    assert wieder.provider == "ollama"
    assert wieder.onboarded is True
    assert wieder.file_roots == [str(tmp_path / "Notizen")]


def test_kaputte_datei_blockiert_den_start_nicht(tmp_path) -> None:
    """Sonst käme ein Nutzer mit beschädigter Datei nie wieder an seinen
    Bestand. Ein leeres Formular ist reparierbar, ein Programm, das nicht
    startet, nicht."""
    config.path_for(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    config.path_for(tmp_path).write_text("{kaputt", encoding="utf-8")

    assert config.load(tmp_path) == config.Settings()


def test_datei_aus_neuerer_version_kippt_nicht(tmp_path) -> None:
    config.path_for(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    config.path_for(tmp_path).write_text(
        json.dumps({"provider": "openai", "zukunftsfeld": {"a": 1}}), encoding="utf-8"
    )
    assert config.load(tmp_path).provider == "openai"


def test_datei_ist_nur_fuer_den_eigentuemer_lesbar(tmp_path) -> None:
    """Keine Geheimnisse darin, aber Mail- und Serveradressen muss kein anderes
    Konto auf dem Rechner lesen."""
    pfad = config.save(tmp_path, config.Settings(provider="openai"))
    assert pfad.stat().st_mode & 0o077 == 0


# -- Vorrang ----------------------------------------------------------------


def test_umgebung_schlaegt_die_datei(monkeypatch) -> None:
    """Der Weg, einen Testlauf zu fahren, ohne die Einstellungen des Nutzers
    anzufassen."""
    umgebung = {"ICARUS_PROVIDER": "ollama"}
    settings = config.Settings(provider="openai", model="gpt-4.1-mini")

    config.apply_to_env(settings, umgebung)

    assert umgebung["ICARUS_PROVIDER"] == "ollama"
    assert umgebung["ICARUS_MODEL"] == "gpt-4.1-mini"


def test_leere_werte_setzen_nichts(monkeypatch) -> None:
    umgebung: dict[str, str] = {}
    config.apply_to_env(config.Settings(), umgebung)
    assert "ICARUS_PROVIDER" not in umgebung
    assert "ICARUS_IMAP_HOST" not in umgebung


def test_absender_faellt_auf_den_benutzer_zurueck() -> None:
    umgebung: dict[str, str] = {}
    settings = config.Settings(
        mail=config.MailSettings(imap_host="imap.example.com", user="du@example.com")
    )
    config.apply_to_env(settings, umgebung)
    assert umgebung["ICARUS_MAIL_FROM"] == "du@example.com"


# -- Über die Schnittstelle -------------------------------------------------


def test_anbieter_setzen_wirkt_ohne_neustart(client: TestClient) -> None:
    """Der Kern: Nach dem Eintragen muss der Chat funktionieren, ohne dass
    jemand die App neu startet."""
    assert client.get("/setup").json()["status"]["chat"] is False

    r = client.put("/setup", json={"provider": "anthropic", "api_key": "sk-test-123"})

    assert r.status_code == 200
    status = r.json()["status"]
    assert status["chat"] is True
    assert status["provider"] == "anthropic"
    # Modell wurde mitgezogen, damit der erste Satz nicht an einer leeren
    # Modellangabe scheitert.
    assert status["model"] == config.DEFAULT_MODELS["anthropic"]


def test_schluessel_kommt_nie_zurueck(client: TestClient) -> None:
    """Ein Feld, das den Wert zurückliefert, wäre der bequemste Weg, ihn
    irgendwann zu protokollieren."""
    client.put("/setup", json={"provider": "openai", "api_key": "sk-geheim-xyz"})

    text = client.get("/setup").text
    assert "sk-geheim-xyz" not in text
    assert client.get("/setup").json()["secrets"]["OPENAI_API_KEY"] is True


def test_unbekannter_anbieter_wird_abgewiesen(client: TestClient) -> None:
    r = client.put("/setup", json={"provider": "hausmarke"})
    assert r.status_code == 400
    assert "hausmarke" in r.json()["detail"]


def test_null_laesst_unangetastet_leer_loescht(client: TestClient) -> None:
    """Sonst ließe sich ein einmal eingetragener Mailserver nie wieder
    loswerden."""
    client.put("/setup", json={
        "mail": {"imap_host": "imap.example.com", "user": "du@example.com"},
        "mail_password": "app-passwort",
    })
    assert client.get("/setup").json()["status"]["mail"] is True

    # Etwas anderes ändern — Mail bleibt stehen.
    client.put("/setup", json={"model": "gpt-4.1"})
    assert client.get("/setup").json()["status"]["mail"] is True

    # Ausdrücklich leeren.
    client.put("/setup", json={"mail": {"imap_host": "", "user": ""}})
    assert client.get("/setup").json()["status"]["mail"] is False


def test_mailkonto_ohne_passwort_gilt_nicht_als_eingerichtet(client: TestClient) -> None:
    """Ein Konto, an dem die Zugangsdaten fehlen, ist kein Konnektor.

    Die Oberfläche darf es deshalb nicht als fertig anzeigen — sonst sucht der
    Nutzer den Fehler später beim Server statt beim fehlenden Passwort.
    """
    client.put("/setup", json={
        "mail": {"imap_host": "imap.example.com", "user": "du@example.com"},
    })

    assert client.get("/setup").json()["status"]["mail"] is False
    assert client.get("/setup").json()["secrets"]["ICARUS_MAIL_PASSWORD"] is False


def test_dateiordner_freigeben_wirkt_sofort(client: TestClient, tmp_path) -> None:
    ordner = tmp_path / "Notizen"
    ordner.mkdir()
    (ordner / "x.md").write_text("Inhalt", encoding="utf-8")

    # Vorher: Aufnahme verboten.
    assert client.post("/ingest", json={"path": str(ordner)}).status_code == 403

    client.put("/setup", json={"file_roots": [str(tmp_path)]})

    # Nachher: geht, ohne Neustart.
    r = client.post("/ingest", json={"path": str(ordner), "adapter": "markdown"})
    assert r.status_code == 200
    assert r.json()["recorded"] == 1


def test_ordner_wieder_entziehen_wirkt_sofort(client: TestClient, tmp_path) -> None:
    ordner = tmp_path / "Notizen"
    ordner.mkdir()
    client.put("/setup", json={"file_roots": [str(tmp_path)]})
    assert client.post("/ingest", json={"path": str(ordner)}).status_code == 200

    client.put("/setup", json={"file_roots": []})

    assert client.post("/ingest", json={"path": str(ordner)}).status_code == 403


def test_onboarding_wird_vermerkt(client: TestClient) -> None:
    """Nicht „alles eingerichtet" — nur, dass die App nicht mehr beim Start in
    den Assistenten springen soll."""
    assert client.get("/setup").json()["settings"]["onboarded"] is False
    client.put("/setup", json={"onboarded": True})
    assert client.get("/setup").json()["settings"]["onboarded"] is True


def test_verbindungstest_meldet_ehrlich(client: TestClient) -> None:
    """Ohne Anbieter ist die Antwort „nicht eingerichtet", nicht „ok"."""
    r = client.post("/setup/test/modell")
    assert r.status_code == 200
    assert r.json() == {"ok": False, "detail": "Kein Anbieter eingerichtet."}

    assert client.post("/setup/test/mail").json()["ok"] is False
    assert client.post("/setup/test/kalender").json()["ok"] is False
    assert client.post("/setup/test/unfug").status_code == 400


def test_verbindungstest_gibt_den_echten_fehler_zurueck(client: TestClient) -> None:
    """Ein Assistent, der bei einem falschen Passwort „ok" sagt, schickt den
    Nutzer den Fehler an der falschen Stelle suchen."""
    client.put("/setup", json={
        "mail": {"imap_host": "127.0.0.1", "imap_port": 1, "user": "x@example.com"},
        "mail_password": "falsch",
    })

    ergebnis = client.post("/setup/test/mail").json()
    assert ergebnis["ok"] is False
    assert ergebnis["detail"]  # der tatsächliche Fehler, nicht eine Floskel


def test_ohne_schluesselspeicher_gilt_der_schluessel_nur_fuer_die_sitzung() -> None:
    """Die Alternative wäre, ihn in die Einstellungsdatei zu schreiben — genau
    der Klartext auf der Platte, den secrets.py vermeidet."""
    class OhneSpeicher(Keychain):
        def __init__(self) -> None:
            self._service = "test"
            self._backend = "none"

    keychain = OhneSpeicher()
    try:
        config.store_secret(keychain, "OPENAI_API_KEY", "sk-fluechtig")
        assert os.environ["OPENAI_API_KEY"] == "sk-fluechtig"
        assert config.secret_status(keychain)["OPENAI_API_KEY"] is True
    finally:
        os.environ.pop("OPENAI_API_KEY", None)


def test_einstellungsdatei_enthaelt_nie_ein_geheimnis(client: TestClient, tmp_path) -> None:
    client.put("/setup", json={
        "provider": "openai", "api_key": "sk-geheim-xyz",
        "mail": {"imap_host": "imap.example.com", "user": "du@example.com"},
        "mail_password": "auch-geheim",
    })

    inhalt = config.path_for(tmp_path).read_text(encoding="utf-8")
    assert "sk-geheim-xyz" not in inhalt
    assert "auch-geheim" not in inhalt
    # Das Unkritische steht drin, sonst wäre die Datei nutzlos.
    assert "imap.example.com" in inhalt
