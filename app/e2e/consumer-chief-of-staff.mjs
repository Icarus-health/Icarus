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

// Der Test beginnt wie ein neuer Nutzer und darf keine technische Verbindung
// benötigen, um zum ersten Nutzen zu gelangen.
await call("PUT", "/setup", { onboarded: false });
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
let dashboardRequests = 0;
page.on("request", (request) => {
  if (new URL(request.url()).pathname === "/dashboard") dashboardRequests += 1;
});

async function waitForDashboardQuietWindow() {
  const deadline = Date.now() + 5_000;
  let observedRequests = dashboardRequests;
  let lastRequestAt = Date.now();

  while (Date.now() < deadline) {
    await page.waitForTimeout(100);
    if (dashboardRequests !== observedRequests) {
      observedRequests = dashboardRequests;
      lastRequestAt = Date.now();
    }
    if (Date.now() - lastRequestAt >= 2_000) return observedRequests;
  }
  assert.fail("/dashboard wurde nicht für mindestens zwei Sekunden ruhig.");
}

try {
  await page.goto(`${baseURL}/?token=${encodeURIComponent(token)}`, {
    waitUntil: "domcontentloaded",
  });
  await page.locator("#status.ready").waitFor();

  const wizard = page.locator("#wizard");
  await wizard.waitFor({ state: "visible" });
  await wizard.getByRole("heading").waitFor();
  for (let step = 0; step < 5; step += 1) {
    await page.locator("#wizard-skip").click();
  }
  await wizard.waitFor({ state: "hidden" });
  const setup = await call("GET", "/setup");
  assert.equal(setup.settings.onboarded, true, "Onboarding wurde nicht abgeschlossen");

  const focus = page.locator("#daily-focus[data-ready='true']");
  await focus.waitFor();
  await focus.getByText("Überfällige Consumer-E2E-Aufgabe", { exact: true }).waitFor();
  await focus.getByText(/überfällig/).waitFor();

  // Der Tagesfokus verändert beim Rendern seinen eigenen DOM-Bereich. Diese
  // Mutation darf keinen weiteren Dashboard-Abruf auslösen. Das Quiet-Window
  // ist absichtlich länger als ein Animation-Frame und würde die frühere
  // Beobachtung des gesamten document.body zuverlässig sichtbar machen.
  await waitForDashboardQuietWindow();

  const beforeOwnMutation = dashboardRequests;
  await focus.evaluate((panel) => panel.append(document.createElement("span")));
  await page.waitForTimeout(300);
  assert.equal(
    dashboardRequests,
    beforeOwnMutation,
    "Eine Mutation im Tagesfokus darf keinen neuen /dashboard-Request auslösen."
  );

  await page.locator('button[data-view="projects"]').click();
  await page
    .locator("#project-list")
    .getByText("Consumer-E2E-Projekt", { exact: true })
    .click();
  const brief = page.locator("#project-detail .project-brief");
  await brief.waitFor();
  await brief.getByText("Überfällige Consumer-E2E-Aufgabe", { exact: true }).waitFor();
  await brief.getByText(/Rückmeldung oder Abhängigkeit/).waitFor();
  await brief
    .getByText("Eine mögliche Blockade ist im Projektkontext genannt.", { exact: true })
    .waitFor();
  await brief.getByText("Budget freigegeben", { exact: true }).waitFor();

  await page.locator('button[data-view="proposals"]').click();
  await page.getByText(/Hier wartet alles, was deine Entscheidung braucht/).waitFor();
  const proposals = page.locator("#proposal-list");
  await proposals.getByText("Gilt das noch?", { exact: true }).waitFor();
  await proposals
    .getByText("Consumer-E2E-Fakt gilt weiterhin.", { exact: true })
    .waitFor();
  await proposals.getByRole("button", { name: "Gilt noch" }).waitFor();
  await proposals.getByRole("button", { name: "Stimmt nicht mehr" }).waitFor();

  // Mehrfaches Wechseln nach „Heute“ darf nach Abschluss der expliziten
  // Aktualisierung keine weitere Abrufkaskade erzeugen.
  await page.locator('button[data-view="dashboard"]').click({ clickCount: 3 });
  await focus.waitFor();
  await waitForDashboardQuietWindow();

  // Tastaturweg durch die vier Hauptbereiche.
  await page.locator('button[data-view="dashboard"]').focus();
  await page.keyboard.press("Tab");
  assert.equal(
    await page.locator('button[data-view="projects"]').evaluate((node) => node === document.activeElement),
    true
  );

  console.log("Consumer-Chief-of-Staff: Onboarding, Tagesfokus, Projektbriefing und Entscheidungsweg bestanden.");
} finally {
  await browser.close();
  await api.dispose();
}
