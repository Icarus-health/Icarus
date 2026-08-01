"""Tests des Grundsatzes aus CLAUDE.md: die für den Nutzer einfachste Lösung.

Nutzerfreundlichkeit klingt nach Geschmack und ist es hier nicht. Die Punkte,
die geprüft werden, sind alle von derselben Art: **Verlangt das Programm Wissen,
das der Nutzer nicht haben kann?** Darauf gibt es eine richtige Antwort.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from icarus_memory import MemoryBackend, SelfModelStore
from icarus_memory.audit import AuditLog
from icarus_memory.episodes import EpisodeStore
from icarus_memory.providers_mail import BY_ID, PROVIDERS, guess
from icarus_memory.server import create_app
from icarus_memory.tasks import TaskStore
from icarus_memory.workspace import WorkspaceStore


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("ICARUS_DATA_DIR", str(tmp_path))
    for name in ("ICARUS_PROVIDER", "ICARUS_IMAP_HOST", "ICARUS_MAIL_USER",
                 "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    app = create_app(
        SelfModelStore(MemoryBackend(), subject_id="test"),
        audit=AuditLog(tmp_path / "audit.sqlite3"),
        tasks=TaskStore(tmp_path / "tasks.sqlite3"),
        workspace=WorkspaceStore(tmp_path / "workspace.sqlite3"),
        episodes=EpisodeStore(tmp_path / "episodes.sqlite3"),
    )
    yield TestClient(app)
    app.state.scheduler.stop()


# -- „Muss der Nutzer etwas wissen, das er nicht wissen kann?" --------------


@pytest.mark.parametrize(
    "adresse,erwartet",
    [
        ("soeren@gmx.de", "gmx"),
        ("Soeren@GMX.DE", "gmx"),          # Groß/klein darf nicht zählen
        ("jemand@googlemail.com", "gmail"),
        ("jemand@me.com", "icloud"),
        ("jemand@hotmail.com", "outlook"),
        ("jemand@web.de", "webde"),
    ],
)
def test_der_anbieter_faellt_aus_der_adresse(adresse: str, erwartet: str) -> None:
    """Der Regelfall ist ein einziges Feld."""
    treffer = guess(adresse)
    assert treffer is not None and treffer.id == erwartet


def test_eigene_domain_wird_nicht_geraten() -> None:
    """Falsch geratene Serverangaben sind schlimmer als gar keine: Der Nutzer
    sieht ausgefüllte Felder und sucht den Fehler überall, nur nicht dort."""
    assert guess("soeren@icarus-health.de") is None
    assert guess("keine-adresse") is None


def test_app_passwort_wird_angesagt_wo_es_noetig_ist() -> None:
    """Ohne diesen Hinweis tippt jemand dreimal sein richtiges Kennwort ein,
    bekommt dreimal „Anmeldung fehlgeschlagen“ und hält das Programm für kaputt.
    """
    for name in ("gmail", "icloud", "outlook", "yahoo", "fastmail"):
        anbieter = BY_ID[name]
        assert anbieter.app_password is True
        assert anbieter.hint, f"{name} verlangt ein App-Passwort und sagt es nicht"
        assert anbieter.help_url.startswith("https://")


def test_jeder_anbieter_ist_vollstaendig() -> None:
    """Ein halb gepflegter Eintrag füllt die Hälfte der Felder und lässt den
    Nutzer mit dem Rest allein — schlechter als kein Eintrag."""
    for anbieter in PROVIDERS:
        assert anbieter.imap_host and anbieter.smtp_host
        assert anbieter.domains, f"{anbieter.id} ist über die Adresse nicht findbar"
        # Ein Hinweis ohne Link schickt den Nutzer auf die Suche.
        if anbieter.app_password:
            assert anbieter.help_url


def test_die_oberflaeche_bekommt_die_liste(client: TestClient) -> None:
    daten = client.get("/setup").json()
    assert daten["mail_providers"]
    assert {"id", "label", "imap_host", "smtp_host", "app_password", "hint"} <= set(
        daten["mail_providers"][0]
    )


def test_erkennung_ueber_die_schnittstelle(client: TestClient) -> None:
    antwort = client.get("/setup/mail-provider", params={"address": "x@gmx.at"})
    assert antwort.json()["provider"]["imap_host"] == "imap.gmx.net"

    leer = client.get("/setup/mail-provider", params={"address": "x@eigene.de"})
    assert leer.json()["provider"] is None


# -- „Muss er dasselbe zweimal eingeben?" -----------------------------------


def test_ein_anbieter_reicht_fuer_lesen_und_senden() -> None:
    """IMAP und SMTP getrennt abzufragen wäre zweimal dieselbe Entscheidung."""
    for anbieter in PROVIDERS:
        assert anbieter.smtp_host, f"{anbieter.id} kann lesen und nicht senden"


# -- „Ist die Vorgabe die richtige?" ----------------------------------------


def test_standardports_sind_die_verschluesselten() -> None:
    """993 und 587, nicht 143 und 25. Die meisten ändern nichts — was
    voreingestellt ist, ist damit die Entscheidung für fast alle."""
    for anbieter in PROVIDERS:
        assert anbieter.imap_port == 993
        assert anbieter.smtp_port == 587


# -- „Muss er tippen, was er zeigen könnte?" --------------------------------
#
# Ganz vermeiden lässt sich das Tippen nicht: Im Container gibt es keinen
# nativen Auswahldialog, und in der App wäre er eine zusätzliche Abhängigkeit.
# Was sich vermeiden lässt, ist der stille Tippfehler — und dass man denselben
# Pfad dreimal eingibt.


def test_ein_falscher_pfad_faellt_sofort_auf(client: TestClient, tmp_path) -> None:
    """Ohne diese Prüfung merkt man den Tippfehler erst, wenn die Aufnahme
    scheitert — drei Bildschirme später, mit einer Meldung, die ihn nicht nennt.
    """
    antwort = client.get("/setup/folder", params={"path": str(tmp_path / "gibtsnicht")})

    assert antwort.json()["ok"] is False
    assert "gibt es nicht" in antwort.json()["detail"]


def test_eine_datei_ist_kein_ordner(client: TestClient, tmp_path) -> None:
    datei = tmp_path / "notiz.md"
    datei.write_text("x", encoding="utf-8")

    antwort = client.get("/setup/folder", params={"path": str(datei)})

    assert antwort.json()["ok"] is False
    assert "Datei" in antwort.json()["detail"]


def test_der_ordner_sagt_was_er_enthaelt(client: TestClient, tmp_path) -> None:
    """„3 lesbare Dateien gefunden“ bestätigt, dass es der *gemeinte* Ordner
    ist. Ein Pfad, der existiert und leer ist, ist meistens der falsche."""
    vault = tmp_path / "Vault"
    vault.mkdir()
    for i in range(3):
        (vault / f"n{i}.md").write_text("Inhalt", encoding="utf-8")

    antwort = client.get("/setup/folder", params={"path": str(vault)}).json()

    assert antwort["ok"] is True
    assert antwort["files"] == 3
    assert "3 lesbare Dateien" in antwort["detail"]


def test_ein_leerer_ordner_ist_kein_fehler_aber_wird_gesagt(
    client: TestClient, tmp_path
) -> None:
    leer = tmp_path / "leer"
    leer.mkdir()

    antwort = client.get("/setup/folder", params={"path": str(leer)}).json()

    assert antwort["ok"] is True
    assert "keine lesbaren" in antwort["detail"]


def test_die_pruefung_liest_nichts(client: TestClient, tmp_path) -> None:
    """Sie zählt nur. Sonst wäre sie ein Weg, an der Freigabe vorbei den
    Rechner zu lesen — die Prüfung läuft ja *vor* dem Freigeben.
    """
    vault = tmp_path / "Vault"
    vault.mkdir()
    (vault / "geheim.md").write_text("Vertraulich", encoding="utf-8")

    antwort = client.get("/setup/folder", params={"path": str(vault)}).json()

    assert "Vertraulich" not in str(antwort)
    assert set(antwort) == {"ok", "path", "files", "detail"}


def test_tilde_wird_aufgeloest(client: TestClient) -> None:
    """`~/Dokumente` ist die Schreibweise, die Menschen benutzen."""
    antwort = client.get("/setup/folder", params={"path": "~"}).json()
    assert antwort["ok"] is True
    assert not antwort["path"].startswith("~")


# -- Der Kalender hängt am selben Anbieter ----------------------------------


def test_wo_caldav_geht_steht_die_adresse() -> None:
    """Den Anbieter für den Kalender erneut zu erfragen wäre dieselbe
    Entscheidung ein zweites Mal."""
    for name in ("icloud", "gmx", "webde", "mailbox", "posteo", "fastmail"):
        assert BY_ID[name].caldav_url.startswith("https://"), name


def test_wo_caldav_nicht_geht_steht_warum() -> None:
    """Zu schweigen hieße, den Nutzer eine Viertelstunde suchen zu lassen,
    bevor er annimmt, das Programm könne es nicht. Es kann es — der Anbieter
    lässt es nicht zu, und genau das gehört dort zu stehen.
    """
    for name in ("gmail", "outlook"):
        anbieter = BY_ID[name]
        assert not anbieter.caldav_url
        assert anbieter.caldav_note
        # Und der Hinweis muss sagen, dass die Mail davon unberührt bleibt —
        # sonst liest er sich wie „der Anbieter geht gar nicht".
        assert "Mail funktioniert" in anbieter.caldav_note


def test_kein_anbieter_hat_beides() -> None:
    """Eine Adresse *und* eine Begründung, warum es keine gibt, wäre ein
    Widerspruch, den die Oberfläche nicht auflösen kann."""
    for anbieter in PROVIDERS:
        assert not (anbieter.caldav_url and anbieter.caldav_note), anbieter.id


def test_die_kalenderangaben_kommen_mit_der_erkennung(client: TestClient) -> None:
    antwort = client.get(
        "/setup/mail-provider", params={"address": "x@icloud.com"}
    ).json()["provider"]

    assert antwort["caldav_url"] == "https://caldav.icloud.com/"
    assert antwort["caldav_note"] == ""
