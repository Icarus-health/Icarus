"""Startpunkt der eingefrorenen Icarus-Sidecar-Binary.

PyInstaller darf nicht direkt ``icarus_memory/server.py`` als lose Datei
starten. Das Modul verwendet bewusst relative Paketimporte; als einzelnes
Skript fehlt ihm jedoch der Paketkontext und die Binary bricht vor dem Start
mit ``attempted relative import with no known parent package`` ab.

Dieser Launcher bleibt außerhalb des Pakets, importiert den Produktionsstart
absolut und bildet damit denselben Weg ab wie der installierte Konsolenbefehl
``icarus-sidecar``. Dazu gehört insbesondere die Wartungsschranke für
vollständige Sicherung und Wiederherstellung.
"""

from icarus_memory.runtime import main


if __name__ == "__main__":
    main()
