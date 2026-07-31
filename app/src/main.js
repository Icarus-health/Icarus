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
  setup: loadSetup,
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

// -- Einrichtung ------------------------------------------------------------
//
// Alles, was sonst in einer .env stünde. Kein Konto, keine Anmeldung: Icarus
// kennt keinen Server, bei dem man sich anmelden könnte. Was hier passiert, ist
// die Frage, welchem Anbieter das Gespräch anvertraut wird — und "keinem" ist
// eine gültige Antwort.

let setupState = null;

const PROVIDER_LABELS = {
  "": "Kein Modell (Gedächtnis funktioniert trotzdem)",
  openai: "OpenAI",
  anthropic: "Anthropic",
  ollama: "Ollama (läuft lokal, nichts verlässt den Rechner)",
};

async function saveSetup(patch) {
  setupState = await api("/setup", { method: "PUT", body: JSON.stringify(patch) });
  return setupState;
}

function field(label, id, { type = "text", value = "", placeholder = "", hint = "" } = {}) {
  const wrap = document.createElement("label");
  wrap.className = "field";
  wrap.textContent = label;
  const input = document.createElement("input");
  input.type = type;
  input.id = id;
  input.value = value ?? "";
  input.placeholder = placeholder;
  input.autocomplete = "off";
  wrap.append(input);
  if (hint) {
    const p = document.createElement("p");
    p.className = "meta";
    p.textContent = hint;
    wrap.append(p);
  }
  return wrap;
}

function providerSelect(id, current) {
  const wrap = document.createElement("label");
  wrap.className = "field";
  wrap.textContent = "Anbieter";
  const select = document.createElement("select");
  select.id = id;
  for (const [value, label] of Object.entries(PROVIDER_LABELS)) {
    const o = document.createElement("option");
    o.value = value;
    o.textContent = label;
    o.selected = value === current;
    select.append(o);
  }
  wrap.append(select);
  return wrap;
}

/** Probiert eine Verbindung wirklich aus, statt sie zu behaupten. */
function testButton(ziel, label, target) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "ghost small";
  button.textContent = label;
  button.addEventListener("click", async () => {
    button.disabled = true;
    target.textContent = "Wird geprüft…";
    target.classList.remove("error");
    try {
      const result = await api(`/setup/test/${ziel}`, { method: "POST" });
      target.textContent = result.detail;
      target.classList.toggle("error", !result.ok);
    } catch (err) {
      target.textContent = err.message;
      target.classList.add("error");
    } finally {
      button.disabled = false;
    }
  });
  return button;
}

async function loadSetup() {
  setupState = await api("/setup");
  const s = setupState.settings;
  const panel = $("#setup-panel");
  panel.replaceChildren();

  // Ohne Schlüsselspeicher gilt ein eingetragener Schlüssel nur für diese
  // Sitzung. Das muss dastehen, nicht überraschen.
  if (!setupState.keychain_available) {
    const warn = document.createElement("p");
    warn.className = "meta error";
    warn.textContent =
      "Kein Schlüsselspeicher gefunden. Eingetragene Schlüssel gelten nur für " +
      "diese Sitzung und werden nicht auf die Platte geschrieben.";
    panel.append(warn);
  }

  // -- Modell
  const modell = document.createElement("section");
  modell.className = "setup-block";
  const mh = document.createElement("h3");
  mh.textContent = "Modell";
  const mnote = document.createElement("p");
  mnote.className = "muted";
  mnote.textContent = setupState.secrets[
    s.provider === "anthropic" ? "ANTHROPIC_API_KEY" : "OPENAI_API_KEY"
  ]
    ? "Ein Schlüssel ist hinterlegt. Leer lassen heißt: unverändert."
    : "Ohne Modell funktioniert das Gedächtnis weiterhin.";
  const psel = providerSelect("setup-provider", s.provider);
  const mmodel = field("Modell", "setup-model", {
    value: s.model,
    placeholder: setupState.default_models[s.provider] ?? "",
  });
  const mkey = field("API-Schlüssel", "setup-key", {
    type: "password",
    placeholder: "unverändert lassen",
  });
  const mend = field("Eigene Adresse", "setup-endpoint", {
    value: s.endpoint,
    hint: "Nur nötig für Ollama an anderem Port oder einen Proxy.",
  });
  const mresult = document.createElement("p");
  mresult.className = "meta";
  const msave = document.createElement("button");
  msave.type = "button";
  msave.textContent = "Übernehmen";
  msave.addEventListener("click", async () => {
    msave.disabled = true;
    try {
      await saveSetup({
        provider: $("#setup-provider").value,
        model: $("#setup-model").value.trim(),
        endpoint: $("#setup-endpoint").value.trim(),
        api_key: $("#setup-key").value || null,
      });
      await loadSetup();
      await refreshStatus();
    } finally {
      msave.disabled = false;
    }
  });
  const mrow = document.createElement("div");
  mrow.className = "row";
  mrow.append(msave, testButton("modell", "Verbindung prüfen", mresult));
  modell.append(mh, mnote, psel, mmodel, mkey, mend, mrow, mresult);
  panel.append(modell);

  // -- Ordner
  const ordner = document.createElement("section");
  ordner.className = "setup-block";
  const oh = document.createElement("h3");
  oh.textContent = "Ordnerzugriff";
  const onote = document.createElement("p");
  onote.className = "muted";
  onote.textContent =
    "Leer heißt: gar kein Dateizugriff. Es gibt bewusst keinen Vorgabewert — " +
    "ein voreingestelltes Home-Verzeichnis wäre die Bequemlichkeit, die den " +
    "Schutz aufhebt.";
  const oinput = field("Ordner, durch Doppelpunkt getrennt", "setup-roots", {
    value: s.file_roots.join(":"),
    placeholder: "/Users/du/Dokumente/Notizen",
  });
  const osave = document.createElement("button");
  osave.type = "button";
  osave.textContent = "Übernehmen";
  osave.addEventListener("click", async () => {
    const roots = $("#setup-roots").value.split(":").map((p) => p.trim()).filter(Boolean);
    await saveSetup({ file_roots: roots });
    await loadSetup();
  });
  ordner.append(oh, onote, oinput, osave);
  panel.append(ordner);

  // -- Mail
  const mail = document.createElement("section");
  mail.className = "setup-block";
  const mah = document.createElement("h3");
  mah.textContent = "E-Mail";
  const manote = document.createElement("p");
  manote.className = "muted";
  manote.textContent =
    "Offene Protokolle statt Anbieter-APIs. Nutze ein App-Passwort, niemals " +
    "dein Hauptpasswort. Gelesene Nachrichten gelten immer als fremder Inhalt.";
  const maresult = document.createElement("p");
  maresult.className = "meta";
  const masave = document.createElement("button");
  masave.type = "button";
  masave.textContent = "Übernehmen";
  masave.addEventListener("click", async () => {
    await saveSetup({
      mail: {
        imap_host: $("#mail-imap").value.trim(),
        imap_port: Number($("#mail-imap-port").value) || 993,
        smtp_host: $("#mail-smtp").value.trim(),
        smtp_port: Number($("#mail-smtp-port").value) || 587,
        user: $("#mail-user").value.trim(),
        sender: $("#mail-from").value.trim(),
      },
      mail_password: $("#mail-pass").value || null,
    });
    await loadSetup();
    await refreshStatus();
  });
  const marow = document.createElement("div");
  marow.className = "row";
  marow.append(masave, testButton("mail", "Posteingang prüfen", maresult));
  mail.append(
    mah, manote,
    field("IMAP-Server", "mail-imap", { value: s.mail.imap_host, placeholder: "imap.example.com" }),
    field("IMAP-Port", "mail-imap-port", { value: s.mail.imap_port }),
    field("SMTP-Server", "mail-smtp", { value: s.mail.smtp_host, placeholder: "leer lassen: nur lesen" }),
    field("SMTP-Port", "mail-smtp-port", { value: s.mail.smtp_port }),
    field("Benutzer", "mail-user", { value: s.mail.user, placeholder: "du@example.com" }),
    field("Passwort", "mail-pass", {
      type: "password",
      placeholder: setupState.secrets.ICARUS_MAIL_PASSWORD ? "hinterlegt" : "App-Passwort",
    }),
    field("Absender", "mail-from", { value: s.mail.sender, hint: "Leer: der Benutzer oben." }),
    marow, maresult
  );
  panel.append(mail);

  // -- Kalender
  const kal = document.createElement("section");
  kal.className = "setup-block";
  const kh = document.createElement("h3");
  kh.textContent = "Kalender";
  const knote = document.createElement("p");
  knote.className = "muted";
  knote.textContent =
    "CalDAV. Termine ohne Gäste bleiben lokal; sobald jemand eingeladen wird, " +
    "ist es außenwirksam und verlangt die strenge Bestätigung.";
  const kresult = document.createElement("p");
  kresult.className = "meta";
  const ksave = document.createElement("button");
  ksave.type = "button";
  ksave.textContent = "Übernehmen";
  ksave.addEventListener("click", async () => {
    await saveSetup({
      calendar: { url: $("#cal-url").value.trim(), user: $("#cal-user").value.trim() },
      calendar_password: $("#cal-pass").value || null,
    });
    await loadSetup();
    await refreshStatus();
  });
  const krow = document.createElement("div");
  krow.className = "row";
  krow.append(ksave, testButton("kalender", "Kalender prüfen", kresult));
  kal.append(
    kh, knote,
    field("CalDAV-Adresse", "cal-url", { value: s.calendar.url, placeholder: "https://caldav.example.com/kalender/" }),
    field("Benutzer", "cal-user", { value: s.calendar.user, hint: "Leer: der Mailbenutzer." }),
    field("Passwort", "cal-pass", {
      type: "password",
      placeholder: setupState.secrets.ICARUS_CALDAV_PASSWORD ? "hinterlegt" : "",
    }),
    krow, kresult
  );
  panel.append(kal);
}

// -- Erststart --------------------------------------------------------------
//
// Jeder Schritt ist überspringbar. Icarus läuft auch, wenn man alles
// überspringt — genau das soll der Assistent zeigen, statt Pflichtfelder
// aufzubauen, an denen jemand abbricht.

const WIZARD_STEPS = [
  {
    title: "Willkommen",
    text:
      "Icarus ist dein Gedächtnis. Alles bleibt auf diesem Rechner — es gibt " +
      "kein Konto und keinen Server, bei dem du dich anmeldest. Die nächsten " +
      "Schritte sind alle freiwillig; du kannst jeden überspringen.",
    build: () => document.createDocumentFragment(),
    apply: async () => {},
  },
  {
    title: "Modell",
    text:
      "Womit soll gesprochen werden? Ohne Modell funktioniert das Gedächtnis " +
      "weiterhin: Aussagen speichern, ansehen und widerrufen geht offline.",
    build: (s) => {
      const frag = document.createDocumentFragment();
      frag.append(
        providerSelect("wiz-provider", s.settings.provider),
        field("API-Schlüssel", "wiz-key", {
          type: "password",
          hint: "Bei Ollama nicht nötig. Wird im Schlüsselbund abgelegt, nie in einer Datei.",
        })
      );
      return frag;
    },
    apply: async () => {
      const provider = $("#wiz-provider").value;
      await saveSetup({ provider, api_key: $("#wiz-key").value || null });
      if (!provider) return "Kein Modell — das Gedächtnis läuft trotzdem.";
      const result = await api("/setup/test/modell", { method: "POST" });
      return result.detail;
    },
  },
  {
    title: "Ordnerzugriff",
    text:
      "Aus welchen Ordnern darf gelesen werden? Leer lassen heißt: gar kein " +
      "Dateizugriff. Es gibt bewusst keine Voreinstellung.",
    build: (s) =>
      field("Ordner, durch Doppelpunkt getrennt", "wiz-roots", {
        value: s.settings.file_roots.join(":"),
        placeholder: "/Users/du/Dokumente/Notizen",
      }),
    apply: async () => {
      const roots = $("#wiz-roots").value.split(":").map((p) => p.trim()).filter(Boolean);
      await saveSetup({ file_roots: roots });
      return roots.length ? `${roots.length} Ordner freigegeben.` : "Kein Dateizugriff.";
    },
  },
  {
    title: "Vorhandene Notizen",
    text:
      "Hast du schon eine Ablage? Icarus liest sie ein, ohne dass du sie " +
      "aufgeben musst. Alles landet als Rohmaterial — nichts davon gilt " +
      "sofort als gewusst.",
    build: (s) => {
      const frag = document.createDocumentFragment();
      const sel = document.createElement("label");
      sel.className = "field";
      sel.textContent = "Quelle";
      const select = document.createElement("select");
      select.id = "wiz-adapter";
      for (const [value, label] of Object.entries({
        obsidian: "Obsidian-Vault",
        notion: "Notion-Export",
        markdown: "Markdown-Ordner",
        dateien: "Textdateien",
      })) {
        const o = document.createElement("option");
        o.value = value;
        o.textContent = label;
        select.append(o);
      }
      sel.append(select);
      frag.append(
        sel,
        field("Ordner", "wiz-ingest", {
          placeholder: "muss oben freigegeben sein",
          hint: s.settings.file_roots.length
            ? `Freigegeben: ${s.settings.file_roots.join(", ")}`
            : "Noch kein Ordner freigegeben — dann diesen Schritt überspringen.",
        })
      );
      return frag;
    },
    apply: async () => {
      const path = $("#wiz-ingest").value.trim();
      if (!path) return "Übersprungen.";
      const report = await api("/ingest", {
        method: "POST",
        body: JSON.stringify({ path, adapter: $("#wiz-adapter").value }),
      });
      return `${report.recorded} aufgenommen, ${report.duplicates} schon bekannt, ${report.skipped} übersprungen.`;
    },
  },
  {
    title: "E-Mail und Kalender",
    text:
      "Kannst du auch später einrichten, unter „Einrichtung“. Mail ist der " +
      "gefährlichste Weg für untergeschobene Anweisungen — gelesene Nachrichten " +
      "gelten deshalb immer als fremder Inhalt und heben die Freigabestufe an.",
    build: () => document.createDocumentFragment(),
    apply: async () => {},
  },
];

let wizardStep = 0;

function renderWizard() {
  const step = WIZARD_STEPS[wizardStep];
  $("#wizard-title").textContent = step.title;
  $("#wizard-text").textContent = step.text;
  $("#wizard-body").replaceChildren(step.build(setupState));
  $("#wizard-result").textContent = "";
  $("#wizard-result").classList.remove("error");
  $("#wizard-progress").textContent = `Schritt ${wizardStep + 1} von ${WIZARD_STEPS.length}`;
  $("#wizard-next").textContent =
    wizardStep === WIZARD_STEPS.length - 1 ? "Fertig" : "Weiter";
}

async function advanceWizard(apply) {
  const next = $("#wizard-next");
  const skip = $("#wizard-skip");
  next.disabled = skip.disabled = true;

  if (apply) {
    try {
      const message = await WIZARD_STEPS[wizardStep].apply();
      if (message) {
        $("#wizard-result").textContent = message;
        $("#wizard-result").classList.remove("error");
      }
    } catch (err) {
      // Bei einem Fehler bleibt der Schritt stehen. Weiterzuspringen würde
      // heißen, dem Nutzer zu suggerieren, es habe geklappt.
      $("#wizard-result").textContent = err.message;
      $("#wizard-result").classList.add("error");
      next.disabled = skip.disabled = false;
      return;
    }
  }

  wizardStep += 1;
  next.disabled = skip.disabled = false;

  if (wizardStep >= WIZARD_STEPS.length) {
    await saveSetup({ onboarded: true });
    $("#wizard").hidden = true;
    await refreshStatus();
    await loadDashboard();
    return;
  }
  renderWizard();
}

$("#wizard-next").addEventListener("click", () => advanceWizard(true));
$("#wizard-skip").addEventListener("click", () => advanceWizard(false));

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

/** Zeigt oben, was gerade wirklich geht. Nach jeder Änderung neu. */
async function refreshStatus() {
  const health = await api("/health");

  const bits = [];
  bits.push(health.chat ? `Modell: ${health.model}` : "kein Modell");
  bits.push(health.semantic_search ? "semantische Suche" : "Textsuche");
  if (health.mail) bits.push("Mail");
  if (health.calendar) bits.push("Kalender");
  statusEl.textContent = bits.join(" · ");
  statusEl.classList.add("ready");

  // Lieber ehrlich sagen, was fehlt, als einen kaputten Chat anbieten.
  $("#chat-input").placeholder = health.chat
    ? "Schreib etwas…"
    : "Kein Modell eingerichtet — siehe Einrichtung";
  return health;
}

async function start() {
  const info = await invoke("sidecar_info");
  base = `http://127.0.0.1:${info.port}`;
  token = info.token;

  const health = await refreshStatus();
  setupState = await api("/setup");

  if (!setupState.settings.onboarded) {
    // Beim allerersten Start führt der Assistent. Danach nie wieder von
    // selbst — wer ihn noch einmal will, geht auf „Einrichtung“.
    wizardStep = 0;
    $("#wizard").hidden = false;
    renderWizard();
  } else if (!health.chat) {
    addMessage(
      "assistant",
      "Es ist kein Modell eingerichtet — Gespräche gehen noch nicht. " +
        "Das Gedächtnis funktioniert trotzdem: unter „Gedächtnis“ lassen sich " +
        "Aussagen speichern, ansehen und widerrufen, unter „Rohmaterial“ " +
        "vorhandene Notizen einlesen."
    );
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
