# Icarus

**A personal, long-term AI operating layer.**

Icarus builds a *verifiable* digital model of its user, keeps that memory
independent of any single AI vendor, integrates current information, and
delegates or performs digital work under explicit control.

It is a **downloadable desktop app** — macOS first, Windows after. No Docker, no
server, no setup.

> **Status: early, but end-to-end.** You can talk to it, it remembers with
> provenance, it reaches for current information, and nothing leaves your
> machine without you confirming exactly what goes out. Missing: real mail and
> calendar channels, computer-use, and memory consolidation.

Detailed documentation is in German under [`docs/`](docs/).

## The four pillars

| # | Pillar | Status |
|---|---|---|
| 1 | Verifiable self-model — provenance, versioning, revocation | **Built.** Record, supersede, expire, revoke with cascade |
| 2 | Vendor-independent memory | **Built.** Local SQLite record; OpenAI-compatible, Anthropic or Ollama in front |
| 3 | Current information | **Partial.** Web fetch, files, time; mail and calendar missing |
| 4 | Controlled delegation and execution | **Built.** Action classes, dry-run approvals, append-only audit |

## How it is put together

```mermaid
flowchart LR
    U[User] --> UI[Tauri app]
    UI --> AG[Agent]
    AG -->|"only valid, sensitivity-filtered"| M[Model]
    M -->|"wants a tool"| P[Policy]
    P -->|read, write| R[Execute]
    P -->|outward| A{{"Approve<br/>with dry-run"}}
    A -->|confirmed| R
    R --> L[(Audit log)]
    R --> SQL[(SQLite record)]

    classDef built fill:#dff5e1,stroke:#3b7a4b,color:#14311d
    classDef gate fill:#f7ecd5,stroke:#a8621f,color:#3a2a12
    class UI,AG,M,P,R,L,SQL built
    class A gate
```

**The model cannot act.** It proposes tools; the policy layer decides. Reads run,
writes report afterwards, and anything leaving your machine requires you to
retype the recipient. Every outcome is logged — including the refusals.

**Two stores, deliberately.** The authoritative record lives in SQLite: exact,
addressable by ID, readable without invoking a model. cognee is only the
semantic index — hits are always resolved against the record, so the graph can
never invent a statement. Drop cognee and the record stays complete; only search
degrades to substring matching. That is pillar 2 made real rather than asserted.

Full picture: [`docs/01-architektur.md`](docs/01-architektur.md).

## Getting started

Requires Python 3.10+ and, for the app itself, Rust and Node.

```bash
make sidecar-dev     # memory core + dev dependencies (no cognee, fast)
make test            # 50 tests: memory rules, policy, audit, agent loop
make sidecar-run     # http://127.0.0.1:8765
```

The memory core needs **no API key and no model**. Recording, superseding,
revoking and exporting all work offline; the app says so rather than offering a
broken chat.

For conversation, point it at any provider — including a fully local one:

```bash
cp .env.example .env
# OpenAI:    OPENAI_API_KEY=...
# Anthropic: ANTHROPIC_API_KEY=...
# Ollama:    ICARUS_PROVIDER=ollama   (nothing else needed)
```

For semantic search across your memory:

```bash
make sidecar-full    # adds cognee (~950 MB, see ADR 0005)
```

### Running the app

```bash
make app-dev         # development, uses the sidecar from your PATH
make app-build       # bundles the sidecar and builds the app
```

`make app-build` produces a `.dmg` and `.app` on macOS. It must run **on** macOS
— a Mac bundle cannot be built from Linux or Windows. Shipping to users also
needs signing and notarisation; see [ADR 0006](docs/adr/0006-tauri-desktop.md).

## Verifying

```bash
make check           # tests + schema validation + cargo check
```

## Repository layout

```
sidecar/             Python: memory, policy, agent
  icarus_memory/
    model.py         Data model, mirrors schema/self-model.schema.json
    store.py         The rules: supersession, expiry, cascading revocation
    backends.py      SQLite (record) + cognee (semantic index)
    policy.py        Action classes, approval levels, constraints
    audit.py         Append-only log, enforced by SQLite triggers
    providers.py     OpenAI-compatible and Anthropic, one interface
    tools.py         Web, files, time, memory, outward actions
    agent.py         Ties it together; proposes, never executes directly
    server.py        Loopback-only HTTP API for the app
  tests/             50 tests, no network and no model required
app/                 Tauri desktop app (Rust shell, HTML/JS frontend)
schema/              Self-model JSON Schema and a worked example
docs/                Architecture, self-model, delegation, roadmap, ADRs
```

## Decisions

Two of these reverse earlier ones. The reasoning is kept rather than
overwritten — the same principle the self-model applies to its own data.

- **[ADR 0003](docs/adr/0003-kein-letta.md)** — Letta is not used. The survey
  recommended it; its repository is explicitly deprecated and its self-hosting
  successor is coupled to a vendor cloud.
- **[ADR 0005](docs/adr/0005-cognee-statt-mem0.md)** — cognee instead of Mem0.
  cognee's default stores are file-based, so it fits in an app; Mem0's server
  needs Postgres and pgvector.
- **[ADR 0006](docs/adr/0006-tauri-desktop.md)** — own Tauri app instead of a
  Docker stack. The self-model and the approval layer are not plugins; they
  touch every interaction.
- **[ADR 0004](docs/adr/0004-selbstmodell-schema.md)** — a purpose-built
  self-model schema, because no surveyed project provides cross-domain
  provenance.

Superseded: [ADR 0001](docs/adr/0001-ui-open-webui.md) (Open WebUI),
[ADR 0002](docs/adr/0002-memory-mem0.md) (Mem0).

## License

Apache-2.0 — see [LICENSE](LICENSE). Bundled third-party components keep their
own licenses.
