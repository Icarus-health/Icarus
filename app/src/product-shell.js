// Icarus — Consumer-Schale über dem technischen Alpha-Gerüst.
//
// Diese Datei verändert nicht die fachlichen Verträge in main.js. Sie ordnet
// die vorhandenen Funktionen so an, dass die Alltagsebene zuerst kommt und die
// technischen Bereiche erreichbar bleiben, ohne die Hauptnavigation zu füllen.

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
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail ?? `HTTP ${response.status}`);
  }
  return response.status === 204 ? null : response.json();
}

function tab(view) {
  return document.querySelector(`.tab[data-view="${view}"]`);
}

function setButtonLabel(button, label) {
  if (!button) return;
  const text = [...button.childNodes].find((node) => node.nodeType === Node.TEXT_NODE);
  if (text) text.nodeValue = `${label} `;
  else button.prepend(document.createTextNode(`${label} `));
}

function arrangeNavigation() {
  const nav = document.querySelector("nav");
  const status = document.querySelector("#status");
  if (!nav || !status || nav.querySelector(".nav-more")) return;

  setButtonLabel(tab("projects"), "Arbeit");
  setButtonLabel(tab("proposals"), "Entscheiden");
  setButtonLabel(tab("ingest"), "Quellen");

  const details = document.createElement("details");
  details.className = "nav-more";
  const summary = document.createElement("summary");
  summary.textContent = "Mehr";
  summary.setAttribute("aria-label", "Weitere Bereiche");

  const menu = document.createElement("div");
  menu.className = "nav-more-menu";
  for (const view of ["ingest", "memory", "audit", "setup"]) {
    const button = tab(view);
    if (button) {
      button.addEventListener("click", () => details.removeAttribute("open"));
      menu.append(button);
    }
  }

  details.append(summary, menu);
  nav.insertBefore(details, status);
}

function showRestoreToast() {
  const text = sessionStorage.getItem("icarus-restore-message");
  if (!text) return;
  sessionStorage.removeItem("icarus-restore-message");

  const toast = document.createElement("aside");
  toast.className = "restore-toast";
  toast.setAttribute("role", "status");
  const message = document.createElement("span");
  message.textContent = text;
  const close = document.createElement("button");
  close.type = "button";
  close.className = "ghost small";
  close.textContent = "Schließen";
  close.addEventListener("click", () => toast.remove());
  toast.append(message, close);
  document.querySelector("main")?.before(toast);
}

function goalButton(label, value, selected) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "goal-choice";
  button.textContent = label;
  button.dataset.goal = value;
  button.setAttribute("aria-pressed", String(selected));
  button.addEventListener("click", () => {
    localStorage.setItem("icarus-start-goal", value);
    document.querySelectorAll(".goal-choice").forEach((item) => {
      item.setAttribute("aria-pressed", String(item === button));
    });
  });
  return button;
}

function enhanceWizard() {
  const wizard = document.querySelector("#wizard");
  const progress = document.querySelector("#wizard-progress")?.textContent ?? "";
  const body = document.querySelector("#wizard-body");
  if (!wizard || !body || wizard.hidden) return;

  if (progress.startsWith("Schritt 1") && !body.querySelector(".goal-grid")) {
    document.querySelector("#wizard-title").textContent =
      "Wobei soll Icarus dir zuerst helfen?";
    document.querySelector("#wizard-text").textContent =
      "Wähle den ersten Nutzen. Technische Verbindungen sind optional und können später ergänzt werden.";

    const selected = localStorage.getItem("icarus-start-goal") ?? "day";
    const grid = document.createElement("div");
    grid.className = "goal-grid";
    grid.append(
      goalButton("Meinen Tag ordnen", "day", selected === "day"),
      goalButton("Projekte zusammenhalten", "projects", selected === "projects"),
      goalButton("Offene Entscheidungen sehen", "decisions", selected === "decisions")
    );
    body.append(grid);
  }

  if (progress.startsWith("Schritt 2")) {
    document.querySelector("#wizard-title").textContent =
      "Gespräch aktivieren — optional";
    document.querySelector("#wizard-text").textContent =
      "Für Gespräche kann ein Modell verbunden werden. Gedächtnis, Projekte und Aufgaben funktionieren auch ohne.";
  }
}

let wizardWasVisible = false;
function handleWizardState() {
  const wizard = document.querySelector("#wizard");
  if (!wizard) return;
  const visible = !wizard.hidden;
  if (visible) {
    wizardWasVisible = true;
    return;
  }
  if (!wizardWasVisible) return;
  wizardWasVisible = false;

  const goal = localStorage.getItem("icarus-start-goal") ?? "day";
  const destination = goal === "projects" ? "projects" : goal === "decisions" ? "proposals" : "dashboard";
  tab(destination)?.click();
}

function decisionCard(approvals) {
  const card = document.createElement("article");
  card.id = "decision-approvals";
  card.className = "decision-summary";

  const head = document.createElement("h3");
  head.textContent = approvals.length
    ? `${approvals.length} Aktion${approvals.length === 1 ? "" : "en"} wartet auf Freigabe`
    : "Keine Aktion wartet auf Freigabe";

  const text = document.createElement("p");
  text.className = "muted";
  text.textContent = approvals.length
    ? "Außenwirksame Schritte werden erst ausgeführt, nachdem du den vollständigen Inhalt geprüft hast."
    : "Hier erscheinen Gedächtnisvorschläge und Aktionen, die deine Entscheidung brauchen.";

  card.append(head, text);
  if (approvals.length) {
    const open = document.createElement("button");
    open.type = "button";
    open.textContent = "Im Gespräch prüfen";
    open.addEventListener("click", () => tab("chat")?.click());
    card.append(open);
  }
  return card;
}

let decisionRefresh = null;
async function refreshDecisionArea() {
  if (decisionRefresh) return decisionRefresh;
  decisionRefresh = (async () => {
    try {
      const [counts, approvals] = await Promise.all([
        api("/proposals/counts"),
        api("/approvals"),
      ]);
      const total = Number(counts.pending ?? 0) + approvals.length;
      const badge = document.querySelector("#proposals-badge");
      if (badge) {
        badge.hidden = total === 0;
        badge.textContent = String(total);
        badge.title = `${counts.pending ?? 0} Gedächtnisvorschläge, ${approvals.length} Freigaben`;
      }

      const view = document.querySelector("#view-proposals");
      if (view) {
        view.querySelector("#decision-approvals")?.remove();
        const intro = view.querySelector(":scope > p.muted");
        intro?.after(decisionCard(approvals));
        if (intro) {
          intro.textContent =
            "Hier wartet alles, was deine Entscheidung braucht. Nichts wird stillschweigend übernommen oder ausgeführt.";
        }
      }
    } catch {
      // Die Hauptoberfläche bleibt zuständig für ihren eigenen Fehlerzustand.
    } finally {
      decisionRefresh = null;
    }
  })();
  return decisionRefresh;
}

function humanBytes(bytes) {
  const value = Number(bytes ?? 0);
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function backupBlock() {
  return [...document.querySelectorAll("#setup-panel .setup-block")].find(
    (block) => block.querySelector("h3")?.textContent.trim() === "Sicherungen"
  );
}

let backupEnhancement = null;
async function enhanceBackups() {
  const block = backupBlock();
  if (!block || backupEnhancement) return backupEnhancement;

  backupEnhancement = (async () => {
    const note = block.querySelector(":scope > p.muted");
    if (note) {
      note.textContent =
        "Eine vollständige Sicherung enthält Gedächtnis, Projekte, Aufgaben, Notizen, Episoden, Vorschläge, Protokoll und Einstellungen. Zugangsdaten im Betriebssystem-Schlüsselbund sind nicht enthalten und müssen auf einem neuen Gerät erneut eingetragen werden.";
    }

    const create = [...block.querySelectorAll("button")].find(
      (button) => button.textContent.trim() === "Jetzt sichern"
    );
    if (create) create.dataset.testid = "create-backup";

    let backups = [];
    try {
      backups = await api("/backups");
    } catch {
      return;
    }
    const byName = new Map(backups.map((entry) => [entry.name, entry]));

    for (const item of block.querySelectorAll("li")) {
      const meta = item.querySelector("p.meta");
      const fileName = meta?.textContent.trim();
      const entry = byName.get(fileName);
      if (!entry || item.dataset.backupEnhanced === entry.name) continue;
      item.dataset.backupEnhanced = entry.name;

      const kind = entry.kind === "installation"
        ? "Vollständige Icarus-Sicherung"
        : "Älterer Selbstmodell-Snapshot";
      const members = entry.members?.length
        ? ` · ${entry.members.length} Bestandteile`
        : "";
      if (meta) meta.textContent = `${kind}${members} · ${humanBytes(entry.bytes)}`;

      const restore = [...item.querySelectorAll("button")].find(
        (button) => button.textContent.trim() === "Zurückspielen"
      );
      if (restore) {
        restore.dataset.testid = "restore-backup";
        restore.dataset.backupName = entry.name;
        if (entry.invalid) {
          restore.disabled = true;
          restore.title = "Diese Sicherung ist beschädigt oder unvollständig.";
        }
      }

      if (entry.members?.length && !item.querySelector(".backup-members")) {
        const details = document.createElement("details");
        details.className = "backup-members";
        const summary = document.createElement("summary");
        summary.textContent = "Enthaltene Daten";
        const content = document.createElement("p");
        content.className = "meta";
        content.textContent = entry.members.join(", ");
        details.append(summary, content);
        item.append(details);
      }
    }
  })().finally(() => {
    backupEnhancement = null;
  });
  return backupEnhancement;
}

let restorePending = false;
function watchRestoreResult() {
  if (!restorePending) return;
  const block = backupBlock();
  if (!block) return;
  const messages = [...block.querySelectorAll(":scope > p.meta")];
  const result = messages.at(-1);
  const text = result?.textContent.trim() ?? "";

  if (text.startsWith("Zurückgespielt:")) {
    restorePending = false;
    sessionStorage.setItem(
      "icarus-restore-message",
      `${text} Alle Ansichten wurden aus dem wiederhergestellten Stand neu geladen.`
    );
    window.setTimeout(() => window.location.reload(), 150);
  } else if (result?.classList.contains("error")) {
    restorePending = false;
  }
}

document.addEventListener(
  "click",
  (event) => {
    const button = event.target.closest("button");
    if (!button) return;

    if (button.dataset.view === "proposals") {
      window.setTimeout(refreshDecisionArea, 0);
    }
    if (button.dataset.view === "setup") {
      window.setTimeout(enhanceBackups, 0);
    }

    if (
      button.dataset.testid === "restore-backup" ||
      (button.textContent.trim() === "Zurückspielen" && button.closest(".setup-block"))
    ) {
      restorePending = true;
      window.setTimeout(() => {
        const block = backupBlock();
        const messages = block ? [...block.querySelectorAll(":scope > p.meta")] : [];
        const text = messages.at(-1)?.textContent ?? "";
        if (!text.includes("Wird zurückgespielt")) restorePending = false;
      }, 50);
    }
  },
  true
);

let enhancementScheduled = false;
function scheduleEnhancement() {
  if (enhancementScheduled) return;
  enhancementScheduled = true;
  window.requestAnimationFrame(() => {
    enhancementScheduled = false;
    arrangeNavigation();
    enhanceWizard();
    handleWizardState();
    void enhanceBackups();
    watchRestoreResult();
  });
}

const observer = new MutationObserver(scheduleEnhancement);
observer.observe(document.body, {
  childList: true,
  subtree: true,
  attributes: true,
  attributeFilter: ["hidden"],
});

arrangeNavigation();
showRestoreToast();
scheduleEnhancement();
void refreshDecisionArea();
window.setInterval(refreshDecisionArea, 15000);
void import("./model-harness-panel.js");
