"""Sicherung und Wiederherstellung des Selbstmodells.

Ein Gedächtnis, das zwanzig Jahre halten soll, hat genau einen katastrophalen
Fehlerfall: Es ist weg. Eine defekte Platte, ein verlorener Rechner, ein
misslungenes Update.

Deshalb drei Dinge:

* **Snapshots** über SQLites eigene Backup-Schnittstelle — konsistent auch dann,
  wenn gerade geschrieben wird. Ein `cp` der Datei ist es nicht.
* **Rotation**, damit die Sicherungen nicht die Platte füllen.
* **Export** als offenes JSON gegen das Schema, optional verschlüsselt. Eine
  SQLite-Datei nützt in zehn Jahren wenig, wenn niemand mehr weiß, welches
  Programm sie geschrieben hat.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .model import now

SNAPSHOT_PREFIX = "self-model-"
EXPORT_MAGIC = "icarus-export-v1"
KDF_ITERATIONS = 600_000


class BackupError(Exception):
    pass


# -- Snapshots -------------------------------------------------------------


def snapshot(db_path: Path, target_dir: Path, keep: int = 14, at: datetime | None = None) -> Path:
    """Legt eine konsistente Kopie der Datenbank an und rotiert alte weg."""
    at = at or now()
    if not db_path.is_file():
        raise BackupError(f"Keine Datenbank unter {db_path}")

    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = at.strftime("%Y%m%dT%H%M%SZ")
    target = target_dir / f"{SNAPSHOT_PREFIX}{stamp}.sqlite3"

    source = sqlite3.connect(str(db_path))
    try:
        destination = sqlite3.connect(str(target))
        try:
            # SQLites Backup-API sperrt korrekt; ein Dateikopieren während eines
            # laufenden Schreibvorgangs ergäbe eine beschädigte Kopie.
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()

    prune(target_dir, keep)
    return target


def prune(target_dir: Path, keep: int) -> list[Path]:
    """Behält die neuesten `keep` Snapshots und entfernt den Rest."""
    snapshots = sorted(
        target_dir.glob(f"{SNAPSHOT_PREFIX}*.sqlite3"),
        key=lambda p: p.name,
        reverse=True,
    )
    removed = []
    for old in snapshots[keep:]:
        old.unlink()
        removed.append(old)
    return removed


def list_snapshots(target_dir: Path) -> list[dict[str, Any]]:
    if not target_dir.is_dir():
        return []
    entries = []
    for path in sorted(target_dir.glob(f"{SNAPSHOT_PREFIX}*.sqlite3"), reverse=True):
        stat = path.stat()
        entries.append({
            "name": path.name,
            "path": str(path),
            "bytes": stat.st_size,
            "created": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(),
        })
    return entries


def restore(snapshot_path: Path, db_path: Path) -> None:
    """Spielt einen Snapshot zurück — nach Prüfung, dass er lesbar ist.

    Die vorhandene Datenbank wird vorher zur Seite gelegt. Eine
    Wiederherstellung, die den aktuellen Stand unwiederbringlich überschreibt,
    ist ein zweiter Weg, alles zu verlieren.
    """
    if not snapshot_path.is_file():
        raise BackupError(f"Kein Snapshot unter {snapshot_path}")

    check = sqlite3.connect(str(snapshot_path))
    try:
        result = check.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise BackupError(f"Snapshot ist beschädigt: {result[0] if result else 'unlesbar'}")
        check.execute("SELECT COUNT(*) FROM assertions")
    except sqlite3.DatabaseError as exc:
        raise BackupError(f"Snapshot nicht lesbar: {exc}") from exc
    finally:
        check.close()

    if db_path.is_file():
        aside = db_path.with_suffix(f".vor-wiederherstellung-{now():%Y%m%dT%H%M%SZ}.sqlite3")
        db_path.replace(aside)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(str(snapshot_path))
    try:
        destination = sqlite3.connect(str(db_path))
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()


# -- Export ----------------------------------------------------------------


def _derive(passphrase: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, KDF_ITERATIONS, 32)


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    """Schlüsselstrom aus HMAC-SHA256 im Zählerbetrieb.

    Bewusst ohne Kryptobibliothek, damit der Export keine Abhängigkeit hat, die
    in zehn Jahren vielleicht nicht mehr baut. HMAC-SHA256 ist in jeder
    Python-Standardbibliothek und in jeder anderen Sprache verfügbar — das
    Format bleibt entzifferbar, auch ohne dieses Programm.
    """
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:length])


def export_model(model_dict: dict[str, Any], passphrase: str | None = None) -> str:
    """Schreibt das Selbstmodell als JSON, optional verschlüsselt.

    Ohne Passphrase: lesbares JSON, passend zu schema/self-model.schema.json.
    Mit Passphrase: verschlüsselt und mit HMAC gegen Veränderung geschützt.
    """
    plain = json.dumps(model_dict, ensure_ascii=False, indent=2).encode("utf-8")
    if not passphrase:
        return plain.decode("utf-8")

    salt = os.urandom(16)
    nonce = os.urandom(16)
    key = _derive(passphrase, salt)
    cipher = bytes(a ^ b for a, b in zip(plain, _keystream(key, nonce, len(plain))))
    # Erst verschlüsseln, dann authentifizieren.
    tag = hmac.new(key, nonce + cipher, hashlib.sha256).digest()

    return json.dumps({
        "format": EXPORT_MAGIC,
        "kdf": {"name": "pbkdf2-sha256", "iterations": KDF_ITERATIONS,
                "salt": base64.b64encode(salt).decode()},
        "cipher": "hmac-sha256-ctr",
        "nonce": base64.b64encode(nonce).decode(),
        "data": base64.b64encode(cipher).decode(),
        "tag": base64.b64encode(tag).decode(),
    }, indent=2)


def import_model(payload: str, passphrase: str | None = None) -> dict[str, Any]:
    """Liest einen Export, entschlüsselt bei Bedarf und prüft die Unversehrtheit."""
    document = json.loads(payload)
    if document.get("format") != EXPORT_MAGIC:
        return document  # unverschlüsselter Export

    if not passphrase:
        raise BackupError("Dieser Export ist verschlüsselt. Passphrase erforderlich.")

    salt = base64.b64decode(document["kdf"]["salt"])
    nonce = base64.b64decode(document["nonce"])
    cipher = base64.b64decode(document["data"])
    tag = base64.b64decode(document["tag"])
    key = _derive(passphrase, salt)

    expected = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise BackupError(
            "Prüfsumme stimmt nicht. Falsche Passphrase oder veränderte Datei."
        )

    plain = bytes(a ^ b for a, b in zip(cipher, _keystream(key, nonce, len(cipher))))
    return json.loads(plain.decode("utf-8"))


__all__ = [
    "BackupError",
    "export_model",
    "import_model",
    "list_snapshots",
    "prune",
    "restore",
    "snapshot",
]
