// Icarus — tägliche Chief-of-Staff-Schicht.
//
// Diese Ebene verdichtet die vorhandenen, verbindlichen Daten zu begründeten
// nächsten Handlungen. Sie erfindet keine Fakten und schreibt nichts in die
// Stores. Jede Empfehlung verweist auf den Bereich, aus dem sie stammt.

const tauri = window.__TAURI__?.core ?? null;
let connectionPromise = null;

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

async function api(path) {
  const info = await connectionInfo();
  const response = await fetch(`${info.base}${path}`, {
    headers: {
      "content-type": "application/json",
      "x-icarus-token": info.token,
    },
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail ?? `HTTP ${response.status}`);
  }
  return response.json();
}

function startOfDay(value) {
  const date = new Date(value);
  date.setHours(0, 0, 0, 0);
  return date;
}

function daysBetween(left, right) {
  return Math.round((startOfDay(right) - startOfDay(left)) / 86_400_000);
}

function hoursUntil(value, now) {
  return (new Date(value) - now) / 3_600_000;
}

function asItems(value) {
  if (Array.isArray(value)) return value;
  return Array.isArray(value?.items) ? value.items : [];
}

/**
 * Erzeugt eine deterministische, erklärbare Prioritätenliste.
 *
 * Kein Modell ist beteiligt. Gleiche Eingaben erzeugen dieselbe Reihenfolge;
 * der Nutzer kann deshalb nachvollziehen, warum etwas oben steht.
 */
export function rankToday(dashboard, now = new Date()) {
  const candidates = [];

  for (const task of asItems(dashboard.tasks)) {
    let score = 50;
    let reason = "Offene Aufgabe";
    if (task.overdue) {
      const late = task.due ? Math.max(1, -daysBetween(now, task.due)) : 1;
      score = 110 + Math.min(late, 20);
      reason = `${late} Tag${late === 1 ? "" : "e"} überfällig`;
    } else if (task.due) {
      const remaining = daysBetween(now, task.due);
      if (remaining <= 0) {
        score = 100;
        reason = "Heute fällig";
      } else if (remaining === 1) {
        score = 88;
        reason = "Morgen fällig";
      } else if (remaining <= 7) {
        score = 72 - remaining;
        reason = `In ${remaining} Tagen fällig`;
      }
    }
    candidates.push({
      id: `task:${task.id}`,
      score,
      title: task.title,
      reason,
      consequence: "Erledigen oder bewusst neu terminieren",
      view: "dashboard",
      kind: "Aufgabe",
    });
  }

  for (const event of asItems(dashboard.calendar)) {
    if (!event.start) continue;
    const hours = hoursUntil(event.start, now);
    if (hours < -1 || hours > 48) continue;
    const soon = hours <= 3;
    candidates.push({
      id: `event:${event.uid ?? event.summary}:${event.start}`,
      score: soon ? 105 : hours <= 12 ? 82 : 68,
      title: event.summary || "Termin",
      reason: soon
        ? `Beginnt in ${Math.max(0, Math.round(hours * 2) / 2)} Stunden`
        : "Steht als Nächstes im Kalender",
      consequence: "Kontext, Unterlagen und gewünschtes Ergebnis prüfen",
      view: "dashboard",
      kind: "Termin",
    });
  }

  const unread = Number(dashboard.mail?.unread ?? 0);
  if (unread > 0) {
    candidates.push({
      id: "mail:unread",
      score: 64 + Math.min(unread, 10),
      title: `${unread} ungelesene Nachricht${unread === 1 ? "" : "en"}`,
      reason: "Mögliche neue Verpflichtungen oder Rückfragen",
      consequence: "Posteingang prüfen und offene Schleifen erfassen",
      view: "chat",
      kind: "Nachrichten",
    });
  }

  for (const project of asItems(dashboard.projects)) {
    if (!project.deadline || !project.open) continue;
    const remaining = daysBetween(now, project.deadline);
    if (remaining > 14) continue;
    candidates.push({
      id: `project:${project.id}`,
      score: remaining < 0 ? 96 : 78 - Math.max(remaining, 0),
      title: project.name,
      reason:
        remaining < 0
          ? `Frist seit ${Math.abs(remaining)} Tagen überschritten`
          : remaining === 0
            ? "Projektfrist ist heute"
            : `Projektfrist in ${remaining} Tagen`,
      consequence: "Nächsten konkreten Schritt und Risiko prüfen",
      view: "projects",
      kind: "Projekt",
    });
  }

  return candidates
    .sort((left, right) => right.score - left.score || left.title.localeCompare(right.title, "de"))
    .slice(0, 7);
}

export function projectBrief(project, now = new Date()) {
  const tasks = project.tasks ?? [];
  const notes = project.notes ?? [];
  const next = [...tasks].sort((left, right) => {
    if (left.overdue !== right.overdue) return left.overdue ? -1 : 1;
    if (left.due && right.due) return new Date(left.due) - new Date(right.due);
    if (left.due) return -1;
    if (right.due) return 1;
    return left.title.localeCompare(right.title, "de");
  })[0];

  const searchable = [
    project.description ?? "",
    ...tasks.flatMap((task) => [task.title ?? "", task.notes ?? "", ...(task.tags ?? [])]),
    ...notes.flatMap((note) => [note.title ?? "", note.body ?? "", ...(note.tags ?? [])]),
  ].join(" ").toLowerCase();

  const waiting = /wartet|waiting|abhängig|rückmeldung|antwort ausstehend/.test(searchable);
  const blocked = /blockiert|blocked|hindernis|kann nicht|fehlt noch/.test(searchable);
  const decisions = notes.filter((note) => note.kind === "decision");
  const deadlineDays = project.deadline ? daysBetween(now, project.deadline) : null;

  let risk = "Kein akutes Fristrisiko aus den vorhandenen Daten erkennbar.";
  if (deadlineDays !== null && deadlineDays < 0) {
    risk = `Die Frist ist seit ${Math.abs(deadlineDays)} Tagen überschritten.`;
  } else if (deadlineDays !== null && deadlineDays <= 7 && tasks.length > 0) {
    risk = `${tasks.length} offene Aufgabe${tasks.length === 1 ? "" : "n"} bei nur ${deadlineDays} verbleibenden Tagen.`;
  } else if (blocked) {
    risk = "Im Projektkontext wird eine Blockade genannt.";
  }

  return {
    nextAction: next?.title ?? "Einen konkreten nächsten Schritt festlegen.",
    waiting: waiting ? "Eine Rückmeldung oder Abhängigkeit scheint offen zu sein." : "Kein Wartestatus markiert.",
    blocked: blocked ? "Eine mögliche Blockade ist im Projektkontext genannt." : "Keine Blockade markiert.",
    risk,
    decisions: decisions.map((note) => note.title),
  };
}

function activate(view) {
  document.querySelector(`.tab[data-view="${view}"]`)?.click();
}

function priorityCard(item, index) {
  const row = document.createElement("li");
  row.className = "focus-item";

  const number = document.createElement("span");
  number.className = "focus-rank";
  number.textContent = String(index + 1);
  number.setAttribute("aria-hidden", "true");

  const copy = document.createElement("div");
  const title = document.createElement("p");
  title.className = "statement";
  title.textContent = item.title;
  const reason = document.createElement("p");
  reason.className = "meta";
  reason.textContent = `${item.kind} · ${item.reason} · ${item.consequence}`;
  copy.append(title, reason);

  const open = document.createElement("button");
  open.type = "button";
  open.className = "ghost small";
  open.textContent = "Öffnen";
  open.setAttribute("aria-label", `${item.title} öffnen`);
  open.addEventListener("click", () => activate(item.view));

  row.append(number, copy, open);
  return row;
}

let focusRefresh = null;
async function renderFocus() {
  if (focusRefresh) return focusRefresh;
  focusRefresh = (async () => {
    const view = document.querySelector("#view-dashboard");
    const greeting = document.querySelector("#greeting");
    if (!view || !greeting) return;

    let panel = document.querySelector("#daily-focus");
    if (!panel) {
      panel = document.createElement("article");
      panel.id = "daily-focus";
      panel.className = "daily-focus";
      panel.setAttribute("aria-labelledby", "daily-focus-title");
      greeting.after(panel);
    }

    try {
      const priorities = rankToday(await api("/dashboard"));
      const head = document.createElement("div");
      head.className = "focus-head";
      const copy = document.createElement("div");
      const title = document.createElement("h3");
      title.id = "daily-focus-title";
      title.textContent = priorities.length ? "Das verdient heute zuerst Aufmerksamkeit" : "Heute ist nichts dringend";
      const note = document.createElement("p");
      note.className = "muted";
      note.textContent = priorities.length
        ? "Nach Frist, zeitlicher Nähe und möglicher Außenwirkung geordnet. Jede Priorität zeigt ihren Grund."
        : "Icarus findet aktuell keine überfällige Aufgabe, nahe Frist oder unmittelbar bevorstehenden Termin.";
      copy.append(title, note);
      head.append(copy);

      const list = document.createElement("ol");
      list.className = "focus-list";
      list.replaceChildren(...priorities.map(priorityCard));
      panel.replaceChildren(head, list);
      panel.dataset.ready = "true";
    } catch (error) {
      const message = document.createElement("p");
      message.className = "meta error";
      message.textContent = `Tagesfokus konnte nicht geladen werden: ${error.message}`;
      panel.replaceChildren(message);
    }
  })().finally(() => {
    focusRefresh = null;
  });
  return focusRefresh;
}

function briefLine(label, value) {
  const row = document.createElement("div");
  const term = document.createElement("dt");
  term.textContent = label;
  const description = document.createElement("dd");
  description.textContent = value;
  row.append(term, description);
  return row;
}

let projectRefresh = null;
async function enhanceProject() {
  if (projectRefresh) return projectRefresh;
  const detail = document.querySelector("#project-detail");
  const selected = document.querySelector("#project-list .project.selected");
  if (!detail || !selected || detail.querySelector(".project-brief")) return;

  const projects = await api("/projects?all=true").catch(() => []);
  const selectedName = selected.querySelector(".statement")?.textContent;
  const project = projects.find((entry) => entry.name === selectedName);
  if (!project) return;

  projectRefresh = (async () => {
    const full = await api(`/projects/${project.id}`);
    const brief = projectBrief(full);
    const card = document.createElement("section");
    card.className = "project-brief";
    card.setAttribute("aria-label", "Projektbriefing");
    const title = document.createElement("h4");
    title.textContent = "Projektbriefing";
    const list = document.createElement("dl");
    list.append(
      briefLine("Nächster Schritt", brief.nextAction),
      briefLine("Wartestatus", brief.waiting),
      briefLine("Blockaden", brief.blocked),
      briefLine("Risiko", brief.risk),
      briefLine("Entscheidungen", brief.decisions.length ? brief.decisions.join(", ") : "Keine dokumentierte Entscheidung.")
    );
    card.append(title, list);
    detail.querySelector("h3")?.after(card);
  })().finally(() => {
    projectRefresh = null;
  });
  return projectRefresh;
}

let scheduled = false;
function schedule() {
  if (scheduled) return;
  scheduled = true;
  requestAnimationFrame(() => {
    scheduled = false;
    if (!document.querySelector("#view-dashboard")?.hidden) void renderFocus();
    if (!document.querySelector("#view-projects")?.hidden) void enhanceProject();
  });
}

new MutationObserver(schedule).observe(document.body, {
  childList: true,
  subtree: true,
  attributes: true,
  attributeFilter: ["hidden", "class"],
});

document.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (button?.dataset.view === "dashboard") window.setTimeout(renderFocus, 0);
  if (button?.dataset.view === "projects") window.setTimeout(enhanceProject, 0);
});

schedule();
window.setInterval(() => {
  if (!document.hidden) void renderFocus();
}, 60_000);
