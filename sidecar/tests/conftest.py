"""Sichere Standardumgebung für die gesamte Sidecar-Testsuite.

Tests dürfen niemals zufällig die persönlichen Zugangsdaten oder den
Betriebssystem-Schlüsselbund des Rechners verwenden, auf dem sie laufen. Die
Umgebung wird deshalb pro Test zurückgesetzt; gezielte Integrationsläufe müssen
diese Sperre ausdrücklich außerhalb des normalen ``pytest``-Ziels aufheben.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from icarus_memory import providers
from icarus_memory.secrets import BACKEND_ENV, KNOWN


# Alles, was Konnektoren, Modelle, Browser oder den lokalen Server von außen
# konfigurieren kann. KNOWN enthält die schutzwürdigen Geheimnisse zentral.
ISOLATED_ENVIRONMENT = (
    *KNOWN,
    "ICARUS_BASE_URL",
    "ICARUS_BROWSER_NODE",
    "ICARUS_BROWSER_WORKER",
    "ICARUS_CALDAV_URL",
    "ICARUS_CALDAV_USER",
    "ICARUS_DATA_DIR",
    "ICARUS_FILE_ROOTS",
    "ICARUS_IMAP_HOST",
    "ICARUS_IMAP_PORT",
    BACKEND_ENV,
    "ICARUS_MAIL_FROM",
    "ICARUS_MAIL_USER",
    "ICARUS_MODEL",
    "ICARUS_MODEL_MAX_COST",
    "ICARUS_MODEL_MAX_INPUT_TOKENS",
    "ICARUS_MODEL_MAX_OUTPUT_TOKENS",
    "ICARUS_MODEL_ROUTES",
    "ICARUS_PROVIDER",
    "ICARUS_SECRETS_PASSPHRASE",
    "ICARUS_SIDECAR_HOST",
    "ICARUS_SIDECAR_PORT",
    "ICARUS_SIDECAR_TOKEN",
    "ICARUS_SIDECAR_URL",
    "ICARUS_SMTP_HOST",
    "ICARUS_SMTP_PORT",
    "ICARUS_UI_DIR",
)


class _BlockedProviderTransport:
    """Ersetzt den HTTP-Transport nur in Tests und löst keine Verbindung aus."""

    def __enter__(self) -> _BlockedProviderTransport:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def post(self, *_: object, **__: object) -> object:
        raise AssertionError(
            "Provider-Netzwerk ist in Standardtests gesperrt. "
            "Nutze einen Stub oder einen ausdrücklichen Integrationslauf."
        )


def _blocked_provider_http(*_: object, **__: object) -> _BlockedProviderTransport:
    return _BlockedProviderTransport()


@pytest.fixture(autouse=True)
def isolated_test_environment(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[None]:
    """Macht jeden Standardtest lokal, reproduzierbar und rückstandsfrei.

    ``monkeypatch`` stellt sämtliche geerbten Werte nach dem Test automatisch
    wieder her. Einzelne Tests dürfen nur für ihren eigenen Ablauf explizit
    synthetische Werte setzen.
    """
    for name in ISOLATED_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(BACKEND_ENV, "none")
    monkeypatch.setenv("ICARUS_DATA_DIR", str(tmp_path / "icarus-test-data"))
    monkeypatch.setattr(providers, "_http", _blocked_provider_http)
    yield
