"""Tests des Container-Betriebs.

Zwei Dinge unterscheiden ihn vom Betrieb in der App, und beide sind
sicherheitsrelevant: Es gibt keinen Schlüsselbund des Betriebssystems, und die
Oberfläche wird vom Sidecar selbst ausgeliefert statt von einer nativen Hülle.
"""

from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from icarus_memory import MemoryBackend, SelfModelStore
from icarus_memory.audit import AuditLog
from icarus_memory.crypto import DecryptionError, seal_json, unseal_json
from icarus_memory.episodes import EpisodeStore
from icarus_memory.secrets import (
    PASSPHRASE_ENV,
    SECRETS_FILE,
    Keychain,
    KeychainError,
    load_into_env,
)
from icarus_memory.server import TOKEN_ENV, create_app
from icarus_memory.tasks import TaskStore
from icarus_memory.workspace import WorkspaceStore


@pytest.fixture(autouse=True)
def saubere_umgebung():
    namen = (PASSPHRASE_ENV, TOKEN_ENV, "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
             "ICARUS_MAIL_PASSWORD", "ICARUS_UI_DIR")
    vorher = {n: os.environ.get(n) for n in namen}
    for n in namen:
        os.environ.pop(n, None)
    yield
    for n, wert in vorher.items():
        if wert is None:
            os.environ.pop(n, None)
        else:
            os.environ[n] = wert


# -- Verschlüsselung --------------------------------------------------------


def test_umschlag_geht_auf_und_zu() -> None:
    text = seal_json({"a": "b"}, "passphrase", "test-v1")
    assert unseal_json(text, "passphrase") == {"a": "b"}


def test_klartext_steht_nicht_im_umschlag() -> None:
    text = seal_json({"OPENAI_API_KEY": "sk-sehr-geheim"}, "pw", "test-v1")
    assert "sk-sehr-geheim" not in text


def test_falsche_passphrase_wird_erkannt() -> None:
    text = seal_json({"a": "b"}, "richtig", "test-v1")
    with pytest.raises(DecryptionError):
        unseal_json(text, "falsch")


def test_veraenderte_daten_werden_erkannt() -> None:
    """Encrypt-then-MAC: Der Prüfwert deckt Nonce und Chiffrat ab.

    Ohne diese Prüfung ließe sich das Chiffrat gezielt verändern, und ein
    Strom-Chiffre gibt dabei die Kontrolle über einzelne Bits.
    """
    umschlag = json.loads(seal_json({"a": "b"}, "pw", "test-v1"))
    roh = list(umschlag["data"])
    roh[0] = "A" if roh[0] != "A" else "B"
    umschlag["data"] = "".join(roh)

    with pytest.raises(DecryptionError, match="Prüfsumme"):
        unseal_json(json.dumps(umschlag), "pw")


def test_jeder_umschlag_ist_anders() -> None:
    """Salt und Nonce je Vorgang neu — sonst verrät gleiches Chiffrat gleichen
    Inhalt."""
    erster = seal_json({"a": "b"}, "pw", "test-v1")
    zweiter = seal_json({"a": "b"}, "pw", "test-v1")
    assert erster != zweiter


def test_export_format_bleibt_lesbar(tmp_path) -> None:
    """Bestehende verschlüsselte Exporte müssen weiter aufgehen — das Verfahren
    wurde in ein gemeinsames Modul gezogen, nicht geändert."""
    from icarus_memory.backup import export_model, import_model

    payload = export_model({"schema_version": "0.1.0", "assertions": []}, "pw")
    assert import_model(payload, "pw")["schema_version"] == "0.1.0"


# -- Schlüsseldatei ---------------------------------------------------------


def test_ohne_passphrase_kein_dateispeicher(tmp_path) -> None:
    """Die Datei ist die Antwort auf „es gibt keinen Schlüsselbund", nicht ein
    stiller Standard."""
    keychain = Keychain(data_dir=tmp_path)
    if keychain.backend not in ("macos", "windows", "secret-tool"):
        assert keychain.backend == "none"
        assert not keychain.available


def test_schluessel_landen_verschluesselt_auf_der_platte(tmp_path) -> None:
    os.environ[PASSPHRASE_ENV] = "geheime-passphrase"
    keychain = Keychain(data_dir=tmp_path)
    if keychain.backend != "file":
        pytest.skip("Betriebssystem-Schlüsselbund vorhanden, Dateiweg nicht aktiv.")

    keychain.set("OPENAI_API_KEY", "sk-nicht-im-klartext")

    inhalt = (tmp_path / SECRETS_FILE).read_text(encoding="utf-8")
    assert "sk-nicht-im-klartext" not in inhalt
    assert keychain.get("OPENAI_API_KEY") == "sk-nicht-im-klartext"


def test_schluesseldatei_ist_nur_fuer_den_eigentuemer_lesbar(tmp_path) -> None:
    os.environ[PASSPHRASE_ENV] = "pw"
    keychain = Keychain(data_dir=tmp_path)
    if keychain.backend != "file":
        pytest.skip("Betriebssystem-Schlüsselbund vorhanden.")

    keychain.set("OPENAI_API_KEY", "sk-x")
    assert (tmp_path / SECRETS_FILE).stat().st_mode & 0o077 == 0


def test_schluessel_ueberleben_den_neustart(tmp_path) -> None:
    os.environ[PASSPHRASE_ENV] = "pw"
    erste = Keychain(data_dir=tmp_path)
    if erste.backend != "file":
        pytest.skip("Betriebssystem-Schlüsselbund vorhanden.")

    erste.set("ANTHROPIC_API_KEY", "sk-ant-bleibt")
    assert Keychain(data_dir=tmp_path).get("ANTHROPIC_API_KEY") == "sk-ant-bleibt"


def test_falsche_passphrase_gibt_nichts_heraus(tmp_path) -> None:
    os.environ[PASSPHRASE_ENV] = "richtig"
    keychain = Keychain(data_dir=tmp_path)
    if keychain.backend != "file":
        pytest.skip("Betriebssystem-Schlüsselbund vorhanden.")
    keychain.set("OPENAI_API_KEY", "sk-x")

    os.environ[PASSPHRASE_ENV] = "falsch"
    assert Keychain(data_dir=tmp_path).get("OPENAI_API_KEY") is None


def test_kaputte_schluesseldatei_blockiert_den_start_nicht(tmp_path) -> None:
    """Ein leerer Speicher ist reparierbar. Ein Programm, das nicht startet,
    lässt den Nutzer nicht mehr an sein Gedächtnis."""
    os.environ[PASSPHRASE_ENV] = "pw"
    (tmp_path / SECRETS_FILE).write_text("{kaputt", encoding="utf-8")

    keychain = Keychain(data_dir=tmp_path)
    if keychain.backend != "file":
        pytest.skip("Betriebssystem-Schlüsselbund vorhanden.")
    assert keychain.get("OPENAI_API_KEY") is None
    assert load_into_env(keychain) == []


def test_loeschen_entfernt_nur_den_einen(tmp_path) -> None:
    os.environ[PASSPHRASE_ENV] = "pw"
    keychain = Keychain(data_dir=tmp_path)
    if keychain.backend != "file":
        pytest.skip("Betriebssystem-Schlüsselbund vorhanden.")

    keychain.set("OPENAI_API_KEY", "sk-a")
    keychain.set("ICARUS_MAIL_PASSWORD", "geheim")
    keychain.delete("OPENAI_API_KEY")

    assert keychain.get("OPENAI_API_KEY") is None
    assert keychain.get("ICARUS_MAIL_PASSWORD") == "geheim"


def test_schreiben_ohne_passphrase_schlaegt_hoerbar_fehl(tmp_path) -> None:
    keychain = Keychain(data_dir=tmp_path)
    os.environ[PASSPHRASE_ENV] = "pw"
    keychain._backend = "file"  # noqa: SLF001 - Zustand nach Wegfall der Variable
    del os.environ[PASSPHRASE_ENV]

    with pytest.raises(KeychainError, match=PASSPHRASE_ENV):
        keychain.set("OPENAI_API_KEY", "sk-x")


# -- Oberfläche über HTTP ---------------------------------------------------


def _app(tmp_path, ui_dir=None):
    if ui_dir is not None:
        os.environ["ICARUS_UI_DIR"] = str(ui_dir)
    return create_app(
        SelfModelStore(MemoryBackend(), subject_id="test"),
        audit=AuditLog(tmp_path / "audit.sqlite3"),
        tasks=TaskStore(tmp_path / "tasks.sqlite3"),
        workspace=WorkspaceStore(tmp_path / "workspace.sqlite3"),
        episodes=EpisodeStore(tmp_path / "episodes.sqlite3"),
    )


@pytest.fixture
def ui(tmp_path):
    verzeichnis = tmp_path / "ui"
    verzeichnis.mkdir()
    (verzeichnis / "index.html").write_text(
        "<!doctype html><title>Icarus</title><body>Hallo", encoding="utf-8"
    )
    (verzeichnis / "main.js").write_text("// test", encoding="utf-8")
    return verzeichnis


def test_oberflaeche_wird_ausgeliefert(tmp_path, ui) -> None:
    client = TestClient(_app(tmp_path, ui))
    antwort = client.get("/")
    assert antwort.status_code == 200
    assert "Icarus" in antwort.text
    assert client.get("/main.js").status_code == 200


def test_die_seite_verdeckt_keine_endpunkte(tmp_path, ui) -> None:
    """Ein Mount auf „/" fängt alles ab, was vorher nicht registriert wurde.

    Stünde er zu früh, wären sämtliche Datenendpunkte unerreichbar — und zwar
    still, mit einer 404 statt einer Fehlermeldung.
    """
    client = TestClient(_app(tmp_path, ui))
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/assertions").status_code == 200
    assert client.get("/episodes/counts").status_code == 200
    assert client.get("/setup").status_code == 200


def test_daten_bleiben_hinter_dem_token_auch_mit_oberflaeche(tmp_path, ui) -> None:
    """Die Kernfrage des Containerbetriebs.

    Die Seite selbst ist ungeschützt — sie enthält kein Nutzerdatum. Alles
    darunter darf es nicht sein.
    """
    os.environ[TOKEN_ENV] = "geheim"
    try:
        client = TestClient(_app(tmp_path, ui))
        # Die Seite: frei, sonst könnte der Browser sie nie laden.
        assert client.get("/").status_code == 200
        # Die Daten: nicht.
        assert client.get("/assertions").status_code == 401
        assert client.get(
            "/assertions", headers={"x-icarus-token": "geheim"}
        ).status_code == 200
    finally:
        os.environ.pop(TOKEN_ENV, None)


def test_ohne_oberflaeche_laeuft_alles_weiter(tmp_path) -> None:
    """In der Tauri-App liefert die App die Dateien aus; der Sidecar tut nichts."""
    os.environ["ICARUS_UI_DIR"] = str(tmp_path / "gibtesnicht")
    client = TestClient(_app(tmp_path))
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/").status_code == 404


# -- Die ausgelieferte Oberfläche selbst ------------------------------------
#
# Zwei Tests über den *Text* der Dateien, nicht über ihr Verhalten — für einen
# echten DOM-Test bräuchte es einen Browser in der CI, und der wäre teurer als
# das, was er hier prüft. Beide fangen einen Rückfall, der real passiert ist und
# beim Lesen des Codes nicht auffällt: Die App startete, sah richtig aus und war
# im Browser trotzdem unbenutzbar.


def _frontend(name: str) -> str:
    from pathlib import Path

    return (Path(__file__).resolve().parents[2] / "app" / "src" / name).read_text(
        encoding="utf-8"
    )


def test_die_oberflaeche_findet_den_sidecar_auch_ohne_tauri() -> None:
    """Im Container läuft dieselbe Seite in einem normalen Browser.

    Dort gibt es kein `invoke`. Ein `await invoke("sidecar_info")` im Start
    scheitert still — die Adresse bleibt `null`, jeder Aufruf geht gegen
    `null/setup`, und der Nutzer sieht eine Oberfläche, die nichts tut.
    """
    js = _frontend("main.js")

    assert "connectionInfo()" in js
    # Tauri darf nur an einer Stelle vorkommen: in connectionInfo selbst.
    assert js.count("__TAURI__") == 1
    # Und nirgends ein nackter Aufruf daneben.
    assert "await invoke(" not in js


def test_verstecktes_bleibt_versteckt() -> None:
    """`hidden` ist die schwächste Regel im Browser.

    `#wizard { display: grid }` schlägt sie. Dann liegt der fertig durchlaufene
    Einrichtungsassistent als unsichtbare Fläche über der ganzen App und
    schluckt jeden Klick — ein Fehler, den man nicht sieht, sondern nur spürt.
    """
    css = _frontend("style.css")

    assert "[hidden]" in css
    regel = css.split("[hidden]", 1)[1].split("}", 1)[0]
    assert "display: none" in regel and "!important" in regel
