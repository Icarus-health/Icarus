import assert from "node:assert/strict";
import { readdir } from "node:fs/promises";

const baseURL = process.env.ICARUS_E2E_BASE ?? "http://127.0.0.1:8901";
const token = process.env.ICARUS_E2E_TOKEN ?? "macos-test";
const dataDir = process.env.ICARUS_E2E_DATA_DIR;

async function call(method, path, data, timeoutMs = 30_000) {
  const response = await fetch(`${baseURL}${path}`, {
    method,
    headers: {
      "content-type": "application/json",
      "x-icarus-token": token,
    },
    body: data === undefined ? undefined : JSON.stringify(data),
    signal: AbortSignal.timeout(timeoutMs),
  });
  const text = await response.text();
  assert.equal(
    response.ok,
    true,
    `${method} ${path} scheiterte mit ${response.status}: ${text}`
  );
  return text ? JSON.parse(text) : null;
}

async function waitForHealth() {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    try {
      const health = await call("GET", "/health", undefined, 2_000);
      if (health.status === "ok") return;
    } catch {
      // PyInstaller braucht auf einem kalten Runner einen Moment zum Starten.
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error("Der gebündelte Icarus-Sidecar wurde nicht rechtzeitig bereit.");
}

await waitForHealth();
await call("PUT", "/setup", { onboarded: true, provider: "", model: "" });

const assertion = await call("POST", "/assertions", {
  statement: "Gebündelter Bestand vor Sicherung.",
  kind: "identity",
  provenance: { source_type: "user_stated" },
});
const project = await call("POST", "/projects", {
  name: "macOS-Projekt vor Sicherung",
  area: "Release-Gate",
});
await call("POST", "/tasks", {
  title: "macOS-Aufgabe vor Sicherung",
  project_id: project.id,
});
await call("POST", "/notes", {
  title: "macOS-Notiz vor Sicherung",
  body: "Muss nach dem Restore vorhanden sein.",
  kind: "decision",
  project_id: project.id,
});
await call("POST", "/episodes", {
  title: "macOS-Episode vor Sicherung",
  body: "Rohmaterial aus dem gebündelten Sidecar.",
  kind: "observation",
  project_id: project.id,
});
await call("POST", "/tools/aufgabe_anlegen", {
  titel: "macOS-Audit-Aufgabe vor Sicherung",
  projekt: "macOS-Projekt vor Sicherung",
});

const created = await call("POST", "/backups");
assert.match(created.name, /^icarus-.*\.zip$/);
const backups = await call("GET", "/backups");
assert.equal(backups[0].name, created.name);
assert.equal(backups[0].kind, "installation");

await call("POST", "/assertions", {
  statement: "Gebündelter Bestand nach Sicherung — muss verschwinden.",
  kind: "identity",
  provenance: { source_type: "user_stated" },
});
const changedProject = await call("POST", "/projects", {
  name: "macOS-Projekt nach Sicherung",
  area: "Release-Gate",
});
await call("POST", "/tasks", {
  title: "macOS-Aufgabe nach Sicherung",
  project_id: changedProject.id,
});
await call("PUT", "/setup", {
  provider: "ollama",
  model: "llama3.1",
});

const restored = await call(
  "POST",
  "/backups/restore",
  { name: created.name },
  60_000
);
assert.equal(restored.restored, created.name);
assert.equal(restored.assertions, 1);

const exported = await call("GET", "/export");
assert.deepEqual(
  exported.assertions.map((item) => item.statement),
  [assertion.statement]
);
assert.deepEqual(
  (await call("GET", "/projects?all=true")).map((item) => item.name),
  ["macOS-Projekt vor Sicherung"]
);

const tasks = await call("GET", "/tasks?all=true");
assert.equal(tasks.some((item) => item.title === "macOS-Aufgabe vor Sicherung"), true);
assert.equal(tasks.some((item) => item.title === "macOS-Audit-Aufgabe vor Sicherung"), true);
assert.equal(tasks.some((item) => item.title === "macOS-Aufgabe nach Sicherung"), false);

assert.deepEqual(
  (await call("GET", "/notes")).map((item) => item.title),
  ["macOS-Notiz vor Sicherung"]
);
assert.deepEqual(
  (await call("GET", "/episodes?limit=200")).map((item) => item.title),
  ["macOS-Episode vor Sicherung"]
);

const setup = await call("GET", "/setup");
assert.equal(setup.settings.provider, "");
assert.equal(setup.settings.model, "");

const audit = JSON.stringify(await call("GET", "/audit?limit=100"));
assert.match(audit, /macOS-Audit-Aufgabe vor Sicherung/);

if (dataDir) {
  const entries = await readdir(dataDir);
  assert.equal(
    entries.some((name) => name.startsWith("self-model.vor-wiederherstellung-")),
    true,
    "Der vorherige Selbstmodellstand wurde nicht beiseitegelegt."
  );
  assert.equal(
    entries.some((name) => name.startsWith(".icarus-recovery-")),
    true,
    "Der vollständige vorherige Arbeitsstand fehlt im Recovery-Ordner."
  );
}

console.log("Gebündelter macOS-Sidecar: Backup und Restore vollständig bestanden.");
