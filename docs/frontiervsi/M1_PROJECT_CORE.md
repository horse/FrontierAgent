# FrontierVSI M1 — Durable Project Core

M1 contains no LLM calls or AgentBus orchestration. It establishes the deterministic book-project substrate required by later milestones.

Core invariants:

- canonical revisions are immutable snapshots;
- `.frontiervsi/project.json` is the authoritative revision pointer;
- every mutation is guarded by `expected_revision` and an exclusive project lock;
- artifact identity is SHA-256 over exact bytes;
- Gate PASS is effective only while its dependency fingerprint matches;
- request IDs are durable and idempotent;
- transaction journals recover the pointer-switched/event-not-appended crash window;
- `doctor` fails closed on integrity violations;
- M1 exposes `frontier-vsi init|status|doctor --json`;
- no existing FrontierAgent core, AgentBus, tool, or workflow behavior is changed.

The normative detailed contract lives in `horse/FrontierVSI-specs/docs/frontiervsi/M1_PROJECT_CORE.md`.
