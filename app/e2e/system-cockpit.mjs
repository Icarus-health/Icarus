import assert from "node:assert/strict";
import { chromium, request } from "playwright";

const baseURL = process.env.ICARUS_E2E_BASE ?? "http://127.0.0.1:8765";
const token = process.env.ICARUS_E2E_TOKEN ?? "rauchtest";
const marker = Date.now().toString(36);
const projectName = `Cockpit-Projekt-${marker}`;
const taskName = `Cockpit-Schritt-${marker}`;
const decisionName = `Cockpit-Entscheidung-${marker}`;
const secretName = `Cockpit-Sensibel-${marker}`;
const workflowName = `Cockpit-Automation-${marker}`;

const api = await request.newContext({
  baseURL,
  extraHTTPHeaders: {
    "content-type": "application/json",
    "x-icarus-token": token,
  },
});

async function call(method, path, data) {
  const response = await api.fetch(path, { method, data });
  const text = await response.text();
  assert.equal(response.ok(), true, `${method} ${path}: ${response.status()} ${text}`);
  return text ? JSON.parse(text) : null;
}

const project = await call("POST", "/projects", {
  name: projectName,
  description: "Cockpit-End-to-End-Test",
});
await call("POST", "/tasks", {
  title: taskName,
  project_id: project.id,
});
await call("POST", "/notes", {
  title: decisionName,
  body: "Die Systemzentrale bleibt quellennachweisbar.",
  kind: "decision",
  project_id: project.id,
});
await call("POST", "/assertions", {
  statement: secretName,
  kind: "goal",
  sensitivity: "special_category",
  provenance: { source_type: "user_stated" },
});
await call("POST", "/graph/rebuild");
await call("POST", "/workflows", {
  id: `wf-cockpit-${marker}`,
  name: workflowName,
  steps: [
    {
      id: "wait",
      kind: "wait_until",
      run_at: new Date(Date.now() + 86_400_000).toISOString(),
      action_class: "read",
    },
  ],
});

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });

try {
  await page.goto(`${baseURL}/?token=${encodeURIComponent(token)}`, {
    waitUntil: "domcontentloaded",
  });
  await page.locator("#status.ready").waitFor();

  await page.locator(".nav-more > summary").click();
  await page.locator('button[data-view="system"]').click();
  await page.locator("#view-system:not([hidden])").waitFor();
  await page.getByRole("heading", { name: "Dein Icarus-System" }).waitFor();
  await page.locator("#system-feedback").getByText("Alles aktuell.", { exact: true }).waitFor();
  await page.getByText(workflowName, { exact: true }).waitFor();

  const search = page.locator("#graph-search");
  await search.fill(projectName);
  await page.locator("#graph-search-form").evaluate((form) => form.requestSubmit());
  await page.locator("#graph-results").getByText(projectName, { exact: true }).waitFor();
  await page.locator("#graph-results").getByText(projectName, { exact: true }).click();
  const detail = page.locator("#graph-detail");
  const relations = detail.locator(".system-detail-list").first();
  await relations.getByText(taskName, { exact: false }).waitFor();
  await relations.getByText(decisionName, { exact: false }).waitFor();

  await search.fill(secretName);
  await page.locator("#graph-search-form").evaluate((form) => form.requestSubmit());
  await page.locator("#graph-results").getByText("Keine passenden Zusammenhänge.", { exact: true }).waitFor();
  assert.equal(
    await page.locator("#graph-results").getByText(secretName, { exact: true }).count(),
    0,
    "Sensibler Eintrag war ohne bewusste Umschaltung sichtbar"
  );

  await page.locator("#graph-sensitive").check();
  await page.locator("#graph-results").getByText(secretName, { exact: true }).waitFor();

  console.log(
    "Systemzentrale bestanden: Projektgraph, Automation und bewusste Freigabe sensibler Zusammenhänge funktionieren."
  );
} finally {
  await browser.close();
  await api.dispose();
}
