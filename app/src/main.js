// Icarus — Oberfläche.
//
// Bewusst ohne Framework. Der Zweck des Gerüsts ist zu zeigen, wie Gedächtnis,
// Freigaben und Protokoll zusammenspielen — nicht, wie man eine SPA baut.

const { invoke } = window.__TAURI__.core;

let base = null;
let token = null;

const $ = (sel) => document.querySelector(sel);
const statusEl = $("#status");

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

// -- Ansichten --------------------------------------------------------------

const REFRESH = {
  dashboard: loadDashboard,
  projects: loadProjects,
  ingest: loadEpisodes,
  memory: loadMemory,
  audit: loadAudit,
  chat: loadApprovals,
};

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    for (const view of document.querySelectorAll(".view")) view.hidden = true;
    $(`#view-${tab.dataset.view}`).hidden = false;
    REFRESH[tab.dataset.view]?.();
  });
});


// -- Heute ------------------------------------------------------------------

function greeting() {
  const h = new Date().getHours();
  if (h < 5) return "Noch wach?";
  if (h < 11) return "Guten Morgen";
  if (h < 18) return "Hallo";
  return "Guten Abend";
}

function li(main, meta, action) {
  const el = document.createElement("li");
  const p = document.createElement("p");
  p.className = "statement";
  p.textContent = main;
  el.append(p);
  if (meta) {
    const m = document.createElement("p");
    m.className = "meta";
    m.textContent = meta;
    el.append(m);
  }
  if (action) el.append(action);
  return el;
}

function renderTasks(block) {
  const list = $("#task-list");
  const items = block.items ?? [];
  list.replaceChildren();
  $("#task-empty").hidden = items.length > 0;

  const badge = $("#task-badge");
  badge.hidden = !block.overdue;
  badge.textContent = `${block.overdue} überfällig`;

  for (const t of items) {
    const done = document.createElement("button");
    done.className = "ghost small";
    done.textContent = "Erledigt";
    done.addEventListener("click", async () => {
      await api(`/tasks/${t.id}/done`, { method: "POST" });
      await loadDashboard();
    });

    const bits = [];
    if (t.due) bits.push(`fällig ${new Date(t.due).toLocaleDateString("de-DE")}`);
    bits.push(SOURCE_LABELS[t.provenance.source_type] ?? t.provenance.source_type);

    const row = li(t.title, bits.join(" · "), done);
    if (t.overdue) row.classList.add("overdue");
    list.append(row);
  }
}

function renderEvents(block) {
  const list = $("#event-list");
  list.replaceChildren();
  $("#event-note").textContent = block.error ?? (block.items.length ? "" : "Nichts geplant.");

  for (const e of block.items ?? []) {
    const start = e.start ? new Date(e.start) : null;
    const when = start
      ? (e.all_day
          ? start.toLocaleDateString("de-DE", { weekday: "short", day: "2-digit", month: "2-digit" }) + " ganztags"
          : start.toLocaleString("de-DE", { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }))
      : "?";
    const bits = [when];
    if (e.location) bits.push(e.location);
    if (e.attendees?.length) bits.push(`mit ${e.attendees.join(", ")}`);
    list.append(li(e.summary, bits.join(" · ")));
  }
}

function renderMail(block) {
  const list = $("#mail-list");
  list.replaceChildren();
  $("#mail-note").textContent = block.error ?? (block.items.length ? "" : "Nichts Neues.");

  const badge = $("#mail-badge");
  badge.hidden = !block.unread;
  badge.textContent = `${block.unread} ungelesen`;

  for (const m of block.items ?? []) {
    const when = m.date ? new Date(m.date).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "";
    const row = li(m.subject || "(kein Betreff)", `${m.from} · ${when}`);
    if (m.unread) row.classList.add("unread");
    list.append(row);
  }
}

function renderMemoryCard(block) {
  const list = $("#memory-list");
  list.replaceChildren();
  $("#memory-note").textContent = block.count
    ? `${block.count} Aussagen insgesamt.`
    : "Noch nichts gemerkt.";

  for (const a of block.recent ?? []) {
    list.append(li(a.statement, SOURCE_LABELS[a.provenance.source_type] ?? a.provenance.source_type));
  }
}

function renderProjectTeaser(block) {
  const list = $("#project-teaser");
  const items = block?.items ?? [];
  list.replaceChildren();
  $("#project-teaser-note").textContent =
    block?.error ?? (items.length ? "" : "Noch keine Projekte.");

  for (const p of items.slice(0, 5)) {
    const bits = [];
    if (p.area) bits.push(p.area);
    if (p.deadline) bits.push(`Frist ${new Date(p.deadline).toLocaleDateString("de-DE")}`);
    bits.push(PROJECT_STATUS[p.status] ?? p.status);
    list.append(li(p.name, bits.join(" · ")));
  }
}

async function loadDashboard() {
  $("#greeting").textContent = greeting();
  const data = await api("/dashboard");
  renderProjectTeaser(data.projects);
  renderTasks(data.tasks);
  renderEvents(data.calendar);
  renderMail(data.mail);
  renderMemoryCard(data.memory);
  setPendingBadge(data.episodes?.pending);
}

$("#task-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const title = $("#task-title").value.trim();
  if (!title) return;
  const due = $("#task-due").value;
  await api("/tasks", {
    method: "POST",
    // Datumsfeld liefert nur den Tag; als Tagesende deuten, damit eine Aufgabe
    // nicht schon morgens früh als überfällig gilt.
    body: JSON.stringify({ title, due: due ? `${due}T23:59:00` : null }),
  });
  $("#task-title").value = "";
  $("#task-due").value = "";
  await loadDashboard();
});

// -- Projekte ---------------------------------------------------------------

const PROJECT_STATUS = {
  idea: "Idee",
  active: "aktiv",
  paused: "pausiert",
  done: "abgeschlossen",
  // Aufgegeben ist nicht abgeschlossen. Der Unterschied muss auch in der
  // Oberfläche sichtbar bleiben, sonst sieht eine Rückschau nach Jahren so
  // aus, als wäre alles gelungen.
  dropped: "aufgegeben",
};

const NOTE_KIND = {
  meeting: "Protokoll",
  research: "Recherche",
  idea: "Idee",
  decision: "Entscheidung",
  reference: "Referenz",
};

let selectedProject = null;

async function loadProjects() {
  const projects = await api("/projects?all=true");
  const list = $("#project-list");
  list.replaceChildren();
  $("#project-empty").hidden = projects.length > 0;

  $("#areas").replaceChildren(
    ...[...new Set(projects.map((p) => p.area).filter(Boolean))].map((a) => {
      const o = document.createElement("option");
      o.value = a;
      return o;
    })
  );

  for (const p of projects) {
    const el = document.createElement("li");
    el.className = "project";
    if (!p.open) el.classList.add("closed");
    if (p.id === selectedProject) el.classList.add("selected");

    const name = document.createElement("p");
    name.className = "statement";
    name.textContent = p.name;

    const meta = document.createElement("p");
    meta.className = "meta";
    const bits = [PROJECT_STATUS[p.status] ?? p.status];
    if (p.area) bits.push(p.area);
    if (p.deadline) bits.push(`Frist ${new Date(p.deadline).toLocaleDateString("de-DE")}`);
    meta.textContent = bits.join(" · ");

    el.append(name, meta);
    el.addEventListener("click", () => showProject(p.id));
    list.append(el);
  }

  if (selectedProject) await showProject(selectedProject);
}

async function showProject(id) {
  selectedProject = id;
  for (const el of document.querySelectorAll("#project-list .project")) {
    el.classList.remove("selected");
  }

  const p = await api(`/projects/${id}`);
  const box = $("#project-detail");
  box.replaceChildren();

  const head = document.createElement("h3");
  head.textContent = p.name;
  box.append(head);

  const meta = document.createElement("p");
  meta.className = "meta";
  const bits = [PROJECT_STATUS[p.status] ?? p.status];
  if (p.area) bits.push(p.area);
  if (p.deadline) bits.push(`Frist ${new Date(p.deadline).toLocaleDateString("de-DE")}`);
  bits.push(SOURCE_LABELS[p.provenance.source_type] ?? p.provenance.source_type);
  meta.textContent = bits.join(" · ");
  box.append(meta);

  if (p.description) {
    const desc = document.createElement("p");
    desc.textContent = p.description;
    box.append(desc);
  }

  const status = document.createElement("select");
  for (const [value, label] of Object.entries(PROJECT_STATUS)) {
    const o = document.createElement("option");
    o.value = value;
    o.textContent = label;
    o.selected = value === p.status;
    status.append(o);
  }
  status.addEventListener("change", async () => {
    await api(`/projects/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ status: status.value }),
    });
    await loadProjects();
  });
  box.append(status);

  const tasksHead = document.createElement("h4");
  tasksHead.textContent = `Offene Aufgaben (${p.tasks.length})`;
  box.append(tasksHead);

  const tasks = document.createElement("ul");
  for (const t of p.tasks) {
    const done = document.createElement("button");
    done.className = "ghost small";
    done.textContent = "Erledigt";
    done.addEventListener("click", async () => {
      await api(`/tasks/${t.id}/done`, { method: "POST" });
      await showProject(id);
    });
    const when = t.due ? `fällig ${new Date(t.due).toLocaleDateString("de-DE")}` : "";
    const row = li(t.title, when, done);
    if (t.overdue) row.classList.add("overdue");
    tasks.append(row);
  }
  box.append(tasks);

  const form = document.createElement("form");
  form.className = "row";
  const input = document.createElement("input");
  input.type = "text";
  input.placeholder = "Aufgabe zu diesem Projekt";
  input.autocomplete = "off";
  const add = document.createElement("button");
  add.type = "submit";
  add.textContent = "Anlegen";
  form.append(input, add);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const title = input.value.trim();
    if (!title) return;
    await api("/tasks", {
      method: "POST",
      body: JSON.stringify({ title, project_id: id }),
    });
    input.value = "";
    await showProject(id);
  });
  box.append(form);

  if (p.notes.length) {
    const notesHead = document.createElement("h4");
    notesHead.textContent = `Notizen (${p.notes.length})`;
    box.append(notesHead);

    const notes = document.createElement("ul");
    for (const n of p.notes) {
      const when = new Date(n.updated_at).toLocaleDateString("de-DE");
      const kind = NOTE_KIND[n.kind] ?? n.kind;
      const origin = SOURCE_LABELS[n.provenance.source_type] ?? n.provenance.source_type;
      notes.append(li(n.title, `${kind} · ${when} · ${origin}`));
    }
    box.append(notes);
  }

  const current = [...document.querySelectorAll("#project-list .project")].find(
    (el) => el.querySelector(".statement")?.textContent === p.name
  );
  current?.classList.add("selected");
}

$("#project-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = $("#project-name").value.trim();
  if (!name) return;
  const deadline = $("#project-deadline").value;
  const created = await api("/projects", {
    method: "POST",
    body: JSON.stringify({
      name,
      area: $("#project-area").value.trim() || null,
      deadline: deadline ? `${deadline}T23:59:00` : null,
    }),
  });
  $("#project-name").value = "";
  $("#project-deadline").value = "";
  selectedProject = created.id;
  await loadProjects();
});

// -- Gespräch ---------------------------------------------------------------

function addMessage(role, text) {
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  el.textContent = text;
  $("#messages").append(el);
  el.scrollIntoView({ block: "end" });
}

function addNotice(text) {
  const el = document.createElement("div");
  el.className = "notice";
  // Was ohne Rückfrage getan wurde, erfährt der Nutzer hinterher —
  // stillschweigend passiert nichts.
  el.textContent = `Ausgeführt: ${text}`;
  $("#messages").append(el);
}

/** Zeigt eine wartende Aktion mit vollständigem Trockenlauf. */
function renderApproval(approval) {
  const card = document.createElement("form");
  card.className = "approval";

  const head = document.createElement("h3");
  head.textContent =
    approval.action_class === "outward"
      ? "Das verlässt deinen Rechner"
      : "Freigabe nötig";
  card.append(head);

  if (approval.reasons?.length) {
    const why = document.createElement("p");
    why.className = "muted";
    why.textContent = approval.reasons.join(" ");
    card.append(why);
  }

  // Der genaue Inhalt, nicht eine Zusammenfassung davon.
  const dry = document.createElement("pre");
  dry.className = "dry-run";
  dry.textContent = approval.dry_run;
  card.append(dry);

  let confirmInput = null;
  if (approval.confirmation_phrase) {
    const label = document.createElement("label");
    label.className = "confirm";
    label.textContent = `Zum Bestätigen wiederholen: ${approval.confirmation_phrase}`;
    confirmInput = document.createElement("input");
    confirmInput.type = "text";
    confirmInput.autocomplete = "off";
    confirmInput.required = true;
    label.append(confirmInput);
    card.append(label);
  }

  const actions = document.createElement("div");
  actions.className = "row";

  const ok = document.createElement("button");
  ok.type = "submit";
  ok.textContent = "Ausführen";

  const no = document.createElement("button");
  no.type = "button";
  no.className = "ghost";
  no.textContent = "Ablehnen";

  actions.append(ok, no);
  card.append(actions);

  const resolve = async (granted) => {
    ok.disabled = no.disabled = true;
    try {
      const turn = await api(`/approvals/${approval.id}`, {
        method: "POST",
        body: JSON.stringify({
          granted,
          confirmation: confirmInput ? confirmInput.value : null,
        }),
      });
      card.remove();
      if (turn.reply) addMessage("assistant", turn.reply);
      (turn.notices ?? []).forEach(addNotice);
      (turn.approvals ?? []).forEach((a) => $("#approvals").append(renderApproval(a)));
    } catch (err) {
      ok.disabled = no.disabled = false;
      const error = document.createElement("p");
      error.className = "error";
      error.textContent = err.message;
      card.append(error);
    }
  };

  card.addEventListener("submit", (e) => {
    e.preventDefault();
    resolve(true);
  });
  no.addEventListener("click", () => resolve(false));

  return card;
}

async function loadApprovals() {
  const pending = await api("/approvals");
  $("#approvals").replaceChildren(...pending.map(renderApproval));
}

$("#chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("#chat-input");
  const message = input.value.trim();
  if (!message) return;

  addMessage("user", message);
  input.value = "";
  input.disabled = true;

  try {
    const turn = await api("/chat", {
      method: "POST",
      body: JSON.stringify({ message }),
    });
    if (turn.reply) addMessage("assistant", turn.reply);
    (turn.notices ?? []).forEach(addNotice);
    $("#approvals").replaceChildren(...(turn.approvals ?? []).map(renderApproval));
  } catch (err) {
    addMessage("error", err.message);
  } finally {
    input.disabled = false;
    input.focus();
  }
});

// -- Rohmaterial ------------------------------------------------------------
//
// Die Mittelfristschicht. Was hier liegt, behauptet nichts über den Nutzer —
// deshalb gibt es in dieser Ansicht bewusst keinen Knopf, der etwas in den
// Bestand schiebt. Der Weg dorthin führt nur über die Verdichtung.

const EPISODE_KIND = {
  message: "Nachricht",
  document: "Dokument",
  event: "Termin",
  interaction: "Kontakt",
  observation: "Beobachtung",
};

const EPISODE_STATE = {
  new: "wartet",
  consolidated: "verdichtet",
  archived: "archiviert",
  ignored: "verworfen",
};

function setPendingBadge(count) {
  const badge = $("#pending-badge");
  badge.hidden = !count;
  badge.textContent = String(count ?? 0);
}

async function loadEpisodes() {
  const [episodes, counts] = await Promise.all([
    api("/episodes?limit=200"),
    api("/episodes/counts"),
  ]);
  setPendingBadge(counts.new);

  const list = $("#episode-list");
  list.replaceChildren();
  $("#episode-empty").hidden = episodes.length > 0;

  for (const e of episodes) {
    const el = document.createElement("li");
    el.className = `episode ${e.state}`;

    const title = document.createElement("p");
    title.className = "statement";
    title.textContent = e.title;

    const meta = document.createElement("p");
    meta.className = "meta";
    const when = new Date(e.occurred_at ?? e.recorded_at).toLocaleDateString("de-DE");
    const origin = SOURCE_LABELS[e.provenance.source_type] ?? e.provenance.source_type;
    const bits = [EPISODE_KIND[e.kind] ?? e.kind, when, origin, EPISODE_STATE[e.state] ?? e.state];
    if (e.participants.length) bits.push(e.participants.join(", "));
    meta.textContent = bits.join(" · ");

    // Der Rohtext, eingeklappt. Er stammt aus fremder Quelle und wird deshalb
    // nie in eine Zeile gequetscht, in der man ihn für eine Aussage von Icarus
    // halten könnte.
    const body = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "Wortlaut";
    const pre = document.createElement("pre");
    pre.className = "dry-run";
    pre.textContent = e.body;
    body.append(summary, pre);

    el.append(title, meta, body);

    if (e.state === "new") {
      const drop = document.createElement("button");
      drop.className = "ghost small";
      drop.textContent = "Gibt nichts her";
      drop.addEventListener("click", async () => {
        await api(`/episodes/${e.id}/ignore`, { method: "POST" });
        await loadEpisodes();
      });
      el.append(drop);
    }

    list.append(el);
  }
}

$("#ingest-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const path = $("#ingest-path").value.trim();
  if (!path) return;

  const note = $("#ingest-note");
  const button = event.target.querySelector("button");
  button.disabled = true;
  note.textContent = "Wird gelesen…";
  note.classList.remove("error");

  try {
    const report = await api("/ingest", {
      method: "POST",
      body: JSON.stringify({ path, adapter: $("#ingest-adapter").value }),
    });
    const bits = [`${report.recorded} aufgenommen`];
    if (report.duplicates) bits.push(`${report.duplicates} schon bekannt`);
    if (report.skipped) bits.push(`${report.skipped} übersprungen`);
    note.textContent =
      bits.join(", ") + ". Nichts davon gilt als gewusst — der Bestand ist unberührt.";
    await loadEpisodes();
  } catch (err) {
    note.textContent = err.message;
    note.classList.add("error");
  } finally {
    button.disabled = false;
  }
});

// -- Gedächtnis -------------------------------------------------------------

async function loadMemory() {
  const [assertions, ctx] = await Promise.all([api("/assertions"), api("/context")]);
  $("#context").textContent = ctx.context;

  const list = $("#assertions");
  list.replaceChildren();
  $("#empty").hidden = assertions.length > 0;

  for (const a of assertions) {
    const li = document.createElement("li");

    const text = document.createElement("p");
    text.className = "statement";
    text.textContent = a.statement;

    // Herkunft steht immer sichtbar unter der Aussage, nie versteckt.
    const meta = document.createElement("p");
    meta.className = "meta";
    const origin = SOURCE_LABELS[a.provenance.source_type] ?? a.provenance.source_type;
    meta.textContent = `${a.kind} · ${origin} · ${new Date(a.recorded_at).toLocaleDateString("de-DE")}`;
    if (a.provenance.source_type === "inference") meta.classList.add("inferred");

    const forget = document.createElement("button");
    forget.className = "ghost small";
    forget.textContent = "Vergessen";
    forget.addEventListener("click", async () => {
      const affected = await api(`/assertions/${a.id}/redact`, {
        method: "POST",
        body: JSON.stringify({ reason: "user_request" }),
      });
      if (affected.length > 1) {
        statusEl.textContent = `Vergessen — samt ${affected.length - 1} daraus abgeleiteten Aussagen.`;
      }
      await loadMemory();
    });

    li.append(text, meta, forget);
    list.append(li);
  }
}

$("#record-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("#statement");
  const statement = input.value.trim();
  if (!statement) return;

  await api("/assertions", {
    method: "POST",
    body: JSON.stringify({
      statement,
      kind: $("#kind").value,
      provenance: { source_type: "user_stated", captured_at: new Date().toISOString() },
      confidence: 1.0,
    }),
  });
  input.value = "";
  await loadMemory();
});

// -- Protokoll --------------------------------------------------------------

const OUTCOME_LABELS = {
  executed: "ausgeführt",
  denied: "durch eine Grenze abgelehnt",
  refused: "von dir abgelehnt",
  failed: "fehlgeschlagen",
  pending: "wartete auf Freigabe",
};

async function loadAudit() {
  const entries = await api("/audit?limit=100");
  const list = $("#audit");
  list.replaceChildren();

  for (const e of entries) {
    const li = document.createElement("li");
    li.className = `audit ${e.outcome}`;

    const head = document.createElement("p");
    head.className = "statement";
    head.textContent = `${e.tool} — ${OUTCOME_LABELS[e.outcome] ?? e.outcome}`;

    const meta = document.createElement("p");
    meta.className = "meta";
    const bits = [new Date(e.at).toLocaleString("de-DE"), e.action_class, e.level];
    if (e.approved_by) bits.push(`freigegeben von ${e.approved_by}`);
    if (e.model) bits.push(e.model);
    meta.textContent = bits.join(" · ");

    const args = document.createElement("pre");
    args.className = "args";
    args.textContent = JSON.stringify(e.arguments, null, 2);

    li.append(head, meta, args);
    if (e.detail) {
      const detail = document.createElement("p");
      detail.className = "meta";
      detail.textContent = e.detail;
      li.append(detail);
    }
    list.append(li);
  }
}

// -- Start ------------------------------------------------------------------

async function start() {
  const info = await invoke("sidecar_info");
  base = `http://127.0.0.1:${info.port}`;
  token = info.token;

  const health = await fetch(`${base}/health`).then((r) => r.json());

  const bits = [];
  bits.push(health.chat ? `Modell: ${health.model}` : "kein Modell");
  bits.push(health.semantic_search ? "semantische Suche" : "Textsuche");
  statusEl.textContent = bits.join(" · ");
  statusEl.classList.add("ready");

  if (!health.chat) {
    // Lieber ehrlich sagen, was fehlt, als einen kaputten Chat anbieten.
    addMessage(
      "assistant",
      "Es ist kein Modell konfiguriert — Gespräche gehen noch nicht. " +
        "Das Gedächtnis funktioniert trotzdem: im Reiter Gedächtnis lassen sich " +
        "Aussagen speichern, ansehen und widerrufen."
    );
    $("#chat-input").placeholder = "Kein Modell konfiguriert";
  }

  await loadApprovals();
  await loadDashboard();
}

async function startWithRetry(attempts = 40) {
  for (let i = 0; i < attempts; i += 1) {
    try {
      await start();
      return;
    } catch {
      await new Promise((r) => setTimeout(r, 250));
    }
  }
  statusEl.textContent = "Der Sidecar antwortet nicht.";
  statusEl.classList.add("error");
}

startWithRetry();
