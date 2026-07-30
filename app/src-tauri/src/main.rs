// Icarus Desktop — Hülle um den Selbstmodell-Sidecar.
//
// Die App startet den Python-Sidecar als Kindprozess, ausschließlich auf
// 127.0.0.1, mit einem bei jedem Start neu erzeugten Token. Das Frontend
// erfährt Port und Token über einen Tauri-Command; niemand sonst auf dem
// Rechner kann den Sidecar ansprechen.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::TcpListener;
use std::process::{Child, Command};
use std::sync::Mutex;

use serde::Serialize;
use tauri::{Manager, RunEvent, State};

/// Verbindungsdaten, die das Frontend braucht, um den Sidecar zu erreichen.
#[derive(Clone, Serialize)]
struct SidecarInfo {
    port: u16,
    token: String,
}

/// Hält den Kindprozess, damit er beim Beenden der App mitgeht.
struct SidecarProcess(Mutex<Option<Child>>);

/// Erzeugt ein Token aus dem Zufallsgenerator des Betriebssystems.
fn generate_token() -> String {
    use std::collections::hash_map::RandomState;
    use std::hash::{BuildHasher, Hasher};

    // Zwei unabhängige Hasher-Seeds ergeben 128 Bit aus der OS-Entropie.
    let a = RandomState::new().build_hasher().finish();
    let b = RandomState::new().build_hasher().finish();
    format!("{a:016x}{b:016x}")
}

/// Fragt das Betriebssystem nach einem freien Port.
fn free_port() -> std::io::Result<u16> {
    let listener = TcpListener::bind("127.0.0.1:0")?;
    let port = listener.local_addr()?.port();
    drop(listener);
    Ok(port)
}

#[tauri::command]
fn sidecar_info(info: State<'_, SidecarInfo>) -> SidecarInfo {
    info.inner().clone()
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            let port = free_port()?;
            let token = generate_token();

            // Nutzerdaten liegen im plattformüblichen App-Verzeichnis, damit das
            // Selbstmodell ein Update der App überlebt.
            let data_dir = app.path().app_data_dir()?;
            std::fs::create_dir_all(&data_dir)?;

            // Im Release liegt der gebündelte Sidecar neben der Binary; in der
            // Entwicklung wird der auf dem PATH installierte genommen.
            let sidecar = app
                .path()
                .resolve("icarus-sidecar", tauri::path::BaseDirectory::Resource)
                .unwrap_or_else(|_| "icarus-sidecar".into());

            let child = Command::new(sidecar)
                .env("ICARUS_SIDECAR_PORT", port.to_string())
                .env("ICARUS_SIDECAR_TOKEN", &token)
                .env("ICARUS_DATA_DIR", &data_dir)
                .spawn()?;

            app.manage(SidecarInfo { port, token });
            app.manage(SidecarProcess(Mutex::new(Some(child))));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![sidecar_info])
        .build(tauri::generate_context!())
        .expect("Icarus konnte nicht gestartet werden")
        .run(|app, event| {
            // Ohne das überlebt der Sidecar das Fenster und hält die Datenbank offen.
            if let RunEvent::ExitRequested { .. } = event {
                if let Some(state) = app.try_state::<SidecarProcess>() {
                    if let Ok(mut guard) = state.0.lock() {
                        if let Some(mut child) = guard.take() {
                            let _ = child.kill();
                            let _ = child.wait();
                        }
                    }
                }
            }
        });
}
