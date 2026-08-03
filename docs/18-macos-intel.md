# macOS auf Intel-Prozessoren

## Unterstützter Auslieferungsweg

Icarus erzeugt zwei getrennte native macOS-Artefakte:

- Apple Silicon: `icarus-macos-apple-silicon`
- Intel x86_64: `icarus-macos-intel`

Beide enthalten ein Hauptprogramm und einen PyInstaller-Sidecar derselben Architektur. Ein Universal-Bundle wird erst angeboten, wenn beide Sidecars in einem gemeinsam geprüften Paket korrekt ausgewählt werden können. Bis dahin sind zwei eindeutige Downloads ehrlicher und leichter zu prüfen.

## Intel-Pipeline

`.github/workflows/build-macos-intel.yml` läuft auf dem offiziellen GitHub-Runner `macos-15-intel` und führt aus:

1. Nachweis der x86_64-Runnerarchitektur
2. vollständige Python-Testmatrix
3. PyInstaller-Build des Sidecars
4. Architekturprüfung des Sidecars
5. Start der eingefrorenen Binary
6. vollständiger Backup-/Restore-Roundtrip
7. Tauri-Build für `x86_64-apple-darwin`
8. Architekturprüfung von Hauptprogramm und eingebettetem Sidecar
9. optional Signatur- und Gatekeeperprüfung
10. Upload von `.app` und `.dmg`

## Signatur und Notarisierung

Ohne Apple-Secrets entsteht ein technisch geprüftes, aber ausdrücklich unsigniertes Testartefakt. Für öffentliche Downloads werden dieselben Repository-Secrets wie im Apple-Silicon-Workflow benötigt:

- `APPLE_CERTIFICATE`
- `APPLE_CERTIFICATE_PASSWORD`
- `APPLE_SIGNING_IDENTITY`
- `APPLE_ID`
- `APPLE_TEAM_ID`
- `APPLE_APP_PASSWORD`

Nur der signierte Weg führt `codesign --verify` und `spctl --assess` aus.

## Release-Grenze

Die CI belegt native x86_64-Ausführung auf einem Intel-macOS-Runner. Vor einer breiten öffentlichen Freigabe bleibt zusätzlich ein kurzer manueller Test auf einem Nutzergerät sinnvoll: DMG öffnen, App starten, Onboarding abschließen, Backup erzeugen und Restore durchführen. Dieser Test betrifft vor allem Gatekeeper-, Finder- und Benutzerberechtigungsverhalten, nicht die Prozessorarchitektur.
