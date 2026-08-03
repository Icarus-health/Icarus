// Proaktiver Chief of Staff mit bewusst kleinem Aufmerksamkeitsbudget.
//
// Die Oberfläche zeigt höchstens fünf begründete Hinweise. Sie führt keine
// Handlung selbst aus und verwendet ausschließlich authentifizierte Endpunkte.

const tauri = window.__TAURI__?.core ?? null;
let connectionPromise = null;
let attentionRefresh = null;
let prepDialog = null;

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
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("de-DE", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function ensureAttentionPanel() {
  const dashboard = document.querySelector("#view-dashboard");
  if (!dashboard || dashboard.querySelector("#proactive-chief")) return;
  const panel = element("section", "proactive-chief");
  panel.id = "proactive-chief";
  panel.innerHTML = `
    <div class="proactive-head">
      <div>
        <p class="eyebrow">Dein Fokus</p>
        <h3>Was jetzt Aufmerksamkeit verdient</h3>
        <p class="muted">Höchstens fünf Hinweise – jeder mit Grund und nächstem Schritt.</p>
      </div>
      <button id="attention-refresh" type="button" class="ghost small">Aktualisieren</button>
    </div>
    <p id="attention-feedback" class="muted" role="status"></p>
    <ol id="attention-list" class="attention-list"></ol>
    <div id="meeting-prep-area" class="meeting-prep-area"></div>
  `;
  const greeting = dashboard.querySelector("#greeting");
  greeting?.after(panel);
  panel.querySelector("#attention-refresh")?.addEventListener("click", refreshAttention);
}

function openView(view) {
  if (view === "system") {
    document.querySelector(".nav-more > summary")?.click();
  }
  const button = document.querySelector(`button[data-view="${view}"]`);
  button?.click();
}

async function control(signal, mode) {
  const feedback = document.querySelector("#attention-feedback");
  if (feedback) feedback.textContent = mode === "snooze" ? "Hinweis wird zurückgestellt …" : "Hinweis wird für diesen Stand ausgeblendet …";
  try {
    await api(
      `/chief-of-staff/attention/${encodeURIComponent(signal.id)}/${mode}`,
      {
        method: "POST",
        body: JSON.stringify(
          mode === "snooze"
            ? { fingerprint: signal.fingerprint, hours: 24 }
            : { fingerprint: signal.fingerprint }
        ),
      }
    );
    await refreshAttention();
  } catch (error) {
    if (feedback) feedback.textContent = `Hinweis konnte nicht geändert werden: ${error.message}`;
  }
}

function signalCard(signal, index) {
  const item = element("li", `attention-item attention-${signal.kind}`);
  item.dataset.signalId = signal.id;
  const number = element("span", "attention-rank", String(index + 1));
  const content = element("div", "attention-content");
  const titleRow = element("div", "attention-title-row");
  titleRow.append(
    element("strong", "", signal.title),
    signal.due_at ? element("time", "meta", formatDate(signal.due_at)) : document.createTextNode("")
  );
  const reason = element("p", "attention-reason", signal.reason);
  const next = element("p", "attention-next");
  next.append(element("span", "meta", "Nächster Schritt"), document.createTextNode(` ${signal.next_action}`));
  const consequence = signal.consequence
    ? element("p", "muted attention-consequence", signal.consequence)
    : null;
  const actions = element("div", "row attention-actions");
  const openLabel = signal.kind === "meeting" ? "Vorbereitung öffnen" : "Öffnen";
  actions.append(
    action(openLabel, () => {
      if (signal.kind === "meeting" && signal.source_id) {
        void showMeetingPrep(signal.source_id);
      } else {
        openView(signal.target_view);
      }
    }),
    action("Morgen", () => control(signal, "snooze"), "ghost small"),
    action("Für diesen Stand ausblenden", () => control(signal, "dismiss"), "ghost small")
  );
  content.append(titleRow, reason, next);
  if (consequence) content.append(consequence);
  content.append(actions);
  item.append(number, content);
  return item;
}

function ensureDialog() {
  if (prepDialog) return prepDialog;
  prepDialog = element("dialog", "meeting-prep-dialog");
  prepDialog.id = "meeting-prep-dialog";
  prepDialog.innerHTML = `
    <div class="meeting-dialog-head">
      <div><p class="eyebrow">Terminvorbereitung</p><h3 id="meeting-prep-title"></h3></div>
      <button id="meeting-prep-close" type="button" class="ghost" aria-label="Schließen">Schließen</button>
    </div>
    <p id="meeting-prep-meta" class="muted"></p>
    <div id="meeting-prep-content"></div>
  `;
  document.body.append(prepDialog);
  prepDialog.querySelector("#meeting-prep-close")?.addEventListener("click", () => prepDialog.close());
  prepDialog.addEventListener("click", (event) => {
    if (event.target === prepDialog) prepDialog.close();
  });
  return prepDialog;
}

function list(title, items, renderer) {
  const section = element("section", "meeting-prep-section");
  section.append(element("h4", "", title));
  if (!items.length) {
    section.append(element("p", "muted", "Keine belegten Informationen gefunden."));
    return section;
  }
  const root = element("ul", "meeting-prep-list");
  for (const item of items) root.append(renderer(item));
  section.append(root);
  return section;
}

async function showMeetingPrep(uid) {
  const dialog = ensureDialog();
  const title = dialog.querySelector("#meeting-prep-title");
  const meta = dialog.querySelector("#meeting-prep-meta");
  const content = dialog.querySelector("#meeting-prep-content");
  if (title) title.textContent = "Wird vorbereitet …";
  if (meta) meta.textContent = "Icarus sammelt nur belegte lokale Zusammenhänge.";
  content?.replaceChildren();
  dialog.showModal();
  try {
    const prep = await api(`/chief-of-staff/meetings/${encodeURIComponent(uid)}/prep`);
    if (title) title.textContent = prep.event.summary;
    if (meta) {
      meta.textContent = [formatDate(prep.event.start), prep.event.location, ...(prep.event.attendees || [])]
        .filter(Boolean)
        .join(" · ");
    }
    const outcome = element("section", "meeting-outcome");
    outcome.append(
      element("p", "eyebrow", "Vorgeschlagenes Ergebnis"),
      element("strong", "", prep.suggested_outcome)
    );
    const questions = list("Fragen für den Termin", prep.questions, (question) => element("li", "", question));
    const projects = list("Verbundene Projekte", prep.related_projects, (match) => {
      const item = element("li", "meeting-project");
      item.append(element("strong", "", match.project.name));
      if (match.open_tasks.length) {
        item.append(element("p", "meta", `Offen: ${match.open_tasks.map((task) => task.title).join(" · ")}`));
      }
      if (match.decisions.length) {
        item.append(element("p", "meta", `Entscheidungen: ${match.decisions.map((note) => note.title).join(" · ")}`));
      }
      return item;
    });
    const history = list("Relevante Vorgeschichte", prep.related_episodes, (match) => {
      const item = element("li", "");
      item.append(
        element("strong", "", match.episode.title),
        element("p", "meta", formatDate(match.episode.occurred_at || match.episode.created_at))
      );
      return item;
    });
    const provenance = element(
      "p",
      "meta meeting-provenance",
      `Ohne Modell zusammengestellt · ${prep.provenance.project_count} Projekte · ${prep.provenance.episode_count} Ereignisse · ${prep.provenance.memory_count} Gedächtniseinträge`
    );
    content?.replaceChildren(outcome, questions, projects, history, provenance);
  } catch (error) {
    if (title) title.textContent = "Vorbereitung nicht verfügbar";
    content?.replaceChildren(element("p", "error", error.message));
  }
}

async function renderMeetingShortcuts() {
  const area = document.querySelector("#meeting-prep-area");
  if (!area) return;
  try {
    const result = await api("/chief-of-staff/meetings?days=3");
    area.replaceChildren();
    if (result.error || !result.items.length) return;
    const head = element("div", "meeting-shortcut-head");
    head.append(element("h4", "", "Kommende Termine"), element("p", "muted", "Kontext statt hektischer Suche kurz vorher."));
    const listRoot = element("div", "meeting-shortcuts");
    for (const meeting of result.items.slice(0, 3)) {
      const card = element("article", "meeting-shortcut");
      card.append(
        element("strong", "", meeting.summary),
        element("p", "meta", [formatDate(meeting.start), meeting.location].filter(Boolean).join(" · ")),
        action("Vorbereitung", () => showMeetingPrep(meeting.uid), "ghost small")
      );
      listRoot.append(card);
    }
    area.append(head, listRoot);
  } catch {
    area.replaceChildren();
  }
}

async function refreshAttention() {
  ensureAttentionPanel();
  if (attentionRefresh) return attentionRefresh;
  attentionRefresh = (async () => {
    const listRoot = document.querySelector("#attention-list");
    const feedback = document.querySelector("#attention-feedback");
    if (!listRoot) return;
    listRoot.replaceChildren(element("li", "muted", "Prioritäten werden geprüft …"));
    try {
      const signals = await api("/chief-of-staff/attention?limit=5");
      listRoot.replaceChildren();
      if (!signals.length) {
        listRoot.append(element("li", "attention-empty", "Aktuell braucht nichts besondere Aufmerksamkeit."));
      } else {
        signals.forEach((signal, index) => listRoot.append(signalCard(signal, index)));
      }
      if (feedback) feedback.textContent = signals.length === 5
        ? "Auf fünf Hinweise begrenzt. Weitere Themen bleiben in ihren Bereichen sichtbar."
        : "";
      await renderMeetingShortcuts();
    } catch (error) {
      listRoot.replaceChildren(element("li", "error", `Fokus konnte nicht geladen werden: ${error.message}`));
    } finally {
      attentionRefresh = null;
    }
  })();
  return attentionRefresh;
}

const stylesheet = document.createElement("link");
stylesheet.rel = "stylesheet";
stylesheet.href = "proactive-chief-of-staff.css";
document.head.append(stylesheet);

const observer = new MutationObserver(() => {
  ensureAttentionPanel();
});
observer.observe(document.body, { childList: true, subtree: true });
ensureAttentionPanel();
void refreshAttention();
window.setInterval(refreshAttention, 60_000);
