"""Verschlüsselung ohne Abhängigkeit — eine Stelle für alles.

Das Verfahren stammt aus `backup.py`, wo es für verschlüsselte Exporte
entstanden ist. Seit es einen zweiten Ort gibt, der es braucht — die
Schlüsseldatei in `secrets.py`, wenn kein Betriebssystem-Schlüsselbund da ist —
liegt es hier.

Der Grund für das eigene Modul ist nicht Ordnung, sondern Risiko: Zwei Kopien
einer Verschlüsselung driften auseinander. Eine wird irgendwann verschärft, die
andere nicht, und niemand merkt, welche der beiden die schwächere ist.

## Warum keine Kryptobibliothek

`hashlib` und `hmac` sind in jeder Python-Standardbibliothek und in praktisch
jeder anderen Sprache vorhanden. Ein Bestand, der zwanzig Jahre halten soll,
darf nicht daran scheitern, dass ein Rad in fünf Jahren nicht mehr baut. Das
Format bleibt entzifferbar, auch ohne dieses Programm — mit vierzig Zeilen in
einer beliebigen Sprache.

Der Preis ist ehrlich zu nennen: Ein Schlüsselstrom aus HMAC-SHA256 im
Zählerbetrieb ist kein AES-GCM. Er ist gut untersucht und für diesen Zweck
tragfähig, aber wer eine geprüfte Implementierung braucht, nimmt eine Bibliothek.

## Die Regeln, die eingehalten werden

* **Encrypt-then-MAC.** Der Prüfwert deckt Nonce **und** Chiffrat ab. Erst
  prüfen, dann entschlüsseln — sonst entschlüsselt man Angreiferdaten.
* **Vergleich in konstanter Zeit** (`hmac.compare_digest`).
* **Salt und Nonce je Vorgang neu** aus `os.urandom`.
* **PBKDF2 mit 600 000 Runden** — die OWASP-Empfehlung für PBKDF2-HMAC-SHA256.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from typing import Any

KDF_ITERATIONS = 600_000
KDF_NAME = "pbkdf2-sha256"
CIPHER_NAME = "hmac-sha256-ctr"


class DecryptionError(Exception):
    """Falsche Passphrase oder veränderte Daten. Beides ist nicht unterscheidbar.

    Und das ist Absicht: Eine Meldung, die „Passphrase falsch" von „Datei
    verändert" trennt, verrät einem Angreifer, ob er den richtigen Schlüssel
    rät.
    """


def derive(passphrase: str, salt: bytes, iterations: int = KDF_ITERATIONS) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256", passphrase.encode("utf-8"), salt, iterations, 32
    )


def keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    """Schlüsselstrom aus HMAC-SHA256 im Zählerbetrieb."""
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:length])


def _xor(data: bytes, stream: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(data, stream))


def seal(plain: bytes, passphrase: str, magic: str) -> dict[str, Any]:
    """Verschlüsselt und authentifiziert. Gibt den Umschlag als dict zurück.

    `magic` kennzeichnet, wofür der Umschlag gedacht ist. Ein Export und eine
    Schlüsseldatei sehen sonst gleich aus, und ein verwechseltes Format fällt
    erst beim Entschlüsseln auf — dann aber als Prüfsummenfehler, was in die
    Irre führt.
    """
    salt = os.urandom(16)
    nonce = os.urandom(16)
    key = derive(passphrase, salt)
    cipher = _xor(plain, keystream(key, nonce, len(plain)))
    return {
        "format": magic,
        "kdf": {
            "name": KDF_NAME,
            "iterations": KDF_ITERATIONS,
            "salt": base64.b64encode(salt).decode(),
        },
        "cipher": CIPHER_NAME,
        "nonce": base64.b64encode(nonce).decode(),
        "data": base64.b64encode(cipher).decode(),
        # Encrypt-then-MAC: der Prüfwert deckt Nonce und Chiffrat ab.
        "tag": base64.b64encode(
            hmac.new(key, nonce + cipher, hashlib.sha256).digest()
        ).decode(),
    }


def unseal(envelope: dict[str, Any], passphrase: str) -> bytes:
    """Prüft und entschlüsselt. Wirft `DecryptionError`, wenn etwas nicht stimmt.

    Die Reihenfolge ist die Regel: **erst prüfen, dann entschlüsseln.** Wer
    zuerst entschlüsselt, verarbeitet Daten, die ein Angreifer gewählt hat.
    """
    try:
        salt = base64.b64decode(envelope["kdf"]["salt"])
        iterations = int(envelope["kdf"].get("iterations", KDF_ITERATIONS))
        nonce = base64.b64decode(envelope["nonce"])
        cipher = base64.b64decode(envelope["data"])
        tag = base64.b64decode(envelope["tag"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DecryptionError(f"Unvollständiger Umschlag: {exc}") from exc

    key = derive(passphrase, salt, iterations)
    expected = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise DecryptionError(
            "Prüfsumme stimmt nicht. Falsche Passphrase oder veränderte Datei."
        )
    return _xor(cipher, keystream(key, nonce, len(cipher)))


def seal_json(payload: Any, passphrase: str, magic: str, indent: int | None = 2) -> str:
    plain = json.dumps(payload, ensure_ascii=False, indent=indent).encode("utf-8")
    return json.dumps(seal(plain, passphrase, magic), indent=indent)


def unseal_json(text: str, passphrase: str) -> Any:
    return json.loads(unseal(json.loads(text), passphrase).decode("utf-8"))


__all__ = [
    "CIPHER_NAME",
    "KDF_ITERATIONS",
    "KDF_NAME",
    "DecryptionError",
    "derive",
    "keystream",
    "seal",
    "seal_json",
    "unseal",
    "unseal_json",
]
