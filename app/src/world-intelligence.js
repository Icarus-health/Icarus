// Sichere Außenwelt-Quellen für die Systemzentrale.
//
// Externe Inhalte bleiben Rohmaterial. Diese Ansicht zeigt Quelle, Zeitpunkt,
// Relevanz und Originaladresse, übernimmt aber nichts automatisch ins Gedächtnis.

const tauri = window.__TAURI__?.core ?? null;
let connectionPromise = null;
let mounted = false;
let refreshing = false;

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

async function api(path, options = {}) {
  const info = await connectionInfo();
  const response = await fetch(`${info.base}${path}`, {
    ...options,
    headers: {
      "content-type": "application/json",
      "x-icarus-token": info.token,
      ...(options.headers ?? {}),
    },
  });
  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;
  if (!response.ok) throw new Error(payload?.detail ?? `HTTP ${response.status}`);
  return payload;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function action(label, handler, className = "") {
  const node = element("button", className, label);
  node.type = "button";
  node.addEventListener("click", handler);
  return node;
}

function formatDate(value) {
  if (!value) return "Noch nicht geprüft";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? String(value)
    : new Intl.DateTimeFormat("de-DE", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(date);
}

function ensurePanel() {
  if (mounted) return true;
  const layout = document.querySelector("#view-system .system-layout");
  if (!layout) return false;
  const panel = element("section", "system-panel world-panel");
  panel.id = "world-panel";
  panel.innerHTML = `
    <div class="system-panel-head">
      <div>
        <h3>Außenwelt</h3>
        <p class="muted">Belegte RSS-/Atom-Quellen. Inhalte bleiben fremde Daten.</p>
      </div>
      <button id="world-refresh-all" type="button" class="ghost small">Alle prüfen</button>
    </div>
    <p id="world-feedback" class="muted" role="status"></p>
    <details class="world-add-source">
      <summary>Quelle hinzufügen</summary>
      <form id="world-source-form">
        <input id="world-source-name" type="text" placeholder="Name der Quelle" required maxlength="200" />
        <input id="world-source-url" type="url" placeholder="https://…/feed.xml" required />
        <select id="world-source-project" aria-label="Optionales Projekt">
          <option value="">Automatisch zuordnen</option>
        </select>
        <button type="submit">Hinzufügen</button>
      </form>
      <p class="meta">Icarus prüft private Netzwerke und Weiterleitungen. Eine Quelle schreibt niemals direkt ins Gedächtnis.</p>
    </details>
    <div class="world-columns">
      <div>
        <h4>Quellen</h4>
        <ul id="world-source-list" class="system-list"></ul>
      </div>
      <div>
        <div class="world-items-head">
          <h4>Neue relevante Änderungen</h4>
          <button id="world-show-all" type="button" class="ghost small">Alle zeigen</button>
        </div>
        <ul id="world-item-list" class="system-list"></ul>
      </div>
    </div>
  `;
  layout.append(panel);
  mounted = true;

  panel.querySelector("#world-refresh-all")?.addEventListener("click", refreshAll);
  panel.querySelector("#world-show-all")?.addEventListener("click", () => renderItems(false));
  panel.querySelector("#world-source-form")?.addEventListener("submit", addSource);
  void refreshWorld();
  return true;
}

function feedback(text, isError = false) {
  const node = document.querySelector("#world-feedback");
  if (!node) return;
  node.textContent = text;
  node.classList.toggle("error", isError);
}

async function projectOptions() {
  const select = document.querySelector("#world-source-project");
  if (!select || select.options.length > 1) return;
  try {
    const projects = await api("/projects");
    for (const project of projects) {
      const option = document.createElement("option");
      option.value = project.id;
      option.textContent = project.name;
      select.append(option);
    }
  } catch {
    // Die Quelle kann weiterhin ohne feste Projektbindung angelegt werden.
  }
}

async function addSource(event) {
  event.preventDefault();
  const name = document.querySelector("#world-source-name")?.value.trim();
  const url = document.querySelector("#world-source-url")?.value.trim();
  const projectId = document.querySelector("#world-source-project")?.value || null;
  if (!name || !url) return;
  feedback("Quelle wird geprüft und hinzugefügt …");
  try {
    const source = await api("/world/sources", {
      method: "POST",
      body: JSON.stringify({ name, url, project_id: projectId }),
    });
    event.target.reset();
    await renderSources();
    feedback(`Quelle „${source.name}“ wurde hinzugefügt. Der erste Abruf startet nur auf ausdrücklichen Klick.`);
  } catch (error) {
    feedback(`Quelle konnte nicht hinzugefügt werden: ${error.message}`, true);
  }
}

async function deleteSource(source) {
  if (!window.confirm(`Quelle „${source.name}“ entfernen? Bereits aufgenommene Episoden bleiben als belegtes Rohmaterial erhalten.`)) return;
  try {
    await api(`/world/sources/${encodeURIComponent(source.id)}`, { method: "DELETE" });
    await refreshWorld();
    feedback(`Quelle „${source.name}“ wurde entfernt.`);
  } catch (error) {
    feedback(`Quelle konnte nicht entfernt werden: ${error.message}`, true);
  }
}

async function refreshSource(source) {
  feedback(`„${source.name}“ wird geprüft …`);
  try {
    const report = await api(`/world/sources/${encodeURIComponent(source.id)}/refresh`, { method: "POST" });
    await refreshWorld();
    feedback(report.unchanged
      ? `„${source.name}“ ist unverändert.`
      : `„${source.name}“: ${report.new} neue, ${report.relevant} relevante Einträge.`);
  } catch (error) {
    await renderSources();
    feedback(`„${source.name}“ konnte nicht gelesen werden: ${error.message}`, true);
  }
}

async function refreshAll() {
  if (refreshing) return;
  refreshing = true;
  feedback("Alle aktiven Quellen werden nacheinander geprüft …");
  try {
    const reports = await api("/world/refresh", { method: "POST" });
    const successes = reports.filter((report) => report.ok).length;
    const failures = reports.length - successes;
    await refreshWorld();
    feedback(`${successes} Quellen geprüft${failures ? ` · ${failures} Fehler` : ""}.`, failures > 0);
  } catch (error) {
    feedback(`Quellen konnten nicht geprüft werden: ${error.message}`, true);
  } finally {
    refreshing = false;
  }
}

async function renderSources() {
  const root = document.querySelector("#world-source-list");
  if (!root) return;
  try {
    const sources = await api("/world/sources");
    root.replaceChildren();
    if (!sources.length) {
      root.append(element("li", "muted", "Noch keine Außenwelt-Quelle eingerichtet."));
      return;
    }
    for (const source of sources) {
      const item = element("li", "world-source-item");
      const head = element("div", "world-source-head");
      head.append(element("strong", "", source.name));
      const status = element(
        "p",
        source.last_error ? "meta error" : "meta",
        source.last_error || `Zuletzt geprüft: ${formatDate(source.last_checked_at)}`
      );
      const address = element("code", "world-address", source.url);
      const actions = element("div", "row world-actions");
      actions.append(
        action("Prüfen", () => refreshSource(source), "ghost small"),
        action("Entfernen", () => deleteSource(source), "ghost small")
      );
      item.append(head, status, address, actions);
      root.append(item);
    }
  } catch (error) {
    root.replaceChildren(element("li", "error", `Quellen konnten nicht geladen werden: ${error.message}`));
  }
}

async function markSeen(item) {
  try {
    await api(`/world/items/${encodeURIComponent(item.id)}/seen`, { method: "POST" });
    await renderItems(true);
  } catch (error) {
    feedback(`Eintrag konnte nicht als gesehen markiert werden: ${error.message}`, true);
  }
}

async function renderItems(newOnly = true) {
  const root = document.querySelector("#world-item-list");
  if (!root) return;
  const query = new URLSearchParams({
    limit: "50",
    new_only: String(newOnly),
    relevant_only: String(newOnly),
  });
  try {
    const items = await api(`/world/items?${query}`);
    root.replaceChildren();
    if (!items.length) {
      root.append(element("li", "muted", newOnly
        ? "Keine neue relevante Außenwelt-Änderung."
        : "Noch keine Einträge aus Außenwelt-Quellen."));
      return;
    }
    for (const item of items) {
      const row = element("li", `world-item ${item.is_new ? "is-new" : ""}`.trim());
      const head = element("div", "world-item-head");
      head.append(
        element("strong", "", item.title),
        item.is_new ? element("span", "world-new", "Neu") : document.createTextNode("")
      );
      const meta = element(
        "p",
        "meta",
        [item.source_name, formatDate(item.published_at || item.first_seen_at), item.project_id ? "Projektbezug" : null]
          .filter(Boolean)
          .join(" · ")
      );
      const summary = element("p", "world-summary", item.summary || "Keine Zusammenfassung geliefert.");
      const terms = item.matched_terms?.length
        ? element("p", "meta", `Passende Begriffe: ${item.matched_terms.join(", ")}`)
        : null;
      const address = element("code", "world-address", item.url);
      const actions = element("div", "row world-actions");
      actions.append(action("Im Rohmaterial öffnen", () => document.querySelector('button[data-view="ingest"]')?.click(), "ghost small"));
      if (item.is_new) actions.append(action("Gesehen", () => markSeen(item), "ghost small"));
      row.append(head, meta, summary);
      if (terms) row.append(terms);
      row.append(address, actions);
      root.append(row);
    }
  } catch (error) {
    root.replaceChildren(element("li", "error", `Außenwelt-Einträge konnten nicht geladen werden: ${error.message}`));
  }
}

async function refreshWorld() {
  if (!ensurePanel()) return;
  await projectOptions();
  await Promise.all([renderSources(), renderItems(true)]);
}

const stylesheet = document.createElement("link");
stylesheet.rel = "stylesheet";
stylesheet.href = "world-intelligence.css";
document.head.append(stylesheet);

const observer = new MutationObserver(() => {
  if (ensurePanel()) observer.disconnect();
});
observer.observe(document.body, { childList: true, subtree: true });
ensurePanel();

document.addEventListener("click", (event) => {
  if (event.target.closest('button[data-view="system"]')) {
    window.setTimeout(refreshWorld, 0);
  }
});
