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
const approval = (await call("GET", "/approvals")).find(
  (item) => item.id === approvalId,
);
assert.ok(approval, `Freigabe ${approvalId} fehlt`);
assert.equal(approval.confirmation_phrase, recipient);

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

  // Ausschließlich die normale Freigabekarte wird benutzt. Der Workflow-
  // Endpunkt zur manuellen Auflösung kommt in diesem Lauf nicht vor.
  await page.locator('button[data-view="chat"]').click();
  const approvalCard = page
    .locator("#approvals .approval")
    .filter({ hasText: recipient });
  await approvalCard.waitFor();
  await approvalCard.locator("label.confirm input").fill(recipient);
  await approvalCard.getByRole("button", { name: "Ausführen", exact: true }).click();
  await approvalCard.waitFor({ state: "detached" });

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
  "Private-Beta-Runtime bestanden: Token, Consumer-UI, Graph, atomarer Freigabeweg, Workflow und Expertenansicht laufen gemeinsam."
);
