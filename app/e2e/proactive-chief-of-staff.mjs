import assert from "node:assert/strict";
import { chromium, request } from "playwright";

const baseURL = process.env.ICARUS_E2E_BASE ?? "http://127.0.0.1:8765";
const token = process.env.ICARUS_E2E_TOKEN ?? "rauchtest";
const marker = Date.now().toString(36);
const urgentTitle = `Proaktiver Hinweis ${marker}`;

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

await call("POST", "/tasks", {
  title: urgentTitle,
  due: new Date(Date.now() - 10 * 86_400_000).toISOString(),
});
for (let index = 0; index < 7; index += 1) {
  await call("POST", "/tasks", {
    title: `Aufmerksamkeitsbudget ${marker}-${index}`,
    due: new Date(Date.now() + (index + 1) * 86_400_000).toISOString(),
  });
}

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
try {
  await page.goto(`${baseURL}/?token=${encodeURIComponent(token)}`, {
    waitUntil: "domcontentloaded",
  });
  await page.locator("#status.ready").waitFor();
  const panel = page.locator("#proactive-chief");
  await panel.waitFor();
  await panel.getByRole("heading", { name: "Was jetzt Aufmerksamkeit verdient" }).waitFor();

  const items = panel.locator("#attention-list > li.attention-item");
  await panel.getByText(urgentTitle, { exact: true }).waitFor();
  assert.ok((await items.count()) <= 5, "Mehr als fünf proaktive Hinweise sichtbar");

  const urgent = items.filter({ hasText: urgentTitle });
  await urgent.getByText(/überfällig/i).waitFor();
  await urgent.getByText("Nächster Schritt", { exact: true }).waitFor();
  await urgent.getByRole("button", { name: "Morgen" }).click();
  await panel.getByText(urgentTitle, { exact: true }).waitFor({ state: "detached" });

  const attention = await call("GET", "/chief-of-staff/attention?limit=10");
  assert.equal(
    attention.some((signal) => signal.title === urgentTitle),
    false,
    "Zurückgestellter Hinweis blieb im API-Ergebnis"
  );

  console.log(
    "Proaktiver Chief of Staff bestanden: begründete Priorität, Fünferbudget und Zurückstellen funktionieren."
  );
} finally {
  await browser.close();
  await api.dispose();
}
