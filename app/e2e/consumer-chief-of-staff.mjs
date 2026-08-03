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
  const response = await api.fetch(path, { method, data });
  const text = await response.text();
  assert.equal(response.ok(), true, `${method} ${path}: ${response.status()} ${text}`);
  return text ? JSON.parse(text) : null;
}

await call("PUT", "/setup", { onboarded: true });
const yesterday = new Date(Date.now() - 86_400_000).toISOString();
const old = new Date(Date.now() - 400 * 86_400_000).toISOString();
const deadline = new Date(Date.now() + 3 * 86_400_000).toISOString();
const project = await call("POST", "/projects", {
  name: "Consumer-E2E-Projekt",
  description: "Wartet auf Rückmeldung und ist blockiert, weil eine Freigabe fehlt.",
  deadline,
});
await call("POST", "/tasks", {
  title: "Überfällige Consumer-E2E-Aufgabe",
  due: yesterday,
  project_id: project.id,
});
await call("POST", "/notes", {
  title: "Budget freigegeben",
  body: "Die Budgetentscheidung ist dokumentiert.",
  kind: "decision",
  project_id: project.id,
});

// Ein alter, weiterhin aktiver Fakt erzeugt ohne Modell einen echten
// Bestätigungsvorschlag. So prüft der Browser den normalen Entscheidungsweg,
// statt einen nicht existierenden internen Werkzeug-Endpunkt zu verwenden.
await call("POST", "/assertions", {
  statement: "Consumer-E2E-Fakt gilt weiterhin.",
  kind: "state",
  provenance: {
    source_type: "user_stated",
    source_ref: "consumer-e2e",
    captured_at: old,
  },
});
await call("POST", "/consolidate", { limit: 20, with_model: false });

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });

try {
  await page.goto(`${baseURL}/?token=${encodeURIComponent(token)}`, {
    waitUntil: "domcontentloaded",
  });
  await page.locator("#status.ready").waitFor();

  const focus = page.locator("#daily-focus[data-ready='true']");
  await focus.waitFor();
  await focus.getByText("Überfällige Consumer-E2E-Aufgabe", { exact: true }).waitFor();
  await focus.getByText(/überfällig/).waitFor();

  await page.locator('button[data-view="projects"]').click();
  await page
    .locator("#project-list")
    .getByText("Consumer-E2E-Projekt", { exact: true })
    .click();
  const brief = page.locator("#project-detail .project-brief");
  await brief.waitFor();
  await brief.getByText("Überfällige Consumer-E2E-Aufgabe", { exact: true }).waitFor();
  await brief.getByText(/Rückmeldung oder Abhängigkeit/).waitFor();
  await brief.getByText(/Blockade/).waitFor();
  await brief.getByText("Budget freigegeben", { exact: true }).waitFor();

  await page.locator('button[data-view="proposals"]').click();
  await page.getByText(/Hier wartet alles, was deine Entscheidung braucht/).waitFor();
  await page.getByText("Gilt das noch?", { exact: true }).waitFor();
  await page
    .getByText("Consumer-E2E-Fakt gilt weiterhin.", { exact: true })
    .waitFor();
  await page.getByRole("button", { name: "Gilt noch" }).waitFor();
  await page.getByRole("button", { name: "Stimmt nicht mehr" }).waitFor();

  // Tastaturweg durch die vier Hauptbereiche.
  await page.locator('button[data-view="dashboard"]').focus();
  await page.keyboard.press("Tab");
  assert.equal(
    await page.locator('button[data-view="projects"]').evaluate((node) => node === document.activeElement),
    true
  );

  console.log("Consumer-Chief-of-Staff: Tagesfokus, Projektbriefing und Entscheidungsweg bestanden.");
} finally {
  await browser.close();
  await api.dispose();
}
