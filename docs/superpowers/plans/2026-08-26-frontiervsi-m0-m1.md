# FrontierVSI M0/M1 Implementation Plan

Goal: establish the approved specification baseline and implement the durable deterministic book-project core before any LLM/multi-agent workflow.

M1 tasks: project layout → deterministic hashing → immutable revision store → mutation lock/event log → Gate fingerprint freshness → durable request idempotency → crash-recovery journal → doctor/JSON CLI → packaging integration → full verification.

Global constraints: do not modify `frontier_agent/`, `apodex/`, `plugins/tools/`, or existing workflows; LLM output is never authoritative; revisions are immutable; mutations are revision-aware and serialized; approvals are hash-bound; no browser/MCP/OpenClaw adapter/Context Pack or Agent roles in M1.

The complete normative TDD plan is stored in `horse/FrontierVSI-specs/docs/superpowers/plans/2026-08-26-frontiervsi-m0-m1.md`.
