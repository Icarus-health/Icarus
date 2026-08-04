import assert from "node:assert/strict";
import { chromium, request } from "playwright";

const baseURL = process.env.ICARUS_E2E_BASE ?? "http://127.0.0.1:8765";
const token = process.env.ICARUS_E2E_TOKEN ?? "rauchtest";
const marker = Date.now().toString(36);

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

const unauthorised = await request.newContext({ baseURL });
assert.equal((await unauthorised.get("/private-beta/status")).status(), 401);
await unauthorised.dispose();

const project = await call("POST", "/projects", {
  name: `Private-Beta-E2E-${marker}`,
  description: "Gemeinsamer Lauf aus Consumer-UX, Graph und Workflows.",
});
await call("POST", "/tasks", {
  title: `Integration prüfen ${marker}`,
  project_id: project.id,
});
await call("POST", "/notes", {
  title: `Private-Beta-Entscheidung ${marker}`,
  body: "Der Graph bleibt eine Projektion, Workflows bleiben policy-gebunden.",
  kind: "decision",
  project_id: project.id,
});
await call("POST", "/episodes", {
  title: `Private-Beta-Ereignis ${marker}`,
  body: "Sören prüft die gemeinsame Runtime.",
  kind: "event",
  project_id: project.id,
  participants: ["Sören"],
});

const graph = await call("POST", "/graph/rebuild");
assert.ok(graph.entities >= 4, `Zu wenige Entitäten: ${JSON.stringify(graph)}`);
assert.ok(graph.edges >= 2, `Zu wenige Kanten: ${JSON.stringify(graph)}`);

const workflowId = `wf-private-beta-${marker}`;
await call("POST", "/workflows", {
  id: workflowId,
  name: "Private-Beta-Warteablauf",
  steps: [
    {
      id: "wait",
      kind: "wait_until",
      run_at: new Date(Date.now() + 86_400_000).toISOString(),
      action_class: "read",
    },
  ],
});
const workflow = await call("POST", `/workflows/${workflowId}/tick`);
assert.equal(workflow.state, "waiting_time");

const approvalWorkflowId = `wf-private-beta-approval-${marker}`;
const recipient = `private-beta-${marker}@example.invalid`;
await call("POST", "/workflows", {
  id: approvalWorkflowId,
  name: "Private-Beta-Freigabeweg",
  steps: [
    {
      id: "send",
      kind: "invoke",
      tool: "mail_senden",
      arguments: {
        to: recipient,
        subject: `Private-Beta-E2E ${marker}`,
        body: "Lokaler Test ohne eingerichteten Versandkanal.",
      },
      action_class: "outward",
    },
  ],
});
const waitingApproval = await call(
  "POST",
  `/workflows/${approvalWorkflowId}/tick`,
);
assert.equal(waitingApproval.state, "waiting_approval");
const approvalId = waitingApproval.steps[0].approval_ids[0];
const pendingApprovals = await call("GET", "/approvals");
const approval = pendingApprovals.find((item) => item.id === approvalId);
assert.ok(approval, `Freigabe ${approvalId} fehlt`);
assert.equal(approval.confirmation_phrase, recipient);

// Ein alter bestätigter Fakt erzeugt ohne Modell einen echten
// Gedächtnisvorschlag. Damit prüft derselbe Browserlauf beide Abschnitte der
// Entscheidungsinbox statt künstliche DOM-Karten einzusetzen.
const old = new Date(Date.now() - 400 * 86_400_000).toISOString();
const assertionText = `Private-Beta-Fakt ${marker} gilt weiterhin.`;
await call("POST", "/assertions", {
  statement: assertionText,
  kind: "state",
  provenance: {
    source_type: "user_stated",
    source_ref: `private-beta-e2e:${marker}`,
    captured_at: old,
  },
  confidence: 1.0,
});
const consolidation = await call("POST", "/consolidate", {
  limit: 20,
  with_model: false,
});
assert.ok(consolidation.confirmations >= 1, JSON.stringify(consolidation));
const proposalCounts = await call("GET", "/proposals/counts");
assert.ok(proposalCounts.pending >= 1, JSON.stringify(proposalCounts));
const expectedDecisionCount = pendingApprovals.length + proposalCounts.pending;

const status = await call("GET", "/private-beta/status");
assert.equal(status.stage, "private_beta");
assert.equal(status.graph.ready, true);
assert.ok(status.graph.entities >= graph.entities);
assert.ok(status.workflows.total >= 2);
assert.equal(typeof status.model_harness.active, "boolean");
assert.equal(typeof status.browser.active, "boolean");

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
try {
  await page.goto(`${baseURL}/?token=${encodeURIComponent(token)}`, {
    waitUntil: "domcontentloaded",
  });
  await page.locator("#status.ready").waitFor();

  // Das Gespräch zeigt keine zweite Freigabekarte. Es weist auf den einzigen
  // verantwortlichen Ort hin und wechselt über den sichtbaren Nutzerweg.
  await page.locator('button[data-view="chat"]').click();
  assert.equal(await page.locator("#view-chat .approval").count(), 0);
  const chatHint = page.locator("#chat-decision-hint");
  await chatHint.waitFor({ state: "visible" });
  await chatHint
    .getByRole("button", { name: "Unter Entscheiden prüfen", exact: true })
    .click();

  await page
    .getByRole("heading", { name: "Aktionen freigeben", exact: true })
    .waitFor();
  await page
    .getByRole("heading", { name: "Gedächtnis prüfen", exact: true })
    .waitFor();
  await page.waitForFunction(
    (expected) =>
      document.querySelector("#proposals-badge")?.textContent.trim() ===
      String(expected),
    expectedDecisionCount,
  );

  // Jede Approval-ID erscheint im gesamten DOM genau einmal und wird über die
  // sichtbare Karte eingelöst, nie per direktem E2E-Endpunktaufruf.
  const approvalCard = page
    .locator("#decision-action-section #approvals .approval")
    .filter({ hasText: recipient });
  await approvalCard.waitFor();
  assert.equal(
    await page.locator(".approval").filter({ hasText: recipient }).count(),
    1,
  );

  const proposalCard = page
    .locator("#decision-memory-section #proposal-list .proposal")
    .filter({ hasText: assertionText });
  await proposalCard.waitFor();
  await proposalCard
    .getByRole("button", { name: "Gilt noch", exact: true })
    .click();
  await proposalCard.waitFor({ state: "detached" });
  await page.waitForFunction(
    (expected) =>
      document.querySelector("#proposals-badge")?.textContent.trim() ===
      String(expected),
    expectedDecisionCount - 1,
  );

  await approvalCard.locator("label.confirm input").fill(recipient);
  await approvalCard
    .getByRole("button", { name: "Ausführen", exact: true })
    .click();
  await approvalCard.waitFor({ state: "detached" });
  await page.waitForFunction(
    (expected) =>
      document.querySelector("#proposals-badge")?.textContent.trim() ===
      String(expected),
    expectedDecisionCount - 2,
  );
  assert.equal(await page.locator("#view-chat .approval").count(), 0);

  const failedWorkflow = await call("GET", `/workflows/${approvalWorkflowId}`);
  assert.equal(failedWorkflow.state, "needs_reconciliation");
  assert.equal(failedWorkflow.steps[0].state, "needs_reconciliation");
  const matchingAudit = (await call("GET", "/audit?limit=100")).filter(
    (entry) =>
      entry.tool === "mail_senden" &&
      entry.approved_by === "user" &&
      entry.arguments.to === recipient,
  );
  assert.equal(matchingAudit.length, 1, JSON.stringify(matchingAudit));
  assert.equal(matchingAudit[0].outcome, "failed");

  await page.locator('button[data-view="projects"]').click();
  await page
    .locator("#project-list")
    .getByText(`Private-Beta-E2E-${marker}`, { exact: true })
    .waitFor();

  // Einrichtung liegt absichtlich unter „Mehr“. Der Test geht denselben Weg
  // wie ein normaler Nutzer, statt ein verstecktes Element direkt anzuklicken.
  await page.locator(".nav-more > summary").click();
  await page.locator('button[data-view="setup"]').click();
  const expert = page.locator("#model-harness-expert");
  await expert.waitFor();
  await expert.getByText("Modellsteuerung für Experten", { exact: true }).waitFor();
} finally {
  await browser.close();
  await api.dispose();
}

console.log(
  "Private-Beta-Runtime bestanden: Token, Consumer-UI, gemeinsame Entscheidungsinbox, Graph, atomarer Freigabeweg, Workflow und Expertenansicht laufen gemeinsam."
);
