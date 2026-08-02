# PyInstaller-Spec für den Icarus-Sidecar.
#
#   pyinstaller packaging/icarus-sidecar.spec
#
# Erzeugt eine eigenständige Binary in app/src-tauri/binaries/, die Tauri über
# `bundle.resources` mit ausliefert. Ohne diese Datei findet die App keinen
# Sidecar und meldet das beim Start.
#
# Zwei Dinge, die hier oft schiefgehen und deshalb ausdrücklich behandelt sind:
#
# * Der Einstieg darf nicht `icarus_memory/server.py` direkt als loses Skript
#   starten. Das Modul verwendet relative Paketimporte; ohne Paketkontext bricht
#   die eingefrorene Binary bereits beim Import ab. Deshalb der kleine Launcher
#   `icarus_sidecar_entry.py` mit absolutem Import.
# * uvicorn und pydantic laden Teile dynamisch nach. PyInstaller sieht das
#   nicht und lässt sie weg — die Binary baut dann sauber und stürzt beim Start
#   ab. Deshalb die hiddenimports.
# * lancedb und pylance bringen native Erweiterungen mit. Sie sind nur nötig,
#   wenn cognee mit eingebaut wird; ohne bleibt das Bündel deutlich kleiner.

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

# SPECPATH wird von PyInstaller gesetzt. Absolute Pfade machen den Build
# unabhängig davon, ob er aus dem Repository, aus `packaging/` oder durch eine
# CI-Aktion gestartet wird.
REPOSITORY = Path(SPECPATH).resolve().parent.parent
ENTRYPOINT = REPOSITORY / "packaging" / "icarus_sidecar_entry.py"
SIDECAR = REPOSITORY / "sidecar"

# cognee ist optional: ICARUS_BUNDLE_COGNEE=1 nimmt die semantische Suche mit.
# Ohne bleibt der verbindliche Bestand vollständig, nur die Suche fällt auf
# Substringsuche zurück — für ein erstes Release der bessere Kompromiss.
WITH_COGNEE = os.environ.get("ICARUS_BUNDLE_COGNEE") == "1"

hidden = [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
]
hidden += collect_submodules("pydantic")

datas = []
excludes = [
    # Nie gebraucht, kosten aber Platz und Startzeit.
    "tkinter", "matplotlib", "PIL", "IPython", "notebook", "pytest",
]

if WITH_COGNEE:
    from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

    hidden += collect_submodules("cognee") + collect_submodules("lancedb")
    datas += collect_data_files("cognee")
    binaries_extra = collect_dynamic_libs("lancedb") + collect_dynamic_libs("pylance")
else:
    excludes += ["cognee", "lancedb", "pylance", "pyarrow", "litellm"]
    binaries_extra = []


a = Analysis(
    [str(ENTRYPOINT)],
    pathex=[str(SIDECAR)],
    binaries=binaries_extra,
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="icarus-sidecar",
    debug=False,
    bootloader_ignore_signals=False,
    # Nicht strippen: auf macOS beschädigt das die Signatur.
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=os.environ.get("ICARUS_TARGET_ARCH"),
    codesign_identity=None,  # Signiert wird das fertige .app, nicht die Binary.
    entitlements_file=None,
)
