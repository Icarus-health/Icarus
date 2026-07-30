# Gebündelter Sidecar

Hier landet die gebaute Sidecar-Binary `icarus-sidecar`. Sie wird **nicht**
eingecheckt — sie ist ein Build-Artefakt von rund einem Gigabyte.

Erzeugen mit:

```bash
make sidecar-binary
```

Das Verzeichnis bleibt versioniert (über diese Datei), damit `cargo check` und
`cargo build` in einem frischen Clone durchlaufen: `tauri.conf.json` verweist
über `binaries/*` hierher, und ein fehlendes Verzeichnis würde den Build-Schritt
abbrechen.

Ohne gebaute Binary startet die App zwar, findet aber keinen Sidecar und meldet
"Der Sidecar antwortet nicht." Für die Entwicklung genügt stattdessen ein auf
dem PATH installierter `icarus-sidecar` (`make sidecar-dev`) — die App fällt
darauf zurück.
