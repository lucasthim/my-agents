---
name: code-quality-audit
description: Review code changes for code quality, core abstraction integrity, architecture smells, unwanted compatibility shims, dirty workarounds, excessive complexity, low cohesion, and incoherent contracts. Use this skill whenever the user asks to review current changes, audit a branch, check architecture quality, find workaround debt, simplify a feature branch, validate abstractions, assess cyclomatic or cognitive complexity, decide whether a change needs a shim or a clean break, or produce a handoff list of code-quality findings — even when they describe it as "look this over", "is this clean", "smell test", "second pair of eyes on this branch", or "before I merge".
---

# Code Quality Audit

A senior-reviewer pass that classifies every finding by lifecycle (shipped vs branch-local) so the recommended fix matches reality: compatibility paths for code already in production, direct cleanup for code that never left the branch.

## First Step: Ask For Scope

Before inspecting files or running repository analysis, ask the user which scope to review. Do not start the dive until they answer.

In Claude Code, use the `AskUserQuestion` tool with a single question:

> What should I review?
> - **Current changes** — staged and unstaged local diffs only.
> - **Current branch** — all changes on this branch since the merge base.

For other agents, ask the same question and wait for the answer before proceeding. The scope drives every subsequent step: which diffs to load, which base commit to compare against, and whether to inspect uncommitted hunks.

## Lifecycle Framing — Read This First

The central question for every finding is whether the affected contract is already merged/released or only exists on the current branch. That distinction determines the right fix.

- **Shipped or merged**: production data, released clients, sibling merged branches, or applied migrations may depend on the old contract. Prefer safe forward migrations, backfills, feature flags, adapters, deprecation paths, and dual reads/writes. Never recommend editing an already-applied production migration.
- **Branch-local**: only the working branch knows about the old shape. Prefer direct contract changes. Recommend deleting obsolete aliases, shims, compatibility wrappers, fallback readers, and back-and-forth development migrations. Rewrite noisy migration chains into a clean final sequence. Tests should validate the intended final behavior, not obsolete branch history.

Every shim, fallback, dual field, alias, or backwards-compatible branch needs a stated reason: production data, external client compatibility, staged rollout, or cross-branch dependency. If no such reason exists, flag it for removal.

When uncertain about lifecycle, state what evidence is missing (e.g., "no merge to main yet — assuming branch-local") and which assumption is safer to make.

## Review Stance

Act as a senior code-quality and architecture reviewer. Do not fix code unless the user explicitly asks for implementation. Produce findings that are concrete, evidenced (file:line), and actionable.

Prioritize substance:

- Broken or muddled core abstractions.
- Contracts that drift across API, sync, DTO, schema, DB, UI, or tests.
- Compatibility shims or fallbacks that only preserve branch-local history.
- Dirty workarounds, silent failure paths, swallowed promises, and broad catch blocks.
- High cyclomatic or cognitive complexity in changed code.
- Low cohesion: modules doing unrelated work; hooks/components owning too many responsibilities; services mixing orchestration, validation, persistence, and presentation shaping.
- Low coherence: names, concepts, and boundaries that do not match the domain model.
- Architecture smells: duplicated business rules, state-machine bypasses, scattered invariants, leaky abstractions, over-broad queries, hidden coupling, test-only design pressure.
- Performance risks introduced by the change — N+1 loops, serial async work, broad client-side filtering, cache invalidation gaps, sync amplification.

Avoid reporting pure preference, formatting, or style issues unless they reveal a maintainability or correctness problem. See `references/patterns-catalog.md` for the full checklist.

## Evidence Workflow

Start with repository orientation:

- `git status --short --branch`
- For **current changes**: inspect `git diff --stat`, `git diff`, and `git diff --cached`.
- For **current branch**: infer the base with upstream/main/dev/master candidates, then inspect `git merge-base`, `git diff --stat <base>...HEAD`, and per-file diffs as needed.
- Use `rg` (ripgrep) and targeted file reads to trace concepts across layers.

Trace each important contract end to end:

- Database entity/migration shape.
- Backend DTOs, services, guards, sync handlers, validators, tests.
- Shared package types and schemas.
- Frontend API clients, hooks, state, UI flows, tests.
- Documentation only when it reveals stale assumptions or affects implementation.

Prefer file:line evidence. If line numbers shift between runs, cite the closest stable symbol and path.

## Multi-Agent Orchestration (Claude Code Only)

In Claude Code, run discovery and adversarial review as parallel agent teams. This produces deeper coverage and catches blind spots a single reviewer would miss.

**Why two phases**: discovery agents pattern-match aggressively against their specialty and tend to over-report. Adversarial agents stress-test those findings, calibrate severity, hunt for misses, and produce the consolidated report. The split mirrors how strong human reviews work — one pass to surface everything plausible, a second pass to keep only what survives scrutiny.

### Phase 1 — Discovery Team (6 parallel agents)

Spawn all six discovery agents in a single message with parallel `Agent` tool calls so they run concurrently. Use `subagent_type: Explore` for read-only inspection. Each agent receives:

- The agreed scope (current changes or current branch with base commit).
- A pointer to their role brief in `references/discovery-agents.md`.
- A request to return findings as a structured list (severity, title, file:line evidence, problem, lifecycle assumption).

The six discovery roles:

1. **Contract & Schema Detective** — drift across DB, DTO, sync payload, validator, UI type, test fixture.
2. **Abstraction Integrity Reviewer** — broken or leaky core abstractions, generic helpers that know too much, layer-boundary violations.
3. **Complexity & Workaround Hunter** — cyclomatic/cognitive spikes, dirty workarounds (`any`, swallowed errors, magic delays, broad catches, test-only branches, TODOs with production behavior).
4. **Cohesion & Coherence Auditor** — modules doing unrelated work, hooks/components with too many responsibilities, naming inconsistencies, domain-vocabulary drift.
5. **Lifecycle & Shim Classifier** — branch-local backwards compatibility, development migration churn, dual payload shapes, fallback readers preserving previous branch commits.
6. **Performance & Hidden Coupling Sentinel** — N+1, serial async, broad scans + client filtering, cache invalidation gaps, scattered invariants, state-machine bypasses.

Full briefs (mission, signals to look for, evidence requirements, return format) are in `references/discovery-agents.md`. Read that file and pass the relevant section verbatim to each subagent so their prompts are self-contained.

### Phase 2 — Adversarial Review Team (6 parallel agents)

After all discovery agents return, spawn the six adversarial agents in a single message with parallel `Agent` calls. Pass them the merged discovery findings plus the original scope.

The six adversarial roles:

1. **False Positive Auditor** — for each finding, look for context that justifies the pattern. Kill noise.
2. **Severity Calibrator** — re-rate every finding on P0/P1/P2/P3 with explicit reasoning. Catch both under-rated risks and over-rated noise.
3. **Lifecycle Verifier** — re-check every shipped-vs-branch-local classification with git evidence (merge state, applied migrations, deployment markers).
4. **Missed Issue Detector** — independent scan of the diff that ignores the discovery output. Surface what slipped through.
5. **Recommendation Quality Reviewer** — critique each recommendation for actionability and safety. Catch unsafe suggestions like editing applied migrations or removing shims that are load-bearing.
6. **Synthesizer & Final Report Builder** — merge findings, resolve contradictions, deduplicate, and produce the final structured output described under "Output Format" below.

Full briefs in `references/adversarial-agents.md`. Same pattern: read the file and pass each role's section to the matching subagent.

### Single-Agent Fallback

If parallel sub-agents are unavailable (smaller harnesses, or the user explicitly asks for a quick read), do the work sequentially in this order: orientation → contract trace → complexity/workaround scan → cohesion/coherence pass → lifecycle classification → adversarial self-review → final report. The output format below applies either way.

## Unwanted Pattern Checklist (Summary)

The categories below are the surface area. The detailed catalog with concrete examples lives in `references/patterns-catalog.md` — read it when you need to refresh a specific category during review.

- Branch-local backwards compatibility.
- Development migration churn.
- Contract splits across layers.
- Abstraction erosion.
- Complexity spikes.
- Dirty workarounds.
- Low cohesion / coherence.
- Hidden performance issues.

## Output Format

Lead with findings. Use severity labels:

- **P0**: data loss, security, production outage, or unrecoverable migration risk.
- **P1**: likely correctness bug, broken contract, serious architecture regression, or high cleanup cost if merged.
- **P2**: maintainability, complexity, performance, or workaround debt that should be fixed before merge.
- **P3**: minor cleanup with low risk.

For each finding include:

- Severity and title.
- **Evidence**: file:line references.
- **Problem**: what is wrong and why it matters.
- **Lifecycle**: shipped/merged vs branch-local assumption, with the reasoning.
- **Recommended action**: compatibility path for shipped work; direct cleanup for branch-local work.

Then close with:

- **Scope reviewed**: current changes or branch, base commit if branch review.
- **Verification performed**: commands run and notable limits.
- **Non-findings or intentional tradeoffs**, if useful.

If there are no findings, say that clearly and list residual risks or areas not reviewed.
