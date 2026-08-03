from pathlib import Path


def test_model_harness_expert_view_is_loaded_progressively():
    root = Path(__file__).resolve().parents[2]
    shell = (root / "app" / "src" / "product-shell.js").read_text(encoding="utf-8")
    panel = (root / "app" / "src" / "model-harness-panel.js").read_text(encoding="utf-8")

    assert 'import("./model-harness-panel.js")' in shell
    assert 'details.id = "model-harness-expert"' in panel
    assert 'summary.textContent = "Modellsteuerung für Experten"' in panel
    assert 'provider === "router"' in panel
    assert "ICARUS_MODEL_ROUTES" in panel
    assert "Gesprächsinhalte" in panel


def test_expert_view_uses_existing_authenticated_setup_endpoint_only():
    root = Path(__file__).resolve().parents[2]
    panel = (root / "app" / "src" / "model-harness-panel.js").read_text(encoding="utf-8")

    assert 'fetch(`${info.base}/setup`' in panel
    assert '"x-icarus-token": info.token' in panel
    assert "api_key" not in panel
    assert "mail_password" not in panel
    assert "calendar_password" not in panel
