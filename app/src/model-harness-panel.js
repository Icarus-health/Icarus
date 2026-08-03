// Expertenansicht für die Modellsteuerung.
//
// Die Alltagsebene bleibt modellagnostisch. Dieses Panel hängt sich nur in die
// technische Einrichtung ein und verwendet ausschließlich den bestehenden,
// authentifizierten Setup-Endpunkt. Geheimnisse oder Registry-Rohdaten werden
// nicht aus dem Sidecar herausgegeben.

const tauri = window.__TAURI__?.core ?? null;
let connectionPromise = null;
let rendering = false;

async function connectionInfo() {
  if (!connectionPromise) {
    connectionPromise = (async () => {
      if (tauri) {
        const info = await tauri.invoke("sidecar_info");
        return { base: `http://127.0.0.1:${info.port}`, token: info.token };
      }
      return {
        base: window.location.origin,
        token:
          sessionStorage.getItem("icarus-token") ??
          new URLSearchParams(window.location.search).get("token") ??
          "",
      };
    })();
  }
  return connectionPromise;
}

async function setupState() {
  const info = await connectionInfo();
  const response = await fetch(`${info.base}/setup`, {
    headers: {
      "content-type": "application/json",
      "x-icarus-token": info.token,
    },
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

function line(label, value) {
  const row = document.createElement("div");
  const term = document.createElement("dt");
  term.textContent = label;
  const description = document.createElement("dd");
  description.textContent = value;
  row.append(term, description);
  return row;
}

function explanation(mode) {
  const list = document.createElement("ul");
  list.className = "meta";
  const items = mode === "router"
    ? [
        "Aufgaben werden nach Fähigkeit, Datenschutz, Qualität, Latenz und Kosten geroutet.",
        "Geheime Inhalte dürfen nur an lokale Modelle gehen; ein Fallback lockert diese Grenze nie.",
        "Defekte Anbieter werden über einen Circuit Breaker vorübergehend aus der Auswahl genommen.",
        "Kosten- und Tokenbudgets werden vor dem Provideraufruf geprüft.",
      ]
    : [
        "Aktuell ist ein einzelner Anbieter aktiv; Icarus verlangt im Alltag trotzdem keine Modellwahl.",
        "Automatisches Routing wird über ICARUS_MODEL_ROUTES mit einer versionierten Registry aktiviert.",
        "Ein Beispiel liegt unter schema/model-registry.example.json.",
      ];
  for (const text of items) {
    const item = document.createElement("li");
    item.textContent = text;
    list.append(item);
  }
  return list;
}

async function render() {
  if (rendering) return;
  const panel = document.querySelector("#setup-panel");
  if (!panel || panel.querySelector("#model-harness-expert")) return;
  rendering = true;
  try {
    const state = await setupState();
    const provider = state.status?.provider ?? "kein Anbieter";
    const model = state.status?.model ?? "kein Modell";
    const routed = provider === "router";

    const details = document.createElement("details");
    details.id = "model-harness-expert";
    details.className = "setup-block";

    const summary = document.createElement("summary");
    summary.textContent = "Modellsteuerung für Experten";

    const note = document.createElement("p");
    note.className = "muted";
    note.textContent =
      "Diese Ansicht erklärt den technischen Antrieb. Gedächtnis, Identität und persönliche Daten gehören nicht in ein Anbieterprofil.";

    const values = document.createElement("dl");
    values.append(
      line("Betriebsmodus", routed ? "Automatische Registry und Routing" : "Einzelanbieter"),
      line("Aktiver Provider", provider),
      line("Aktives Modell", model),
      line("Lokaler Betrieb", state.settings?.provider === "ollama" ? "ja" : routed ? "abhängig von der Route" : "nein oder nicht belegt"),
      line("Evaluationsbericht", "wird aus versionierten Icarus-Testfällen erzeugt; keine Anbieterbehauptung")
    );

    const audit = document.createElement("p");
    audit.className = "meta";
    audit.textContent =
      "Routingereignisse enthalten Modell-ID, Gründe, Fallbackrang, geschätzte Kosten und Latenz – niemals Gesprächsinhalte.";

    details.append(summary, note, values, explanation(routed), audit);
    panel.append(details);
  } catch (error) {
    const message = document.createElement("p");
    message.id = "model-harness-expert";
    message.className = "meta error";
    message.textContent = `Modellsteuerung konnte nicht angezeigt werden: ${error.message}`;
    panel.append(message);
  } finally {
    rendering = false;
  }
}

new MutationObserver(() => {
  if (!document.querySelector("#view-setup")?.hidden) void render();
}).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["hidden"] });

document.addEventListener("click", (event) => {
  if (event.target.closest('button[data-view="setup"]')) window.setTimeout(render, 0);
});
