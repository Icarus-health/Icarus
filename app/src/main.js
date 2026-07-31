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

const REFRESH = { dashboard: loadDashboard, memory: loadMemory, audit: loadAudit, chat: loadApprovals };

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

async function loadDashboard() {
  $("#greeting").textContent = greeting();
  const data = await api("/dashboard");
  renderTasks(data.tasks);
  renderEvents(data.calendar);
  renderMail(data.mail);
  renderMemoryCard(data.memory);
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
