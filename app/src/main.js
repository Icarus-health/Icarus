// Icarus — Oberfläche.
//
// Bewusst ohne Framework. Der Zweck des Gerüsts ist zu zeigen, wie Gedächtnis,
// Freigaben und Protokoll zusammenspielen — nicht, wie man eine SPA baut.

// Läuft in zwei Umgebungen: in der Tauri-App und im Browser (Container).
// Der Unterschied ist genau eine Frage — woher kommen Adresse und Token?
//
//   Tauri:   die App hat beides erzeugt und reicht es über `sidecar_info`.
//   Browser: der Sidecar liefert diese Seite selbst aus, also ist die Adresse
//            der eigene Ursprung. Das Token steht beim Start in der Konsole
//            und kommt als `?token=` in die URL — derselbe Weg wie bei
//            Jupyter, und aus demselben Grund: Der Browser muss es kennen,
//            andere Prozesse auf dem Rechner sollen es nicht.
const tauri = window.__TAURI__?.core ?? null;

let base = null;
let token = null;

/** Holt Adresse und Token, je nachdem wo wir laufen. */
async function connectionInfo() {
  if (tauri) {
    const info = await tauri.invoke("sidecar_info");
    return { base: `http://127.0.0.1:${info.port}`, token: info.token };
  }

  const fromUrl = new URLSearchParams(window.location.search).get("token");
  if (fromUrl) {
    // Aus der Adresszeile entfernen, sobald es liegt: Ein Token im Verlauf
    // des Browsers und in jedem Screenshot ist unnötig.
    sessionStorage.setItem("icarus-token", fromUrl);
    history.replaceState(null, "", window.location.pathname);
  }
  return {
    base: window.location.origin,
    token: sessionStorage.getItem("icarus-token") ?? "",
  };
}

const $ = (sel) => document.querySelector(sel);
const statusEl = $("#status");

/** Schreibt in die Zustandsanzeige — und blendet sie dabei ein.
 *
 * Sie ist im Normalfall leer und versteckt. Wer sie nur beschreibt, ohne
 * `hidden` zu lösen, schickt seine Meldung ins Nichts. Deshalb geht jede
 * Meldung durch hier. */
function meldeZustand(text, art = "") {
  statusEl.classList.remove("ready", "error");
  if (art) statusEl.classList.add(art);
  statusEl.textContent = text;
  statusEl.hidden = !text;
}

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
  proposals: loadProposals,
  setup: loadSetup,
  memory: loadMemory,
  audit: loadAudit,
  chat: loadChat,
};

/** Gespräch: Freigaben und Posteingang. Der Posteingang darf dabei nicht das
 *  Gespräch mitreißen, wenn kein Mailkonto eingerichtet ist. */
async function loadChat() {
  await loadApprovals();
  await loadMailbox().catch(() => {});
}

// -- Suche ------------------------------------------------------------------
//
// Ein Feld für alles. Wer suchen will, muss nicht wissen, in welcher Schicht
// etwas liegt — das ist die Arbeit des Programms, nicht die des Nutzers.

let sucheTreffer = [];
let sucheGewaehlt = 0;
let sucheLauf = 0;

function sucheOeffnen() {
  $("#suche").hidden = false;
  const feld = $("#suche-feld");
  feld.value = "";
  feld.focus();
  sucheTreffer = [];
  sucheGewaehlt = 0;
  $("#suche-ergebnis").replaceChildren();
}

function sucheSchliessen() {
  $("#suche").hidden = true;
}

function sucheMarkieren() {
  document.querySelectorAll(".suche-treffer").forEach((el, i) => {
    el.classList.toggle("gewaehlt", i === sucheGewaehlt);
    if (i === sucheGewaehlt) el.scrollIntoView({ block: "nearest" });
  });
}

function sucheOeffneTreffer(treffer) {
  sucheSchliessen();
  // Kein Ziel heißt: dafür gibt es noch keine Ansicht. Dann passiert nichts,
  // statt irgendwohin zu springen.
  if (treffer?.ziel) openTab(treffer.ziel);
}

function sucheZeichnen(ergebnis) {
  const kasten = $("#suche-ergebnis");
  kasten.replaceChildren();
  sucheTreffer = [];
  sucheGewaehlt = 0;

  if (!ergebnis.gruppen.length) {
    const leer = document.createElement("p");
    leer.id = "suche-leer";
    leer.className = "muted";
    // Nichts gefunden ist ein Ergebnis, keine Panne — und es wird gesagt.
    leer.textContent = ergebnis.frage.length < 2
      ? "Tipp weiter — ab zwei Zeichen suche ich."
      : `Nichts gefunden zu „${ergebnis.frage}“.`;
    kasten.append(leer);
    return;
  }

  for (const gruppe of ergebnis.gruppen) {
    const titel = document.createElement("div");
    titel.className = "suche-gruppe";
    titel.textContent = gruppe.beschriftung;
    kasten.append(titel);

    for (const treffer of gruppe.treffer) {
      const knopf = document.createElement("button");
      knopf.type = "button";
      knopf.className = "suche-treffer";

      const zeile = document.createElement("span");
      zeile.className = "titel";
      if (treffer.fremd) {
        const marke = document.createElement("span");
        marke.className = "fremd";
        marke.textContent = "von außen";
        zeile.append(marke);
      }
      zeile.append(document.createTextNode(treffer.titel));
      knopf.append(zeile);

      if (treffer.zeile) {
        const unten = document.createElement("span");
        unten.className = "zeile";
        unten.textContent = treffer.zeile;
        knopf.append(unten);
      }

      knopf.addEventListener("click", () => sucheOeffneTreffer(treffer));
      kasten.append(knopf);
      sucheTreffer.push(treffer);
    }
  }

  sucheMarkieren();
}

$("#suche-feld").addEventListener("input", async (event) => {
  const frage = event.target.value;
  // Jede Eingabe bekommt eine Nummer: eine langsame Antwort auf eine alte
  // Frage darf eine schnellere auf eine neue nicht überschreiben.
  const meine = ++sucheLauf;
  try {
    const ergebnis = await api(`/suche?q=${encodeURIComponent(frage)}`);
    if (meine === sucheLauf) sucheZeichnen(ergebnis);
  } catch (err) {
    if (meine !== sucheLauf) return;
    const kasten = $("#suche-ergebnis");
    kasten.replaceChildren();
    const p = document.createElement("p");
    p.id = "suche-leer";
    p.className = "muted";
    p.style.color = "var(--danger)";
    p.textContent = err.message;
    kasten.append(p);
  }
});

document.addEventListener("keydown", (event) => {
  const offen = !$("#suche").hidden;

  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    offen ? sucheSchliessen() : sucheOeffnen();
    return;
  }

  if (!offen) return;

  if (event.key === "Escape") {
    event.preventDefault();
    sucheSchliessen();
  } else if (event.key === "ArrowDown" && sucheTreffer.length) {
    event.preventDefault();
    sucheGewaehlt = (sucheGewaehlt + 1) % sucheTreffer.length;
    sucheMarkieren();
  } else if (event.key === "ArrowUp" && sucheTreffer.length) {
    event.preventDefault();
    sucheGewaehlt = (sucheGewaehlt - 1 + sucheTreffer.length) % sucheTreffer.length;
    sucheMarkieren();
  } else if (event.key === "Enter" && sucheTreffer.length) {
    event.preventDefault();
    sucheOeffneTreffer(sucheTreffer[sucheGewaehlt]);
  }
});

$("#suche-knopf").addEventListener("click", sucheOeffnen);

// Ein Klick daneben schließt — wie bei jedem Blatt, das über etwas liegt.
$("#suche").addEventListener("click", (event) => {
  if (event.target.id === "suche") sucheSchliessen();
});

// Drei Orte, mehr Ansichten. Welche Ansicht zu welchem Ort gehört, steht
// hier — und nur hier. `null` heißt: erreichbar, aber kein eigener Ort.
const ORT = {
  dashboard: "dashboard",
  chat: "chat",
  projects: "projects",
  memory: "projects",
  proposals: "projects",
  ingest: "projects",
  audit: null,
  setup: null,
};

// Die Ablage ist der einzige Ort mit Fächern.
const ABLAGE = new Set(["projects", "memory", "proposals", "ingest"]);

// Einmal an einer Stelle, damit auch ein Knopf im Briefing umschalten kann.
function openTab(name) {
  const ansicht = $(`#view-${name}`);
  if (!ansicht) return;

  for (const view of document.querySelectorAll(".view")) view.hidden = true;
  ansicht.hidden = false;

  // Oben leuchtet der **Ort**, nicht die Ansicht: wer in „Was ich weiß“
  // steht, ist immer noch in der Ablage.
  const ort = ORT[name];
  document.querySelectorAll("nav .tab").forEach((t) => {
    t.classList.toggle("active", Boolean(ort) && t.dataset.view === ort);
  });

  const leiste = $("#ablage-leiste");
  if (leiste) {
    leiste.hidden = !ABLAGE.has(name);
    leiste.querySelectorAll(".fach").forEach((f) => {
      f.classList.toggle("active", f.dataset.view === name);
    });
  }

  mehrSchliessen();
  REFRESH[name]?.();
}

document.querySelectorAll("nav .tab[data-view]").forEach((tab) => {
  tab.addEventListener("click", () => openTab(tab.dataset.view));
});

document.querySelectorAll("#ablage-leiste .fach, #mehr-menue button").forEach((el) => {
  el.addEventListener("click", () => openTab(el.dataset.view));
});

// -- Das Zahnrad ------------------------------------------------------------

function mehrSchliessen() {
  const menue = $("#mehr-menue");
  if (!menue || menue.hidden) return;
  menue.hidden = true;
  $("#mehr-knopf")?.setAttribute("aria-expanded", "false");
}

$("#mehr-knopf")?.addEventListener("click", (event) => {
  event.stopPropagation();
  const menue = $("#mehr-menue");
  const offen = !menue.hidden;
  menue.hidden = offen;
  $("#mehr-knopf").setAttribute("aria-expanded", String(!offen));
});

// Ein Menü, das sich nur über seinen eigenen Knopf schließen lässt, ist eine
// Falle. Klick daneben und Esc schließen es auch.
document.addEventListener("click", mehrSchliessen);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") mehrSchliessen();
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
  // Diese Karte hat „Heute“ verlassen und einen eigenen Ort bekommen.
  // Fehlt sie, ist das kein Fehler — und kein Grund, die Seite
  // mittendrin abbrechen zu lassen.
  if (!$("#mail-list")) return;

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
  // Diese Karte hat „Heute“ verlassen und einen eigenen Ort bekommen.
  // Fehlt sie, ist das kein Fehler — und kein Grund, die Seite
  // mittendrin abbrechen zu lassen.
  if (!$("#memory-list")) return;

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
  // Diese Karte hat „Heute“ verlassen und einen eigenen Ort bekommen.
  // Fehlt sie, ist das kein Fehler — und kein Grund, die Seite
  // mittendrin abbrechen zu lassen.
  if (!$("#project-teaser")) return;

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

// Je Quelle die Handlung, die der Knopf wirklich auslöst. Fehlt hier ein
// Eintrag oder die Kennung, bekommt der Punkt **keinen** Knopf: ein Knopf,
// der nichts tut, ist schlimmer als keiner.
const BRIEFING_TATEN = {
  aufgabe: (ref) => api(`/tasks/${ref}/done`, { method: "POST" }),
  bestaetigung: (ref) => api(`/proposals/${ref}/accept`, { method: "POST" }),
};

// Punkte ohne eigene Handlung führen dorthin, wo man sie erledigt.
const BRIEFING_ZIELE = {
  widerspruch: "proposals",
  vorschlag: "proposals",
  mail: "chat",
  termin: null,
};

function renderBriefing(briefing) {
  const kasten = $("#briefing");
  const liste = $("#briefing-punkte");
  liste.replaceChildren();

  if (!briefing) {
    // Kein Urteil zu haben ist etwas anderes, als ein leeres zu zeigen.
    kasten.hidden = true;
    return;
  }

  kasten.hidden = false;
  $("#briefing-intro").textContent = briefing.einleitung ?? "";

  for (const punkt of briefing.punkte ?? []) {
    const li = document.createElement("li");

    const satz = document.createElement("p");
    satz.className = "satz";
    satz.textContent = punkt.text;
    li.append(satz);

    const tat = BRIEFING_TATEN[punkt.quelle];
    const ziel = BRIEFING_ZIELE[punkt.quelle];

    if (punkt.aktion && punkt.ref && tat) {
      const knopf = document.createElement("button");
      knopf.className = "tat small";
      knopf.type = "button";
      knopf.textContent = punkt.aktion;
      knopf.addEventListener("click", async () => {
        knopf.disabled = true;
        try {
          await tat(punkt.ref);
          await loadDashboard();
        } catch (err) {
          // Jede Aktion antwortet — mit Ergebnis oder mit Grund.
          knopf.disabled = false;
          satz.after(fehlerzeile(err.message));
        }
      });
      li.append(knopf);
    } else if (punkt.aktion && ziel) {
      const knopf = document.createElement("button");
      knopf.className = "tat small ghost";
      knopf.type = "button";
      knopf.textContent = punkt.aktion;
      knopf.addEventListener("click", () => openTab(ziel));
      li.append(knopf);
    }

    liste.append(li);
  }

  const nachsatz = $("#briefing-nachsatz");
  nachsatz.textContent = briefing.nachsatz ?? "";
  nachsatz.hidden = !briefing.nachsatz;
}

function fehlerzeile(text) {
  const p = document.createElement("p");
  p.className = "meta";
  p.style.color = "var(--danger)";
  p.textContent = text;
  return p;
}

async function loadDashboard() {
  $("#greeting").textContent = greeting();
  const data = await api("/dashboard");
  renderBriefing(data.briefing);
  renderProjectTeaser(data.projects);
  renderTasks(data.tasks);
  renderEvents(data.calendar);
  renderMail(data.mail);
  renderMemoryCard(data.memory);
  setPendingBadge(data.episodes?.pending);
  setProposalsBadge(data.proposals?.pending);
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

// -- Posteingang ------------------------------------------------------------
//
// Mail dort, wo man ohnehin schreibt. Die Sicherheitsregel bleibt unangetastet
// und ist hier die wichtigste im ganzen System: **Jeder kann dir eine Mail
// schreiben.** Der Text wird deshalb sichtbar als fremd gerahmt, und der
// Sendeknopf ist kein zweiter Weg an der Freigabe vorbei — er ist derselbe Weg,
// nur kürzer: Antwort tippen, senden, und die Freigabe mit vollem Trockenlauf
// steht direkt darüber.

let mailboxState = { items: [], can_send: false };

async function loadMailbox() {
  const note = $("#mailbox-note");
  const liste = $("#mailbox-list");
  note.classList.remove("error");

  try {
    mailboxState = await api("/mail");
  } catch (err) {
    liste.replaceChildren();
    note.textContent = err.message;
    note.classList.add("error");
    setMailboxBadge(0);
    return;
  }

  setMailboxBadge(mailboxState.unread);
  note.textContent = mailboxState.items.length
    ? (mailboxState.can_send ? "" : "Kein SMTP eingerichtet — Lesen geht, Senden nicht.")
    : "Nichts im Posteingang.";

  liste.replaceChildren(...mailboxState.items.map(renderMailItem));
}

function setMailboxBadge(count) {
  const badge = $("#mailbox-badge");
  badge.hidden = !count;
  badge.textContent = `${count ?? 0} ungelesen`;
}

function renderMailItem(m) {
  const el = document.createElement("li");
  if (m.unread) el.classList.add("unread");

  const kopf = document.createElement("p");
  kopf.className = "statement";
  kopf.textContent = m.subject || "(kein Betreff)";

  const meta = document.createElement("p");
  meta.className = "meta";
  const wann = m.date
    ? new Date(m.date).toLocaleString("de-DE",
        { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })
    : "";
  meta.textContent = [m.from, wann].filter(Boolean).join(" · ");

  const auf = document.createElement("details");
  const zeile = document.createElement("summary");
  zeile.textContent = "Öffnen";
  auf.append(zeile);

  const inhalt = document.createElement("div");
  auf.append(inhalt);
  let geladen = false;
  auf.addEventListener("toggle", async () => {
    if (!auf.open || geladen) return;
    geladen = true;
    inhalt.replaceChildren(meldung("Wird geladen…"));
    try {
      inhalt.replaceChildren(...(await mailBody(m)));
    } catch (err) {
      geladen = false;   // Ein Netzwerkfehler darf den Knopf nicht verbrauchen.
      inhalt.replaceChildren(meldung(err.message, true));
    }
  });

  el.append(kopf, meta, auf);
  return el;
}

function meldung(text, fehler = false) {
  const p = document.createElement("p");
  p.className = fehler ? "meta error" : "meta";
  p.textContent = text;
  return p;
}

/** Baut die geöffnete Nachricht: Wortlaut, Antwortfeld, „Ins Gedächtnis". */
async function mailBody(m) {
  const voll = await api(`/mail/${encodeURIComponent(m.uid)}`);
  const teile = [];

  // Der Wortlaut steht monospaced und gerahmt, wie jeder fremde Inhalt: Er
  // darf nie so aussehen, als hätte Icarus ihn geschrieben.
  const text = document.createElement("pre");
  text.className = "dry-run";
  text.textContent = voll.body || voll.preview || "(kein Text)";
  teile.push(text);

  const antwort = document.createElement("textarea");
  antwort.rows = 4;
  antwort.className = "reply";
  antwort.placeholder = mailboxState.can_send
    ? `Antwort an ${voll.answer_to}…`
    : "Kein SMTP eingerichtet — Senden geht nicht.";
  antwort.disabled = !mailboxState.can_send;

  const ergebnis = meldung("");

  const senden = document.createElement("button");
  senden.type = "button";
  senden.textContent = "Senden";
  senden.disabled = !mailboxState.can_send;
  senden.addEventListener("click", async () => {
    const body = antwort.value.trim();
    if (!body) {
      ergebnis.textContent = "Die Antwort ist leer.";
      ergebnis.classList.add("error");
      return;
    }
    senden.disabled = true;
    ergebnis.classList.remove("error");
    ergebnis.textContent = "Wird vorgelegt…";
    try {
      // Über dasselbe Werkzeug wie das Modell — also durch Policy, Freigabe
      // und Protokoll. Nichts geht hier direkt hinaus.
      await api("/tools/mail_senden", {
        method: "POST",
        body: JSON.stringify({
          to: voll.answer_to,
          subject: betreffFuerAntwort(voll.subject),
          body,
          in_reply_to: voll.message_id,
        }),
      });
      ergebnis.textContent =
        "Liegt als Freigabe oben — dort steht, was genau hinausgeht.";
      await loadApprovals();
      $("#approvals").scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (err) {
      ergebnis.textContent = err.message;
      ergebnis.classList.add("error");
    } finally {
      senden.disabled = !mailboxState.can_send;
    }
  });

  const merken = document.createElement("button");
  merken.type = "button";
  merken.className = "ghost small";
  merken.textContent = "Ins Gedächtnis";
  merken.addEventListener("click", async () => {
    merken.disabled = true;
    try {
      const { new: neu } = await api(
        `/mail/${encodeURIComponent(m.uid)}/remember`, { method: "POST" }
      );
      // Genau sagen, was passiert ist. „Gemerkt" wäre falsch: Es liegt als
      // Rohmaterial, nicht als Wissen über dich.
      ergebnis.classList.remove("error");
      ergebnis.textContent = neu
        ? "Als Rohmaterial aufgenommen. Nichts davon gilt als gewusst."
        : "Lag schon als Rohmaterial vor.";
      await loadEpisodes().catch(() => {});
    } catch (err) {
      ergebnis.textContent = err.message;
      ergebnis.classList.add("error");
      merken.disabled = false;
    }
  });

  const reihe = document.createElement("div");
  reihe.className = "row";
  reihe.append(senden, merken);

  teile.push(antwort, reihe, ergebnis);
  return teile;
}

/** „Re: Betreff" — aber nicht „Re: Re: Re:". */
function betreffFuerAntwort(betreff) {
  const rein = (betreff || "").trim();
  if (!rein) return "Re:";
  return /^(re|aw|antw)\s*:/i.test(rein) ? rein : `Re: ${rein}`;
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
  summary: "Rückblick",
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
  await fillIngestFolders();
  await renderSummaries();

  const list = $("#episode-list");
  list.replaceChildren();
  $("#episode-empty").hidden = episodes.some((e) => e.kind !== "summary");

  for (const e of episodes) {
    // Rückblicke stehen oben in ihrem eigenen Block. Sie hier ein zweites Mal
    // zu zeigen, stellte sie neben das Rohmaterial, aus dem sie stammen — und
    // genau diese Verwechslung soll die Trennung verhindern.
    if (e.kind === "summary") continue;

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

// -- Rückblicke -------------------------------------------------------------
//
// Sie stehen über dem Rohmaterial, nicht darin. Ein Rückblick ist die Antwort
// auf „was war im April", und dafür taugt er nur, wenn man ihn nicht zwischen
// vierzig Einzelnotizen suchen muss.
//
// Jeder trägt einen Knopf zum Zurücknehmen. Ohne den wäre das Zusammenfassen
// eine Einbahnstraße: Ein Monat, den ein Modell falsch gelesen hat, wäre
// faktisch ersetzt, und niemand käme mehr an die Übersicht heran.

async function renderSummaries() {
  const daten = await api("/summaries");
  const block = $("#summary-block");
  block.replaceChildren();

  const note = $("#summary-note");
  note.classList.remove("error");

  if (daten.candidates.length) {
    const wie_viele = daten.candidates.reduce((n, k) => n + k.count, 0);
    const zeitraeume = daten.candidates.length === 1
      ? "einem Zeitraum" : `${daten.candidates.length} Zeiträumen`;
    note.textContent =
      `${wie_viele} ältere Episoden in ${zeitraeume} könnten zu Rückblicken ` +
      "werden. Die Quellen bleiben dabei erhalten.";
    const knopf = document.createElement("button");
    knopf.type = "button";
    knopf.className = "small";
    knopf.textContent = "Zusammenfassen";
    knopf.addEventListener("click", async () => {
      knopf.disabled = true;
      note.textContent = "Läuft…";
      try {
        const report = await api("/summaries/run", {
          method: "POST", body: JSON.stringify({}),
        });
        // Erst neu zeichnen, dann melden. Umgekehrt überschreibt das Neuzeichnen
        // die Antwort, und ein Lauf ohne Modell sähe aus, als sei nichts
        // passiert — der Nutzer drückt dann noch dreimal.
        await loadEpisodes();
        note.textContent = report.summary;
        note.classList.toggle("error", Boolean(report.errors.length));
      } catch (err) {
        note.textContent = err.message;
        note.classList.add("error");
        knopf.disabled = false;
      }
    });
    block.append(knopf);
  } else {
    note.textContent = "";
  }

  for (const s of daten.items) {
    const el = document.createElement("li");
    el.className = "episode summary";

    const title = document.createElement("p");
    title.className = "statement";
    title.textContent = s.title;

    const meta = document.createElement("p");
    meta.className = "meta inferred";
    meta.textContent =
      `${s.period} · aus ${s.covers.length} Episoden` +
      (s.provenance.extracted_by ? ` · ${s.provenance.extracted_by}` : "");

    const text = document.createElement("p");
    text.className = "statement";
    text.textContent = s.body;

    const zurueck = document.createElement("button");
    zurueck.className = "ghost small";
    zurueck.textContent = "Zurücknehmen";
    zurueck.addEventListener("click", async () => {
      zurueck.disabled = true;
      await api(`/summaries/${s.id}`, { method: "DELETE" });
      await loadEpisodes();
    });

    el.append(title, meta, text, zurueck);
    block.append(el);
  }
}

/** Füllt die Ordnerauswahl aus dem, was schon freigegeben ist.
 *
 *  Den Pfad hier erneut abzufragen wäre dasselbe zweimal — und beim zweiten Mal
 *  mit größerer Chance auf einen Tippfehler. Ist nichts freigegeben, sagt das
 *  Feld das, statt den Nutzer in einen Fehler laufen zu lassen.
 */
async function fillIngestFolders() {
  const { file_roots: roots } = await api("/ingest/adapters");
  const auswahl = $("#ingest-path");
  const vorher = auswahl.value;
  auswahl.replaceChildren();

  const knopf = $("#ingest-form").querySelector("button");
  if (!roots.length) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "Erst unter Einrichtung einen Ordner freigeben";
    auswahl.append(opt);
    auswahl.disabled = true;
    knopf.disabled = true;
    return;
  }

  auswahl.disabled = false;
  knopf.disabled = false;
  for (const pfad of roots) {
    const opt = document.createElement("option");
    opt.value = pfad;
    opt.textContent = pfad;
    auswahl.append(opt);
  }
  if (roots.includes(vorher)) auswahl.value = vorher;
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
    note.replaceChildren(document.createTextNode(
      bits.join(", ") + ". Nichts davon gilt als gewusst — der Bestand ist unberührt."
    ));

    // Warum etwas übersprungen wurde, gehört sichtbar hierher. „5
    // übersprungen" allein lässt jemanden glauben, sein Vault sei vollständig
    // drin — und die fehlenden fünf sind womöglich die langen, wichtigen.
    if (report.skipped_reasons?.length) {
      const warum = document.createElement("details");
      const kopf = document.createElement("summary");
      kopf.textContent = "Was übersprungen wurde";
      const liste = document.createElement("ul");
      for (const grund of report.skipped_reasons) {
        const zeile = document.createElement("li");
        zeile.className = "meta";
        zeile.textContent = grund;
        liste.append(zeile);
      }
      if (report.skipped > report.skipped_reasons.length) {
        liste.append(meldung(
          `… und ${report.skipped - report.skipped_reasons.length} weitere.`
        ));
      }
      warum.append(kopf, liste);
      note.append(warum);
    }

    await loadEpisodes();
  } catch (err) {
    note.textContent = err.message;
    note.classList.add("error");
  } finally {
    button.disabled = false;
  }
});

// -- Vorschläge ---------------------------------------------------------------
//
// Das Scharnier zwischen Rohmaterial und Bestand (docs/08-gedaechtnisschichten.md).
// Die eine Regel, die diese Ansicht tragen muss: Verdichtung schlägt vor, sie
// schreibt nicht. Deshalb gibt es hier zwei Knöpfe statt eines Automatismus,
// und der Beleg steht immer daneben, nie nur in einem Log.

const PROPOSAL_KIND_LABELS = {
  assertion: "Neue Aussage über dich",
  confirmation: "Gilt das noch?",
  conflict: "Widersprechen sich diese?",
};

// Dieselben Bezeichnungen wie im Formular unter „Was ich weiß“ — eine Art heißt
// überall gleich, ob die Aussage schon im Bestand steht oder erst vorgeschlagen wird.
const ASSERTION_KIND_LABELS = {
  state: "Zustand",
  identity: "Identität",
  preference: "Vorliebe",
  goal: "Ziel",
  constraint: "Grenze",
  // Über die Auswahl im Formular nicht erreichbar, aus einer Verdichtung aber
  // sehr wohl. Ohne Eintrag stünde hier sonst das englische Rohwort.
  episode: "Begebenheit",
  relationship: "Beziehung",
  skill: "Fähigkeit",
};

// Je Art eine passende Beschriftung: „Übernehmen“ träfe eine Bestätigung nicht,
// und bei einem Konflikt wäre es irreführend, weil Zustimmung nichts auflöst.
const PROPOSAL_ACTIONS = {
  assertion: { accept: "Übernehmen", reject: "Verwerfen" },
  confirmation: { accept: "Gilt noch", reject: "Stimmt nicht mehr" },
  conflict: { accept: "Als strittig markieren", reject: "Ignorieren" },
};

function setProposalsBadge(count) {
  const badge = $("#proposals-badge");
  badge.hidden = !count;
  badge.textContent = String(count ?? 0);
}

/** „regel/…“ oder „modell/…“ — wer den Vorschlag gemacht hat, bleibt sichtbar. */
function proposedByLabel(who) {
  if (!who) return "Herkunft unbekannt";
  const [art, rest] = who.split("/", 2);
  if (art === "modell") return `Modell: ${rest || "?"}`;
  if (art === "regel") return `Regel: ${rest || "?"}`;
  return who;
}

/** Eine Karte je Vorschlag — im Aufbau nah an renderApproval: der Beleg voll
    sichtbar, zwei Knöpfe, ein Fehler bleibt an der Karte statt zu verschwinden. */
function renderProposal(p) {
  const card = document.createElement("li");
  card.className = `proposal ${p.kind}`;

  const head = document.createElement("h3");
  head.textContent = PROPOSAL_KIND_LABELS[p.kind] ?? p.kind;
  card.append(head);

  if (p.kind === "conflict") {
    // Zwei Zeilen, nicht eine: sonst liest sich der Vorschlag wie eine
    // einzelne Aussage statt wie ein möglicher Widerspruch zwischen zweien.
    const [links, rechts] = p.statement.split(/\s*⟷\s*/);
    const l1 = document.createElement("p");
    l1.className = "statement";
    l1.textContent = links ?? p.statement;
    const l2 = document.createElement("p");
    l2.className = "statement";
    l2.textContent = rechts ?? "";
    card.append(l1, l2);

    const hint = document.createElement("p");
    hint.className = "meta";
    hint.textContent =
      "Zustimmung markiert beide Seiten als strittig — sie löst den Widerspruch nicht auf.";
    card.append(hint);
  } else {
    const statement = document.createElement("p");
    statement.className = "statement";
    statement.textContent = p.statement;
    card.append(statement);

    if (p.kind === "assertion") {
      const meta = document.createElement("p");
      meta.className = "meta";
      const bits = [ASSERTION_KIND_LABELS[p.assertion_kind] ?? p.assertion_kind];
      if (typeof p.confidence === "number") bits.push(`${Math.round(p.confidence * 100)}% Zuversicht`);
      meta.textContent = bits.join(" · ");
      card.append(meta);
    }
  }

  const rationale = document.createElement("p");
  rationale.className = "muted";
  rationale.textContent = p.rationale;
  card.append(rationale);

  // Der Beleg ist bei einer neuen Aussage Pflicht: ohne das wörtliche Zitat
  // ließe sich nicht prüfen, worauf der Vorschlag beruht — die ganze Schicht
  // wäre sonst eine Blackbox (docs/08-gedaechtnisschichten.md).
  if (p.evidence?.length) {
    const details = document.createElement("details");
    details.className = "evidence";
    const summary = document.createElement("summary");
    summary.textContent = p.evidence.length > 1 ? "Belege" : "Beleg";
    details.append(summary);
    for (const ev of p.evidence) {
      const pre = document.createElement("pre");
      pre.className = "dry-run";
      pre.textContent = ev.quote || "(kein Zitat hinterlegt)";
      details.append(pre);
    }
    card.append(details);
  } else if (p.kind === "assertion") {
    // Sollte nie vorkommen — der Sidecar weist Aussagen ohne Beleg ab. Wenn
    // doch eine ankommt, muss sie auffallen statt normal auszusehen: Ein
    // unbelegter Vorschlag, der sich nicht von einem belegten unterscheidet,
    // hebt genau die Prüfbarkeit auf, für die diese Ansicht existiert.
    const warnung = document.createElement("p");
    warnung.className = "meta error";
    warnung.textContent =
      "Ohne Beleg. Nicht nachprüfbar — im Zweifel verwerfen.";
    card.append(warnung);
  }

  const proposer = document.createElement("p");
  proposer.className = "meta";
  proposer.textContent = proposedByLabel(p.proposed_by);
  card.append(proposer);

  const actions = document.createElement("div");
  actions.className = "row";
  const labels = PROPOSAL_ACTIONS[p.kind] ?? { accept: "Übernehmen", reject: "Verwerfen" };

  const accept = document.createElement("button");
  accept.type = "button";
  accept.textContent = labels.accept;

  const reject = document.createElement("button");
  reject.type = "button";
  reject.className = "ghost";
  reject.textContent = labels.reject;

  actions.append(accept, reject);
  card.append(actions);

  const decide = async (endpoint) => {
    accept.disabled = reject.disabled = true;
    try {
      const result = await api(`/proposals/${p.id}/${endpoint}`, { method: "POST" });
      card.remove();
      await loadProposalCounts();
      $("#proposal-empty").hidden = $("#proposal-list").children.length > 0;
      if (endpoint === "accept" && p.kind === "assertion" && result?.assertion) {
        $("#proposal-feedback").textContent =
          `Jetzt im Gedächtnis: „${result.assertion.statement}“`;
      }
    } catch (err) {
      accept.disabled = reject.disabled = false;
      const error = document.createElement("p");
      error.className = "error";
      error.textContent = err.message;
      card.append(error);
    }
  };

  accept.addEventListener("click", () => decide("accept"));
  reject.addEventListener("click", () => decide("reject"));

  return card;
}

async function loadProposalCounts() {
  const counts = await api("/proposals/counts");
  setProposalsBadge(counts.pending);
  return counts;
}

async function loadProposals() {
  const [proposals] = await Promise.all([api("/proposals?limit=200"), loadProposalCounts()]);

  const list = $("#proposal-list");
  list.replaceChildren();
  $("#proposal-empty").hidden = proposals.length > 0;
  $("#proposal-feedback").textContent = "";

  for (const p of proposals) list.append(renderProposal(p));
}

$("#consolidate-btn").addEventListener("click", async () => {
  const button = $("#consolidate-btn");
  const note = $("#consolidate-note");
  button.disabled = true;
  note.classList.remove("error");
  // Ein Modelllauf über mehrere Episoden kann spürbar dauern — lieber das
  // sagen, als den Knopf kommentarlos stehen zu lassen.
  note.textContent = "Läuft — kann bei vielen Episoden etwas dauern…";

  try {
    const report = await api("/consolidate", {
      method: "POST",
      body: JSON.stringify({ limit: 20, with_model: true }),
    });
    note.textContent = report.summary;
    await loadProposals();
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
  // -- Ordnerzugriff
  //
  // Vorher ein Feld mit Doppelpunkt-Syntax. Pfade tippt niemand fehlerfrei, und
  // die Syntax muss man auch noch wissen. Jetzt: eine Liste, ein Ordner pro
  // Zeile — und jeder wird beim Hinzufügen geprüft, statt drei Bildschirme
  // später an einer Fehlermeldung zu scheitern, die den Tippfehler nicht nennt.
  const ordner = document.createElement("section");
  ordner.className = "setup-block";
  const oh = document.createElement("h3");
  oh.textContent = "Ordnerzugriff";
  const onote = document.createElement("p");
  onote.className = "muted";
  onote.textContent =
    "Aus diesen Ordnern darf Icarus lesen — und nur aus diesen. Es gibt " +
    "bewusst keinen Vorgabewert: ein voreingestelltes Home-Verzeichnis wäre " +
    "die Bequemlichkeit, die den Schutz aufhebt.";

  const oliste = document.createElement("ul");
  oliste.className = "roots";
  const oresult = document.createElement("p");
  oresult.className = "meta";

  async function speichereOrdner(pfade) {
    await saveSetup({ file_roots: pfade });
    await loadSetup();
  }

  for (const pfad of s.file_roots) {
    const zeile = document.createElement("li");
    const name = document.createElement("p");
    name.className = "statement";
    name.textContent = pfad;

    const weg = document.createElement("button");
    weg.className = "ghost small";
    weg.textContent = "Entfernen";
    weg.addEventListener("click", async () => {
      weg.disabled = true;
      await speichereOrdner(s.file_roots.filter((x) => x !== pfad));
    });

    zeile.append(name, weg);
    oliste.append(zeile);
  }
  if (!s.file_roots.length) {
    const leer = document.createElement("p");
    leer.className = "meta";
    leer.textContent = "Noch kein Ordner freigegeben — Icarus liest keine Dateien.";
    oliste.append(leer);
  }

  const oinput = field("Ordner hinzufügen", "setup-root-neu", {
    placeholder: "/Users/du/Dokumente/Notizen",
  });
  const oadd = document.createElement("button");
  oadd.type = "button";
  oadd.textContent = "Prüfen und freigeben";
  oadd.addEventListener("click", async () => {
    const pfad = $("#setup-root-neu").value.trim();
    if (!pfad) return;
    oadd.disabled = true;
    oresult.classList.remove("error");
    oresult.textContent = "Wird geprüft…";
    try {
      // Erst nachsehen, dann freigeben. Ein Ordner, den es nicht gibt, ist
      // fast immer ein Tippfehler — und den zeigt man am besten sofort.
      const pruef = await api(`/setup/folder?path=${encodeURIComponent(pfad)}`);
      if (!pruef.ok) {
        oresult.textContent = pruef.detail;
        oresult.classList.add("error");
        oadd.disabled = false;
        return;
      }
      oresult.textContent = pruef.detail;
      await speichereOrdner([...s.file_roots, pruef.path]);
    } catch (err) {
      oresult.textContent = err.message;
      oresult.classList.add("error");
      oadd.disabled = false;
    }
  });

  ordner.append(oh, onote, oliste, oinput, oadd, oresult);
  panel.append(ordner);

  // -- Mail
  //
  // Vorher standen hier sieben Felder, von denen vier Wissen voraussetzten, das
  // außerhalb der IT niemand hat. Jetzt: Adresse eintippen, Passwort, fertig.
  // Die Serverangaben stehen darunter eingeklappt, für eigene Domains — das ist
  // genau die Gruppe, die sie auch kennt.
  const mail = document.createElement("section");
  mail.className = "setup-block";
  const mah = document.createElement("h3");
  mah.textContent = "E-Mail";
  const manote = document.createElement("p");
  manote.className = "muted";
  manote.textContent =
    "Trag deine Adresse ein — den Rest sucht Icarus. Gelesene Nachrichten " +
    "gelten immer als fremder Inhalt.";
  const maresult = document.createElement("p");
  maresult.className = "meta";

  // Der Hinweis auf ein nötiges App-Passwort. Er steht hier, bevor jemand das
  // erste Mal prüft — wer ihn nicht kennt, tippt sonst dreimal sein richtiges
  // Kennwort ein, bekommt dreimal „Anmeldung fehlgeschlagen“ und hält das
  // Programm für kaputt.
  const mahint = document.createElement("p");
  mahint.className = "meta";

  const adresse = field("Deine E-Mail-Adresse", "mail-user", {
    value: s.mail.user, placeholder: "du@beispiel.de",
  });

  const server = document.createElement("details");
  const serverSummary = document.createElement("summary");
  serverSummary.textContent = "Serverangaben";
  server.append(
    serverSummary,
    field("IMAP-Server", "mail-imap", { value: s.mail.imap_host, placeholder: "imap.beispiel.de" }),
    field("IMAP-Port", "mail-imap-port", { value: s.mail.imap_port }),
    field("SMTP-Server", "mail-smtp", { value: s.mail.smtp_host, placeholder: "leer lassen: nur lesen" }),
    field("SMTP-Port", "mail-smtp-port", { value: s.mail.smtp_port }),
    field("Absender", "mail-from", { value: s.mail.sender, hint: "Leer: die Adresse oben." })
  );

  // Was Icarus selbst eingetragen hat. Nur das darf es auch wieder ändern
  // oder leeren — was der Nutzer getippt hat, bleibt stehen. Ohne diese
  // Unterscheidung stünde nach einem Wechsel von @gmx.de auf die eigene Domain
  // weiter imap.gmx.net da, und das Prüfen scheiterte an einer Angabe, die
  // niemand eingetragen hat und deshalb auch niemand verdächtigt.
  let selbstEingetragen = "";

  /** Trägt die Serverangaben eines Anbieters ein und zeigt seinen Hinweis. */
  function applyMailProvider(p) {
    mahint.replaceChildren();
    mahint.classList.remove("error");
    const jetzt = $("#mail-imap").value.trim();
    const unseres = !jetzt || jetzt === selbstEingetragen;

    if (!p) {
      if (!unseres) return;   // Handgetragenes bleibt unangetastet.
      // Eigene Domain: aufräumen und aufklappen, statt den Nutzer raten zu
      // lassen, warum das Prüfen scheitert.
      $("#mail-imap").value = "";
      $("#mail-smtp").value = "";
      selbstEingetragen = "";
      server.open = true;
      mahint.textContent =
        "Diesen Anbieter kennt Icarus nicht — trag die Serverangaben unten ein.";
      return;
    }

    if (!unseres) {
      // Der Nutzer hat eigene Angaben gemacht. Der Hinweis darf trotzdem
      // kommen, die Felder nicht überschrieben werden.
      selbstEingetragen = "";
    } else {
      $("#mail-imap").value = p.imap_host;
      $("#mail-imap-port").value = p.imap_port;
      $("#mail-smtp").value = p.smtp_host;
      $("#mail-smtp-port").value = p.smtp_port;
      selbstEingetragen = p.imap_host;
    }
    if (p.hint) {
      mahint.append(document.createTextNode(p.hint + " "));
      if (p.help_url) {
        const a = document.createElement("a");
        a.href = p.help_url;
        a.target = "_blank";
        a.rel = "noreferrer";
        a.textContent = "Dort einrichten";
        mahint.append(a);
      }
    }
  }

  const maprovider = document.createElement("label");
  maprovider.className = "field";
  const maproviderText = document.createElement("span");
  maproviderText.textContent = "Anbieter";
  const maselect = document.createElement("select");
  maselect.id = "mail-provider";
  for (const p of [{ id: "", label: "Aus der Adresse erkennen" },
                   ...(setupState.mail_providers ?? []),
                   { id: "eigen", label: "Anderer Anbieter" }]) {
    const opt = document.createElement("option");
    opt.value = p.id;
    opt.textContent = p.label;
    maselect.append(opt);
  }
  maselect.addEventListener("change", () => {
    const gewaehlt = (setupState.mail_providers ?? [])
      .find((p) => p.id === maselect.value);
    if (maselect.value === "eigen") {
      server.open = true;
      mahint.replaceChildren();
      return;
    }
    if (gewaehlt) {
      // Von Hand gewählt ist eine ausdrückliche Ansage: Dann überschreiben wir
      // auch, was schon dasteht.
      selbstEingetragen = $("#mail-imap").value.trim();
      applyMailProvider(gewaehlt);
    }
    else void erkenneAnbieter();
  });
  maprovider.append(maproviderText, maselect);

  /** Fragt den Sidecar, welcher Anbieter zu der Adresse gehört. */
  async function erkenneAnbieter() {
    const adr = $("#mail-user").value.trim();
    if (!adr.includes("@")) return;
    const { provider } = await api(
      `/setup/mail-provider?address=${encodeURIComponent(adr)}`
    );
    if (provider) maselect.value = provider.id;
    applyMailProvider(provider);
    // Der Kalender hängt am selben Anbieter. Ihn getrennt zu erfragen wäre
    // dieselbe Entscheidung zweimal. (Funktionsdeklaration weiter unten im
    // selben Gültigkeitsbereich — sie ist hier schon sichtbar.)
    applyCalendarProvider(provider);
  }

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
    mah, manote, adresse,
    field("Passwort", "mail-pass", {
      type: "password",
      placeholder: setupState.secrets.ICARUS_MAIL_PASSWORD ? "hinterlegt" : "App-Passwort",
    }),
    maprovider, mahint, server, marow, maresult
  );
  panel.append(mail);

  // Erst nach dem Anhängen: `applyMailProvider` greift über `$()` ins Dokument.
  $("#mail-user").addEventListener("blur", erkenneAnbieter);
  if (s.mail.user && !s.mail.imap_host) void erkenneAnbieter();

  // -- Kalender
  const kal = document.createElement("section");
  kal.className = "setup-block";
  const kh = document.createElement("h3");
  kh.textContent = "Kalender";
  const knote = document.createElement("p");
  knote.className = "muted";
  knote.textContent =
    "Wird aus deinem Mailanbieter oben abgeleitet. Termine ohne Gäste bleiben " +
    "lokal; sobald jemand eingeladen wird, " +
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
  const khint = document.createElement("p");
  khint.className = "meta";
  khint.id = "cal-hint";
  kal.insertBefore(khint, kal.querySelector(".row"));
  panel.append(kal);

  /** Überträgt, was am Mailanbieter über den Kalender bekannt ist. */
  function applyCalendarProvider(anbieter) {
    khint.replaceChildren();
    khint.classList.remove("error");
    if (!anbieter) return;
    if (anbieter.caldav_note) {
      // Ehrlich sagen, dass es nicht geht, statt den Nutzer eine Viertelstunde
      // suchen zu lassen, bevor er annimmt, das Programm könne es nicht.
      khint.textContent = anbieter.caldav_note;
      return;
    }
    if (anbieter.caldav_url && !$("#cal-url").value.trim()) {
      $("#cal-url").value = anbieter.caldav_url;
      khint.textContent =
        `Adresse von ${anbieter.label} übernommen. Benutzer und Passwort sind ` +
        "dieselben wie bei der Mail.";
    }
  }

  // Der Anbieter ist schon erkannt — ihn für den Kalender erneut abzufragen
  // wäre dieselbe Entscheidung ein zweites Mal.
  const bekannt = (setupState.mail_providers ?? [])
    .find((x) => x.id === maselect.value);
  if (bekannt) applyCalendarProvider(bekannt);
  maselect.addEventListener("change", () => {
    applyCalendarProvider(
      (setupState.mail_providers ?? []).find((x) => x.id === maselect.value)
    );
  });

  await renderSchedule(panel);
  await renderBackups(panel);
}

// -- Der mitlaufende Prozess ------------------------------------------------
//
// Er macht die Vorschlagsschlange voller, nie den Bestand. Deshalb ist er
// unbedenklich — im schlimmsten Fall entsteht Arbeit, die man ignoriert.
// Standardmäßig aus, und die Modellnutzung darin noch einmal getrennt: Ein
// Zeitplan, der ungefragt einen Anbieter ruft, gibt fremdes Geld aus.

function checkbox(label, id, checked, hint) {
  const wrap = document.createElement("label");
  wrap.className = "field check";
  const input = document.createElement("input");
  input.type = "checkbox";
  input.id = id;
  input.checked = Boolean(checked);
  const text = document.createElement("span");
  text.textContent = label;
  wrap.append(input, text);
  if (hint) {
    const p = document.createElement("p");
    p.className = "meta";
    p.textContent = hint;
    wrap.append(p);
  }
  return wrap;
}

/** Zeigt den letzten Bericht — aufklappbar, damit die Schritte einzeln lesbar
 *  sind. Eigene Funktion, weil ein Lauf von Hand ihn sofort ersetzen muss:
 *  „Zuletzt: 09:39" neben einem Ergebnis von 09:44 liest sich wie ein Fehler. */
function showLastRun(container, run) {
  container.replaceChildren();
  if (!run) return;

  const details = document.createElement("details");
  const summary = document.createElement("summary");
  const wann = new Date(run.started_at).toLocaleString("de-DE");
  summary.textContent = `Zuletzt: ${wann}${run.ok ? "" : " — mit Fehlern"}`;
  details.append(summary);

  const liste = document.createElement("ul");
  for (const job of run.jobs) {
    const eintrag = document.createElement("li");
    eintrag.className = "meta";
    eintrag.textContent = `${job.name}: ${job.detail || (job.ok ? "ok" : "Fehler")}`;
    if (!job.ok) eintrag.classList.add("error");
    liste.append(eintrag);
  }
  details.append(liste);
  container.append(details);
}

async function renderSchedule(panel) {
  const plan = await api("/schedule");

  const block = document.createElement("section");
  block.className = "setup-block";

  const head = document.createElement("h3");
  head.textContent = "Mitlaufen";

  const note = document.createElement("p");
  note.className = "muted";
  note.textContent =
    "Liest die eingestellten Ordner erneut ein, legt Vorschläge vor und sichert " +
    "das Gedächtnis. Er schreibt nichts in den Bestand — das bleibt deine " +
    "Entscheidung. Er läuft nur, solange Icarus offen ist.";

  block.append(head, note);

  block.append(
    checkbox("Regelmäßig laufen lassen", "plan-enabled", plan.enabled),
    field("Alle … Minuten", "plan-interval", {
      value: plan.interval_minutes,
      hint: `Mindestens ${plan.min_interval_minutes}. Häufiger erzeugt Lärm, bevor du die erste Runde geprüft hast.`,
    }),
    checkbox(
      "Dabei auch das Modell fragen", "plan-model", plan.with_model,
      "Nur damit entstehen neue Aussagen aus deinen Notizen — und nur damit " +
        "kostet der Lauf etwas beim Anbieter."
    ),
    checkbox("Bei jedem Lauf sichern", "plan-backup", plan.backup)
  );

  // Die Ordner ein drittes Mal abzutippen wäre zweimal zu viel. Sie stehen
  // schon in der Freigabe — hier wird nur angehakt, welche mitlaufen sollen.
  const quellen = document.createElement("div");
  const qh = document.createElement("p");
  qh.className = "meta";
  const roots = setupState.status.file_roots ?? [];
  qh.textContent = roots.length
    ? "Diese Ordner bei jedem Lauf erneut einlesen:"
    : "Kein Ordner freigegeben — es gibt nichts einzulesen.";
  quellen.append(qh);
  for (const [i, pfad] of roots.entries()) {
    quellen.append(checkbox(pfad, `plan-quelle-${i}`, pfad in (plan.sources ?? {})));
  }
  block.append(quellen);

  const result = document.createElement("p");
  result.className = "meta";

  // Was zuletzt passiert ist, gehört sichtbar hierher. Ein Prozess, dessen
  // Ergebnisse man nirgends sieht, ist nicht von einem abgestürzten zu
  // unterscheiden.
  const letzter = document.createElement("div");
  showLastRun(letzter, plan.last_run);
  block.append(letzter);

  const save = document.createElement("button");
  save.type = "button";
  save.textContent = "Übernehmen";
  save.addEventListener("click", async () => {
    save.disabled = true;
    const sources = {};
    for (const [i, pfad] of (setupState.status.file_roots ?? []).entries()) {
      if ($(`#plan-quelle-${i}`)?.checked) sources[pfad] = "markdown";
    }
    try {
      await api("/schedule", {
        method: "PUT",
        body: JSON.stringify({
          enabled: $("#plan-enabled").checked,
          interval_minutes: Number($("#plan-interval").value) || undefined,
          with_model: $("#plan-model").checked,
          backup: $("#plan-backup").checked,
          sources,
        }),
      });
      await loadSetup();
    } catch (err) {
      result.textContent = err.message;
      result.classList.add("error");
      save.disabled = false;
    }
  });

  const jetzt = document.createElement("button");
  jetzt.type = "button";
  jetzt.className = "ghost small";
  jetzt.textContent = "Jetzt einmal laufen";
  jetzt.addEventListener("click", async () => {
    jetzt.disabled = true;
    result.textContent = "Läuft…";
    result.classList.remove("error");
    try {
      const report = await api("/schedule/run", { method: "POST" });
      result.textContent = report.summary;
      result.classList.toggle("error", !report.ok);
      showLastRun(letzter, report);
      await loadDashboard();
    } catch (err) {
      result.textContent = err.message;
      result.classList.add("error");
    } finally {
      jetzt.disabled = false;
    }
  });

  const row = document.createElement("div");
  row.className = "row";
  row.append(save, jetzt);
  block.append(row, result);
  panel.append(block);
}

// -- Sicherungen ------------------------------------------------------------
//
// Der Zeitplan legt bei jedem Lauf eine an. Bis eben waren sie unsichtbar und
// unbenutzbar — ein Sicherungsnetz ohne Griff ist eine Beruhigung ohne Deckung.
//
// Das Zurückspielen ersetzt den aktuellen Stand, also wird gefragt. Es ist aber
// nicht unumkehrbar: Der ersetzte Stand landet daneben, nicht im Nichts. Genau
// das steht auch in der Rückfrage — sonst klingt sie schlimmer als sie ist.

async function renderBackups(panel) {
  const block = document.createElement("section");
  block.className = "setup-block";

  const head = document.createElement("h3");
  head.textContent = "Sicherungen";

  const note = document.createElement("p");
  note.className = "muted";
  note.textContent =
    "Ein Gedächtnis, das Jahre halten soll, hat genau einen katastrophalen " +
    "Fehlerfall. Beim Zurückspielen wird der aktuelle Stand nicht gelöscht, " +
    "sondern danebengelegt.";

  const ergebnis = document.createElement("p");
  ergebnis.className = "meta";

  const liste = document.createElement("ul");

  async function zeichnen() {
    const sicherungen = await api("/backups");
    liste.replaceChildren();
    if (!sicherungen.length) {
      liste.append(meldung("Noch keine Sicherung."));
      return;
    }
    for (const s of sicherungen) {
      const el = document.createElement("li");
      const name = document.createElement("p");
      name.className = "statement";
      name.textContent = lesbarerZeitpunkt(s.name);

      const meta = document.createElement("p");
      meta.className = "meta";
      meta.textContent = s.name;

      const zurueck = document.createElement("button");
      zurueck.className = "ghost small";
      zurueck.textContent = "Zurückspielen";
      zurueck.addEventListener("click", async () => {
        // Eine Rückfrage, die sein muss, in einem Satz — und sie sagt, dass
        // nichts verloren geht. Ohne das lehnt jeder vernünftige Mensch ab.
        const ok = window.confirm(
          `Den Stand von ${lesbarerZeitpunkt(s.name)} zurückspielen?\n\n` +
          "Der aktuelle Stand wird nicht gelöscht — er landet als eigene " +
          "Datei daneben."
        );
        if (!ok) return;
        zurueck.disabled = true;
        ergebnis.classList.remove("error");
        ergebnis.textContent = "Wird zurückgespielt…";
        try {
          const r = await api("/backups/restore", {
            method: "POST", body: JSON.stringify({ name: s.name }),
          });
          ergebnis.textContent =
            `Zurückgespielt: ${r.assertions} Aussagen. ${r.detail}`;
          await loadMemory().catch(() => {});
          await refreshStatus().catch(() => {});
        } catch (err) {
          ergebnis.textContent = err.message;
          ergebnis.classList.add("error");
        } finally {
          zurueck.disabled = false;
        }
      });

      el.append(name, meta, zurueck);
      liste.append(el);
    }
  }

  const jetzt = document.createElement("button");
  jetzt.type = "button";
  jetzt.textContent = "Jetzt sichern";
  jetzt.addEventListener("click", async () => {
    jetzt.disabled = true;
    ergebnis.classList.remove("error");
    try {
      const { name } = await api("/backups", { method: "POST" });
      ergebnis.textContent = `Gesichert: ${name}`;
      await zeichnen();
    } catch (err) {
      ergebnis.textContent = err.message;
      ergebnis.classList.add("error");
    } finally {
      jetzt.disabled = false;
    }
  });

  block.append(head, note, liste, jetzt, ergebnis);
  panel.append(block);
  await zeichnen();
}

/** „self-model-20260802T122954Z.sqlite3" → „2.8.2026, 12:29". */
function lesbarerZeitpunkt(name) {
  const m = /(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z/.exec(name);
  if (!m) return name;
  const [, j, mo, ta, st, mi, se] = m;
  return new Date(Date.UTC(+j, +mo - 1, +ta, +st, +mi, +se))
    .toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" });
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
      "Kannst du auch später einrichten — oben unter „Mehr“. Mail ist der " +
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
    const art = ASSERTION_KIND_LABELS[a.kind] ?? a.kind;
    meta.textContent = `${art} · ${origin} · ${new Date(a.recorded_at).toLocaleDateString("de-DE")}`;
    if (a.provenance.source_type === "inference") meta.classList.add("inferred");

    const forget = document.createElement("button");
    forget.className = "ghost small";
    forget.textContent = "Vergessen";
    forget.addEventListener("click", async () => {
      const affected = await api(`/assertions/${a.id}/redact`, {
        method: "POST",
        body: JSON.stringify({ reason: "user_request" }),
      });
      const mit = affected.length - 1;
      if (mit > 0) {
        meldeZustand(
          mit === 1
            ? "Vergessen — samt einer daraus abgeleiteten Aussage."
            : `Vergessen — samt ${mit} daraus abgeleiteten Aussagen.`,
        );
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
  // Wie überall sonst: eine leere Ansicht sagt, dass sie leer ist, und warum.
  $("#audit-empty").hidden = entries.length > 0;

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

  // Läuft alles, steht hier **nichts**. Eine Leiste, die dauernd meldet, dass
  // alles in Ordnung ist, meldet nichts — und wenn dann wirklich etwas fehlt,
  // geht es zwischen den Selbstverständlichkeiten unter. Was eingerichtet ist,
  // steht unter „Einrichtung“; hierher gehört nur, was den Nutzer heute
  // einschränkt.
  if (health.chat) {
    meldeZustand("");
  } else {
    meldeZustand("Kein Modell", "error");
    statusEl.title = "Ohne Modell gehen keine Gespräche. Oben unter „Mehr“ einrichten.";
  }

  // Lieber ehrlich sagen, was fehlt, als einen kaputten Chat anbieten.
  $("#chat-input").placeholder = health.chat
    ? "Schreib etwas…"
    : "Kein Modell eingerichtet — siehe Einrichtung";
  return health;
}

async function start() {
  // Über `connectionInfo()`, nicht direkt über Tauri: Dieselbe Oberfläche läuft
  // im Container in einem normalen Browser, und dort gibt es kein `invoke`.
  ({ base, token } = await connectionInfo());

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
        "Das Gedächtnis funktioniert trotzdem: in der Ablage unter „Was ich " +
        "weiß“ lassen sich Aussagen speichern, ansehen und widerrufen, unter " +
        "„Eingelesenes“ " +
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
  meldeZustand("Der Sidecar antwortet nicht.", "error");
  statusEl.classList.add("error");
}

startWithRetry();
