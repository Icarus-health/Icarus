// Verbraucherfreundliche Systemzentrale für die Icarus-Private-Beta.
//
// Sie zeigt keine Entwicklerlogs, sondern beantwortet: Was läuft? Was weiß
// Icarus? Was wartet? Was ist verbunden? Sensible Zusammenhänge bleiben
// standardmäßig verborgen und werden nur nach einer bewussten Umschaltung
// abgefragt.

const tauri = window.__TAURI__?.core ?? null;
let connectionPromise = null;
let systemButton = null;
let systemView = null;
let sensitiveVisible = false;
let selectedEntity = null;

const typeLabels = {
  person: "Person",
  organisation: "Organisation",
  role: "Rolle",
  project: "Projekt",
  goal: "Ziel",
  decision: "Entscheidung",
  event: "Ereignis",
  place: "Ort",
};

const stateLabels = {
  pending: "Bereit",
  running: "Läuft",
  waiting_time: "Wartet auf einen Zeitpunkt",
  waiting_condition: "Wartet auf eine Bedingung",
  waiting_approval: "Wartet auf deine Freigabe",
  needs_reconciliation: "Muss geklärt werden",
  completed: "Abgeschlossen",
  failed: "Fehlgeschlagen",
  cancelled: "Abgebrochen",
};

const relationLabels = {
  has_deadline: "hat Frist",
  has_next_step: "hat nächsten Schritt",
  has_decision: "enthält Entscheidung",
  participated_in: "war beteiligt an",
};

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
  if (!response.ok) {
    throw new Error(payload?.detail ?? `HTTP ${response.status}`);
  }
  return payload;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function button(label, handler, className = "") {
  const node = element("button", className, label);
  node.type = "button";
  node.addEventListener("click", handler);
  return node;
}

function empty(text) {
  return element("p", "muted system-empty", text);
}

function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? String(value)
    : new Intl.DateTimeFormat("de-DE", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(date);
}

function showSystem() {
  document.querySelectorAll(".view").forEach((view) => {
    view.hidden = view !== systemView;
  });
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("active", tab === systemButton);
  });
  document.querySelector(".nav-more")?.removeAttribute("open");
  void renderSystem();
}

function ensureShell() {
  if (systemView) return true;
  const menu = document.querySelector(".nav-more-menu");
  const main = document.querySelector("main");
  if (!menu || !main) return false;

  systemButton = element("button", "tab", "System");
  systemButton.type = "button";
  systemButton.dataset.view = "system";
  systemButton.addEventListener("click", showSystem);
  menu.append(systemButton);

  systemView = element("section", "view system-view");
  systemView.id = "view-system";
  systemView.hidden = true;
  systemView.innerHTML = `
    <div class="system-head">
      <div>
        <h2>Dein Icarus-System</h2>
        <p class="muted">Zusammenhänge, Automationen und Verbindungen an einem Ort.</p>
      </div>
      <button id="system-refresh" type="button" class="ghost">Aktualisieren</button>
    </div>
    <p id="system-feedback" class="muted" role="status"></p>
    <div id="system-overview" class="system-overview"></div>
    <div class="system-layout">
      <section class="system-panel system-knowledge">
        <div class="system-panel-head">
          <div><h3>Zusammenhänge</h3><p class="muted">Was miteinander verbunden ist – mit Quellen.</p></div>
          <button id="graph-rebuild" type="button" class="ghost small">Neu aufbauen</button>
        </div>
        <form id="graph-search-form" class="system-search">
          <input id="graph-search" type="search" placeholder="Person, Projekt, Ziel …" autocomplete="off" />
          <select id="graph-type" aria-label="Art des Zusammenhangs">
            <option value="">Alle Arten</option>
            <option value="person">Personen</option>
            <option value="organisation">Organisationen</option>
            <option value="project">Projekte</option>
            <option value="goal">Ziele</option>
            <option value="decision">Entscheidungen</option>
            <option value="event">Ereignisse</option>
            <option value="role">Rollen</option>
            <option value="place">Orte</option>
          </select>
          <button type="submit">Suchen</button>
        </form>
        <details class="system-privacy">
          <summary>Datenschutz und sensible Zusammenhänge</summary>
          <label><input id="graph-sensitive" type="checkbox" /> Sensible Zusammenhänge in dieser Ansicht anzeigen</label>
          <p class="meta">Standardmäßig erscheinen nur Zusammenhänge aus normalen Quellen. Diese Einstellung gilt nur für die aktuelle Sitzung.</p>
        </details>
        <div class="system-graph-split">
          <ul id="graph-results" class="system-list"></ul>
          <div id="graph-detail" class="system-detail"><p class="muted">Wähle einen Zusammenhang.</p></div>
        </div>
      </section>
      <section class="system-panel system-automations">
        <div class="system-panel-head">
          <div><h3>Automationen</h3><p class="muted">Was Icarus gerade verfolgt oder vorbereitet.</p></div>
          <button id="workflow-run-ready" type="button" class="ghost small">Bereite Schritte vor</button>
        </div>
        <ul id="workflow-list" class="system-list"></ul>
      </section>
      <section class="system-panel system-connections">
        <h3>Modelle und Verbindungen</h3>
        <div id="connection-status"></div>
      </section>
      <section class="system-panel system-conflicts">
        <h3>Zu klärende Identitäten</h3>
        <p class="muted">Icarus führt ähnliche Namen nicht still zusammen.</p>
        <ul id="graph-conflicts" class="system-list"></ul>
      </section>
    </div>
  `;
  main.append(systemView);

  document.querySelector("#system-refresh")?.addEventListener("click", renderSystem);
  document.querySelector("#graph-rebuild")?.addEventListener("click", rebuildGraph);
  document.querySelector("#workflow-run-ready")?.addEventListener("click", runReady);
  document.querySelector("#graph-search-form")?.addEventListener("submit", (event) => {
    event.preventDefault();
    void renderEntities();
  });
  document.querySelector("#graph-sensitive")?.addEventListener("change", (event) => {
    sensitiveVisible = event.target.checked;
    selectedEntity = null;
    void renderEntities();
  });
  return true;
}

function feedback(text, isError = false) {
  const node = document.querySelector("#system-feedback");
  if (!node) return;
  node.textContent = text;
  node.classList.toggle("error", isError);
}

function overviewCard(label, value, detail, tone = "") {
  const card = element("article", `system-stat ${tone}`.trim());
  card.append(
    element("p", "meta", label),
    element("strong", "system-stat-value", String(value)),
    element("p", "muted", detail)
  );
  return card;
}

function renderOverview(status) {
  const root = document.querySelector("#system-overview");
  if (!root) return;
  root.replaceChildren(
    overviewCard(
      "Zusammenhänge",
      status.graph.entities,
      `${status.graph.edges} Beziehungen mit ${status.graph.sources} Quellen`
    ),
    overviewCard(
      "Automationen",
      status.workflows.total,
      status.workflows.total ? "Laufzeit ist aktiv" : "Noch keine laufende Automation"
    ),
    overviewCard(
      "Modellsteuerung",
      status.model_harness.active ? "Automatisch" : "Einzelmodell",
      status.model_harness.model || "Kein Modell verbunden"
    ),
    overviewCard(
      "Browser",
      status.browser.active ? "Aktiv" : "Nicht aktiv",
      status.browser.active ? "Policy-gebunden und getrennt" : "Kann später ergänzt werden",
      status.browser.active ? "ready" : ""
    )
  );
}

async function renderEntities() {
  const list = document.querySelector("#graph-results");
  const detail = document.querySelector("#graph-detail");
  if (!list || !detail) return;
  list.replaceChildren(empty("Suche läuft …"));
  const query = document.querySelector("#graph-search")?.value.trim() ?? "";
  const type = document.querySelector("#graph-type")?.value ?? "";
  const params = new URLSearchParams({
    query,
    entity_type: type,
    limit: "60",
    include_sensitive: String(sensitiveVisible),
  });
  try {
    const entities = await api(`/graph/entities?${params}`);
    list.replaceChildren();
    if (!entities.length) {
      list.append(empty("Keine passenden Zusammenhänge."));
      detail.replaceChildren(empty("Wähle einen anderen Suchbegriff."));
      return;
    }
    for (const entity of entities) {
      const item = element("li", "system-entity-item");
      const open = button(entity.canonical_name, () => selectEntity(entity.id), "system-entity-button");
      const meta = element(
        "span",
        "meta",
        `${typeLabels[entity.entity_type] ?? entity.entity_type} · ${entity.relation_count} Beziehungen · ${entity.source_count} Quellen`
      );
      item.append(open, meta);
      list.append(item);
    }
    if (selectedEntity && entities.some((entity) => entity.id === selectedEntity)) {
      await selectEntity(selectedEntity);
    }
  } catch (error) {
    list.replaceChildren(empty(`Zusammenhänge konnten nicht geladen werden: ${error.message}`));
  }
}

function relationText(edge, entityId) {
  const outgoing = edge.source_id === entityId;
  const other = outgoing ? edge.target_name : edge.source_name;
  const relation = relationLabels[edge.predicate] ?? edge.predicate.replaceAll("_", " ");
  return outgoing ? `${relation}: ${other}` : `${other}: ${relation}`;
}

async function selectEntity(entityId) {
  selectedEntity = entityId;
  const detail = document.querySelector("#graph-detail");
  if (!detail) return;
  detail.replaceChildren(empty("Zusammenhang wird geladen …"));
  const suffix = `include_sensitive=${String(sensitiveVisible)}`;
  try {
    const [entity, neighbors, timeline] = await Promise.all([
      api(`/graph/entities/${encodeURIComponent(entityId)}?${suffix}`),
      api(`/graph/entities/${encodeURIComponent(entityId)}/neighbors?${suffix}`),
      api(`/graph/entities/${encodeURIComponent(entityId)}/timeline?${suffix}`),
    ]);
    const title = element("h4", "", entity.canonical_name);
    const meta = element("p", "muted", typeLabels[entity.entity_type] ?? entity.entity_type);
    const relationTitle = element("h5", "", "Verbindungen");
    const relationList = element("ul", "system-detail-list");
    if (!neighbors.length) relationList.append(empty("Keine sichtbaren Beziehungen."));
    for (const edge of neighbors) {
      const item = element("li", "");
      item.append(
        element("strong", "", relationText(edge, entityId)),
        element(
          "p",
          "meta",
          `${edge.status === "disputed" ? "Strittig · " : ""}${edge.sources.length} Quellen`
        )
      );
      if (edge.attributes && Object.keys(edge.attributes).length) {
        const facts = element("p", "meta", Object.entries(edge.attributes)
          .filter(([, value]) => value !== null && value !== "")
          .map(([key, value]) => `${key}: ${value}`)
          .join(" · "));
        if (facts.textContent) item.append(facts);
      }
      relationList.append(item);
    }
    const timelineTitle = element("h5", "", "Zeitverlauf");
    const timelineList = element("ul", "system-detail-list compact");
    const dated = timeline.filter((edge) => edge.valid_from || edge.valid_until);
    if (!dated.length) timelineList.append(empty("Kein Zeitbezug hinterlegt."));
    for (const edge of dated) {
      timelineList.append(
        element(
          "li",
          "",
          `${formatDate(edge.valid_from || edge.valid_until)} · ${relationText(edge, entityId)}`
        )
      );
    }
    detail.replaceChildren(title, meta, relationTitle, relationList, timelineTitle, timelineList);
  } catch (error) {
    detail.replaceChildren(empty(`Zusammenhang konnte nicht geladen werden: ${error.message}`));
  }
}

async function rebuildGraph() {
  feedback("Zusammenhänge werden aus den bestätigten Daten neu aufgebaut …");
  try {
    await api("/graph/rebuild", { method: "POST" });
    selectedEntity = null;
    await Promise.all([renderSystem(), renderEntities()]);
    feedback("Zusammenhänge wurden neu aufgebaut.");
  } catch (error) {
    feedback(`Neuaufbau fehlgeschlagen: ${error.message}`, true);
  }
}

async function workflowAction(path, body = undefined) {
  try {
    await api(path, {
      method: "POST",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    await renderWorkflows();
  } catch (error) {
    feedback(`Automation konnte nicht aktualisiert werden: ${error.message}`, true);
  }
}

function terminal(state) {
  return ["completed", "cancelled"].includes(state);
}

async function reconcileWorkflow(workflow, executed) {
  try {
    const detail = await api(`/workflows/${encodeURIComponent(workflow.id)}`);
    const step = detail.steps.find((item) => item.state === "needs_reconciliation");
    if (!step) throw new Error("Kein unklarer Schritt gefunden");
    await workflowAction(`/workflows/${encodeURIComponent(workflow.id)}/reconcile`, {
      step_id: step.id,
      executed,
      result: {
        ok: true,
        text: executed
          ? "Vom Nutzer als bereits ausgeführt bestätigt."
          : "Vom Nutzer als nicht ausgeführt bestätigt.",
      },
    });
  } catch (error) {
    feedback(`Klärung fehlgeschlagen: ${error.message}`, true);
  }
}

async function renderWorkflows() {
  const root = document.querySelector("#workflow-list");
  if (!root) return;
  try {
    const workflows = await api("/workflows");
    root.replaceChildren();
    if (!workflows.length) {
      root.append(empty("Noch keine Automationen. Icarus führt nichts unsichtbar im Hintergrund aus."));
      return;
    }
    for (const workflow of workflows) {
      const item = element("li", "system-workflow");
      const head = element("div", "system-workflow-head");
      head.append(
        element("strong", "", workflow.name),
        element("span", `system-state state-${workflow.state}`, stateLabels[workflow.state] ?? workflow.state)
      );
      const actions = element("div", "row system-actions");
      if (!terminal(workflow.state)) {
        actions.append(
          button("Jetzt prüfen", () => workflowAction(`/workflows/${encodeURIComponent(workflow.id)}/tick`), "ghost small"),
          button("Abbrechen", () => workflowAction(`/workflows/${encodeURIComponent(workflow.id)}/cancel`), "ghost small")
        );
      }
      if (workflow.state === "waiting_approval") {
        actions.append(button("Freigabe öffnen", () => document.querySelector('button[data-view="chat"]')?.click(), "small"));
      }
      if (workflow.state === "needs_reconciliation") {
        actions.append(
          button("Wurde ausgeführt", () => reconcileWorkflow(workflow, true), "small"),
          button("Wurde nicht ausgeführt", () => reconcileWorkflow(workflow, false), "ghost small")
        );
      }
      const note = element(
        "p",
        "meta",
        workflow.next_run_at ? `Nächste Prüfung: ${formatDate(workflow.next_run_at)}` : `Zuletzt geändert: ${formatDate(workflow.updated_at)}`
      );
      item.append(head, note, actions);
      root.append(item);
    }
  } catch (error) {
    root.replaceChildren(empty(`Automationen konnten nicht geladen werden: ${error.message}`));
  }
}

async function runReady() {
  feedback("Bereite fällige Schritte vor …");
  try {
    const results = await api("/workflows/run-ready", { method: "POST" });
    await renderWorkflows();
    feedback(results.length ? `${results.length} Automation(en) wurden geprüft.` : "Aktuell ist kein Schritt fällig.");
  } catch (error) {
    feedback(`Automationen konnten nicht geprüft werden: ${error.message}`, true);
  }
}

function renderConnections(status, connectors) {
  const root = document.querySelector("#connection-status");
  if (!root) return;
  root.replaceChildren();
  const model = element("article", "system-connection");
  model.append(
    element("strong", "", "KI-Antrieb"),
    element(
      "p",
      "muted",
      status.model_harness.active
        ? `Icarus wählt passend zur Aufgabe. Aktiv: ${status.model_harness.model || "automatisch"}.`
        : status.model_harness.model
          ? `Aktuell verbunden: ${status.model_harness.model}.`
          : "Kein Modell verbunden. Gedächtnis und Organisation bleiben nutzbar."
    )
  );
  root.append(model);

  if (!connectors.length) {
    const connection = element("article", "system-connection");
    connection.append(
      element("strong", "", "Externe Steuerung"),
      element("p", "muted", status.browser.detail || "Noch kein Connector aktiv.")
    );
    root.append(connection);
    return;
  }
  for (const connector of connectors) {
    const connection = element("article", "system-connection");
    connection.append(
      element("strong", "", connector.name),
      element("p", "muted", `${connector.operations.length} kontrollierte Fähigkeiten · Version ${connector.version}`)
    );
    root.append(connection);
  }
}

async function renderConflicts() {
  const root = document.querySelector("#graph-conflicts");
  if (!root) return;
  try {
    const conflicts = await api(`/graph/conflicts?include_sensitive=${String(sensitiveVisible)}`);
    root.replaceChildren();
    if (!conflicts.length) {
      root.append(empty("Keine Identität muss geklärt werden."));
      return;
    }
    for (const conflict of conflicts) {
      const item = element("li", "");
      item.append(
        element("strong", "", conflict.normalized_alias),
        element("p", "meta", `${conflict.candidate_ids.length} mögliche Zuordnungen · ${conflict.reason}`)
      );
      root.append(item);
    }
  } catch (error) {
    root.replaceChildren(empty(`Konflikte konnten nicht geladen werden: ${error.message}`));
  }
}

async function renderSystem() {
  if (!ensureShell()) return;
  feedback("Systemzustand wird geladen …");
  try {
    const [status, connectors] = await Promise.all([
      api("/private-beta/status"),
      api("/connectors"),
    ]);
    renderOverview(status);
    renderConnections(status, connectors);
    await Promise.all([renderEntities(), renderWorkflows(), renderConflicts()]);
    feedback("Alles aktuell.");
  } catch (error) {
    feedback(`Systemzentrale konnte nicht geladen werden: ${error.message}`, true);
  }
}

const stylesheet = document.createElement("link");
stylesheet.rel = "stylesheet";
stylesheet.href = "system-cockpit.css";
document.head.append(stylesheet);

const observer = new MutationObserver(() => {
  if (ensureShell()) observer.disconnect();
});
observer.observe(document.body, { childList: true, subtree: true });
ensureShell();
