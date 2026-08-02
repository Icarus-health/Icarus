import assert from "node:assert/strict";
import { chromium, request } from "playwright";

const baseURL = process.env.ICARUS_E2E_BASE ?? "http://127.0.0.1:8765";
const token = process.env.ICARUS_E2E_TOKEN ?? "rauchtest";

const api = await request.newContext({
  baseURL,
  extraHTTPHeaders: {
    "content-type": "application/json",
    "x-icarus-token": token,
  },
});

async function call(method, path, data) {
  const response = await api.fetch(path, {
    method,
    data,
  });
  const text = await response.text();
  assert.equal(
    response.ok(),
    true,
    `${method} ${path} scheiterte mit ${response.status()}: ${text}`
  );
  return text ? JSON.parse(text) : null;
}

async function waitForHealth() {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    try {
      const health = await call("GET", "/health");
      if (health.status === "ok") return;
    } catch {
      // Der Container kann beim ersten Versuch noch starten.
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error("Icarus wurde nicht rechtzeitig bereit.");
}

await waitForHealth();
await call("PUT", "/setup", { onboarded: true, provider: "", model: "" });

const assertionBefore = await call("POST", "/assertions", {
  statement: "Bestand vor der Sicherung.",
  kind: "identity",
  provenance: { source_type: "user_stated" },
});
const projectBefore = await call("POST", "/projects", {
  name: "Projekt vor Sicherung",
  area: "E2E",
  description: "Muss nach dem Restore wieder da sein.",
});
await call("POST", "/tasks", {
  title: "Aufgabe vor Sicherung",
  project_id: projectBefore.id,
});
await call("POST", "/notes", {
  title: "Notiz vor Sicherung",
  body: "Diese Notiz gehört zum gesicherten Projekt.",
  kind: "decision",
  project_id: projectBefore.id,
});
await call("POST", "/episodes", {
  title: "Episode vor Sicherung",
  body: "Rohmaterial, das nach dem Restore vorhanden sein muss.",
  kind: "observation",
  project_id: projectBefore.id,
});
await call("POST", "/tools/aufgabe_anlegen", {
  titel: "Audit-Aufgabe vor Sicherung",
  projekt: "Projekt vor Sicherung",
});

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });

try {
  await page.goto(`${baseURL}/?token=${encodeURIComponent(token)}`, {
    waitUntil: "domcontentloaded",
  });
  await page.locator("#status.ready").waitFor();

  // Consumer-Navigation: die Hauptleiste bleibt kurz, Einrichtung liegt unter Mehr.
  assert.equal(await page.locator("nav > .tab").count(), 4);
  await page.locator(".nav-more > summary").click();
  await page.locator('button[data-view="setup"]').click();
  await page.getByRole("heading", { name: "Sicherungen" }).waitFor();

  const createBackup = page.locator('[data-testid="create-backup"]');
  await createBackup.waitFor();
  await createBackup.click();
  await page.getByText(/Gesichert:/).waitFor();

  const backups = await call("GET", "/backups");
  assert.equal(backups.length, 1);
  const backup = backups[0];
  assert.equal(backup.kind, "installation");
  for (const expected of [
    "self-model.sqlite3",
    "audit.sqlite3",
    "tasks.sqlite3",
    "workspace.sqlite3",
    "episodes.sqlite3",
    "proposals.sqlite3",
    "einstellungen.json",
  ]) {
    assert.equal(
      backup.members.includes(expected),
      true,
      `${expected} fehlt im vollständigen Backup.`
    );
  }

  await call("POST", "/assertions", {
    statement: "Bestand nach der Sicherung — muss verschwinden.",
    kind: "identity",
    provenance: { source_type: "user_stated" },
  });
  const projectAfter = await call("POST", "/projects", {
    name: "Projekt nach Sicherung",
    area: "E2E",
  });
  await call("POST", "/tasks", {
    title: "Aufgabe nach Sicherung",
    project_id: projectAfter.id,
  });
  await call("POST", "/notes", {
    title: "Notiz nach Sicherung",
    body: "Muss durch den Restore verschwinden.",
    project_id: projectAfter.id,
  });
  await call("POST", "/episodes", {
    title: "Episode nach Sicherung",
    body: "Muss durch den Restore verschwinden.",
    kind: "observation",
    project_id: projectAfter.id,
  });
  await call("POST", "/tools/aufgabe_anlegen", {
    titel: "Audit-Aufgabe nach Sicherung",
    projekt: "Projekt nach Sicherung",
  });
  await call("PUT", "/setup", {
    provider: "ollama",
    model: "llama3.1",
  });

  // Seite neu laden, damit der sichtbare Stand wirklich dem geänderten Backend entspricht.
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.locator("#status.ready").waitFor();
  await page.locator(".nav-more > summary").click();
  await page.locator('button[data-view="setup"]').click();

  const restore = page.locator(
    `[data-testid="restore-backup"][data-backup-name="${backup.name}"]`
  );
  await restore.waitFor();
  page.once("dialog", (dialog) => dialog.accept());
  const reloaded = page.waitForNavigation({ waitUntil: "domcontentloaded" });
  await restore.click();
  await reloaded;
  await page.locator("#status.ready").waitFor();
  await page.getByRole("status").filter({ hasText: "Alle Ansichten wurden" }).waitFor();

  const assertions = await call("GET", "/export");
  assert.deepEqual(
    assertions.assertions.map((item) => item.statement),
    [assertionBefore.statement]
  );

  const projects = await call("GET", "/projects?all=true");
  assert.deepEqual(projects.map((item) => item.name), ["Projekt vor Sicherung"]);

  const tasks = await call("GET", "/tasks?all=true");
  assert.equal(tasks.some((item) => item.title === "Aufgabe nach Sicherung"), false);
  assert.equal(tasks.some((item) => item.title === "Aufgabe vor Sicherung"), true);
  assert.equal(tasks.some((item) => item.title === "Audit-Aufgabe vor Sicherung"), true);
  assert.equal(tasks.some((item) => item.title === "Audit-Aufgabe nach Sicherung"), false);

  const notes = await call("GET", "/notes");
  assert.deepEqual(notes.map((item) => item.title), ["Notiz vor Sicherung"]);

  const episodes = await call("GET", "/episodes?limit=200");
  assert.deepEqual(episodes.map((item) => item.title), ["Episode vor Sicherung"]);

  const setup = await call("GET", "/setup");
  assert.equal(setup.settings.provider, "");
  assert.equal(setup.settings.model, "");

  const audit = await call("GET", "/audit?limit=100");
  const auditText = JSON.stringify(audit);
  assert.match(auditText, /Audit-Aufgabe vor Sicherung/);
  assert.doesNotMatch(auditText, /Audit-Aufgabe nach Sicherung/);

  // Der nach dem Restore neu geladene Browser muss denselben Stand zeigen.
  await page.locator('button[data-view="dashboard"]').click();
  const dashboard = page.locator("#view-dashboard");
  await dashboard
    .locator("#project-teaser")
    .getByText("Projekt vor Sicherung", { exact: true })
    .waitFor();
  await dashboard
    .locator("#task-list")
    .getByText("Aufgabe vor Sicherung", { exact: true })
    .waitFor();
  assert.equal(
    await dashboard
      .locator("#project-teaser")
      .getByText("Projekt nach Sicherung", { exact: true })
      .count(),
    0
  );

  await page.locator('button[data-view="projects"]').click();
  const projectView = page.locator("#view-projects");
  await projectView
    .locator("#project-list")
    .getByText("Projekt vor Sicherung", { exact: true })
    .waitFor();
  assert.equal(
    await projectView
      .locator("#project-list")
      .getByText("Projekt nach Sicherung", { exact: true })
      .count(),
    0
  );

  console.log("Browser-Backup/Restore: vollständig bestanden.");
} finally {
  await browser.close();
  await api.dispose();
}
