// Minimales Frontend. Bewusst ohne Framework — das Gerüst soll zeigen, wie die
// App an den Sidecar kommt, nicht wie man eine Oberfläche baut.

const { invoke } = window.__TAURI__.core;

let base = null;
let token = null;

const statusEl = document.querySelector("#status");
const listEl = document.querySelector("#assertions");
const emptyEl = document.querySelector("#empty");

async function api(path, options = {}) {
  const response = await fetch(`${base}${path}`, {
    ...options,
    headers: {
      "content-type": "application/json",
      "x-icarus-token": token,
      ...(options.headers ?? {}),
    },
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail ?? `HTTP ${response.status}`);
  }
  return response.status === 204 ? null : response.json();
}

/** Herkunft in Klartext — der Nutzer soll nicht Feldnamen lesen müssen. */
const SOURCE_LABELS = {
  user_stated: "von dir gesagt",
  chat: "aus einem Gespräch",
  email: "aus einer E-Mail",
  calendar: "aus dem Kalender",
  document: "aus einem Dokument",
  web: "aus dem Web",
  tool_output: "von einem Werkzeug",
  inference: "selbst gefolgert",
  manual_correction: "manuell korrigiert",
};

function render(assertions) {
  listEl.replaceChildren();
  emptyEl.hidden = assertions.length > 0;

  for (const a of assertions) {
    const li = document.createElement("li");

    const text = document.createElement("p");
    text.className = "statement";
    text.textContent = a.statement;

    const meta = document.createElement("p");
    meta.className = "meta";
    const origin = SOURCE_LABELS[a.provenance.source_type] ?? a.provenance.source_type;
    const recorded = new Date(a.recorded_at).toLocaleDateString("de-DE");
    meta.textContent = `${origin} · ${recorded}`;
    if (a.provenance.source_type === "inference") {
      meta.classList.add("inferred");
    }

    const forget = document.createElement("button");
    forget.className = "forget";
    forget.textContent = "Vergessen";
    // Der Widerruf nimmt alles mit, was aus dieser Aussage gefolgert wurde.
    forget.addEventListener("click", async () => {
      const affected = await api(`/assertions/${a.id}/redact`, {
        method: "POST",
        body: JSON.stringify({ reason: "user_request" }),
      });
      if (affected.length > 1) {
        statusEl.textContent =
          `Vergessen — samt ${affected.length - 1} daraus abgeleiteten Aussagen.`;
      }
      await refresh();
    });

    li.append(text, meta, forget);
    listEl.append(li);
  }
}

async function refresh() {
  render(await api("/assertions"));
}

document.querySelector("#record-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = document.querySelector("#statement");
  const statement = input.value.trim();
  if (!statement) return;

  await api("/assertions", {
    method: "POST",
    body: JSON.stringify({
      statement,
      kind: document.querySelector("#kind").value,
      provenance: { source_type: "user_stated", captured_at: new Date().toISOString() },
      confidence: 1.0,
    }),
  });
  input.value = "";
  await refresh();
});

async function start() {
  const info = await invoke("sidecar_info");
  base = `http://127.0.0.1:${info.port}`;
  token = info.token;

  const health = await fetch(`${base}/health`).then((r) => r.json());
  statusEl.textContent = health.semantic_search
    ? "Bereit — semantische Suche aktiv."
    : "Bereit — semantische Suche aus, Bestand vollständig.";
  statusEl.classList.add("ready");

  await refresh();
}

// Der Sidecar braucht einen Moment zum Hochfahren.
async function startWithRetry(attempts = 20) {
  for (let i = 0; i < attempts; i += 1) {
    try {
      await start();
      return;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  }
  statusEl.textContent = "Der Sidecar antwortet nicht.";
  statusEl.classList.add("error");
}

startWithRetry();
