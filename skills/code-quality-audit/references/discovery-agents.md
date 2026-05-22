# Discovery Agents — Role Briefs

Six specialist agents run in parallel. Each pattern-matches aggressively against its specialty. Over-reporting is acceptable here; the adversarial team will calibrate. The goal is breadth — surface every plausible issue so nothing hides.

When dispatching a subagent, copy its role brief verbatim into the prompt and append:

- The agreed scope (current changes or current branch with base commit).
- The repo root (current working directory).
- The expected return format from "Common Return Format" at the bottom of this file.

Use `subagent_type: Explore` — these agents read, grep, and reason; they do not edit.

---

## Agent 1 — Contract & Schema Detective

**Mission**: Find contracts that drift across layers. A contract is any place a shape is declared or consumed: DB columns, entities, migrations, DTOs, request/response types, sync payloads, validators, frontend types, hooks that map server data to view models, test fixtures.

**Signals to look for**:

- A field is renamed/added/removed in one layer but not another.
- Frontend type requires a property the backend does not return (or vice versa).
- DB column allows a state the service-layer validator rejects, or the reverse.
- Sync schema version diverges from the entity it represents.
- Test fixtures assert old vocabulary while production code moved on.
- Optional/nullable mismatches between layers.
- Enum value drift (UI shows an enum the backend can't produce).
- Migration introduces a column but no service writes to it (orphan column), or a service reads a column no migration created (phantom column).
- Two layers parse the same payload with incompatible schemas.

**Evidence requirements**: cite the exact file:line for each side of the drift. If a contract is consumed in 3 places, list all 3.

**Out of scope**: pure style, naming preferences without drift, formatting.

**Tracing tactics**:

- For each changed entity/DTO/schema, grep its name across the repo.
- Follow data flow: DB → ORM → service → controller → sync handler → frontend client → hook → component.
- Compare migration up/down with the entity definition.

---

## Agent 2 — Abstraction Integrity Reviewer

**Mission**: Find broken or muddled core abstractions, leaky layer boundaries, and generic helpers that secretly know about specific features.

**Signals to look for**:

- A "shared" or "generic" utility imports from a feature module, or contains feature-specific branches.
- A domain service reaches into another module's internals instead of using its public API.
- An abstraction whose interface implies generality but whose only callsite is a single concrete use case.
- Hooks/components that combine routing, persistence, validation, and rendering concerns in one body.
- Layer violations: UI calls DB directly, controllers do business rule enforcement that belongs in a domain service, repositories format presentation data.
- Names that promise an abstraction the implementation doesn't deliver (e.g., `BasePolicy` with no subclasses, or `EventBus` that only ever has one publisher and one subscriber).
- Abstractions introduced "for future flexibility" with no current second use case.
- Inheritance hierarchies used to share code that should be composition.
- Interfaces with a single implementer where the indirection adds no value.

**Evidence requirements**: cite the abstraction's declaration file:line and the violating callsite(s) file:line.

**Out of scope**: pure naming preferences, micro-refactors that don't change the abstraction shape.

**Tracing tactics**:

- For each new abstraction or interface in the diff, find all callers. One caller usually means the abstraction is premature.
- For each "shared" or "common" file touched, check imports — does it depend on feature modules?

---

## Agent 3 — Complexity & Workaround Hunter

**Mission**: Find code that is hard to reason about, and find the dirty workarounds that signal the author gave up on a clean solution.

**Signals to look for — complexity**:

- Functions with high cyclomatic complexity (many branches, switch arms, ternaries) in changed code.
- Deeply nested conditionals (>3 levels).
- Large changed functions (>50 lines of net new logic) without clear sub-steps.
- Many boolean parameters or flags driving behavior.
- Duplicated switch/if-chains on the same discriminator across files.
- Multi-step flows without explicit state boundaries.

**Signals to look for — dirty workarounds**:

- `any`, `unknown` without narrowing, `as any` casts, `@ts-ignore`, `// eslint-disable` on substantive rules.
- Empty catch blocks, broad catches that re-log and swallow, ignored Promise returns (`void` on important promises, missing `await`).
- Magic delays (`setTimeout`, `sleep`, `await new Promise(r => setTimeout(r, 100))`) used as synchronization.
- Broad retries with no backoff or termination condition.
- Global mutable flags or module-level state introduced as a hack.
- Test-only branches in production code (`if (process.env.NODE_ENV === 'test')` outside test setup).
- TODOs that describe production behavior gaps.
- Feature flags that always evaluate one way.
- Hardcoded values that look like they should be config.
- Commented-out code left in.

**Evidence requirements**: cite file:line and quote the offending fragment when it's short.

**Out of scope**: stylistic preferences, complexity in unchanged code unless the change makes it worse.

**Tracing tactics**:

- Read each changed file fully when the diff touches non-trivial logic.
- Grep for `any`, `@ts-ignore`, `eslint-disable`, `TODO`, `FIXME`, `HACK`, `XXX` within changed files.

---

## Agent 4 — Cohesion & Coherence Auditor

**Mission**: Find modules whose responsibilities can't be named in one sentence, and find vocabulary inconsistencies that erode the mental model of the codebase.

**Signals to look for — cohesion**:

- A single file or module whose changes touch unrelated concerns (UI rendering AND data normalization AND HTTP plumbing).
- A hook or component that combines: routing, persistence, validation, side effects, presentation.
- A service that mixes orchestration, validation, persistence, and presentation shaping.
- A "helpers" or "utils" file that grows by accretion with unrelated functions.
- New code added to a file whose name no longer describes its contents.

**Signals to look for — coherence**:

- The same concept named differently across layers (`userId` vs `user_id` vs `accountId` vs `ownerId` for the same thing).
- A concept renamed in one layer but not others.
- Domain vocabulary that doesn't match how the team or product describes the thing.
- Mixed singular/plural naming for the same entity.
- Inconsistent verb choice for the same operation (`fetch`/`load`/`get`/`retrieve` for the same kind of read).
- Boolean names that confuse the reader (`isNotDisabled`, double negatives).
- Abbreviations used inconsistently (`ctx` here, `context` there).

**Evidence requirements**: for cohesion, cite the file and list the unrelated concerns it now handles. For coherence, cite at least two file:line pairs showing the inconsistency.

**Out of scope**: bikeshedding individual names that are fine in isolation.

**Tracing tactics**:

- For each touched file, ask: can I write one sentence that names what it owns? If not, that's a finding.
- For each new domain term in the diff, grep the repo for alternative spellings/synonyms.

---

## Agent 5 — Lifecycle & Shim Classifier

**Mission**: Classify each change as shipped/merged vs branch-local, and surface compatibility code that only exists to preserve previous commits on the same branch.

**Why this matters**: branch-local backwards compatibility burns review time, hides real intent, and makes the final architecture harder to read. It should usually be deleted before merge.

**Signals to look for**:

- Field aliases or dual property names where one is the "new" name and one is the "old" name from earlier in the same branch.
- Route aliases for paths that were just renamed.
- Dual payload shapes accepted by a handler (old format + new format) where the only producer of the old format is an earlier commit on this branch.
- Fallback readers in sync code that handle a schema version no released client ever sent.
- Migration chains that create an intermediate table/column and later drop or rename it — collapse them into one final migration if nothing has shipped.
- Hand-edited migration timestamps used to reorder unreleased migrations.
- Normalization migrations that only normalize data created by earlier commits on this branch.
- Deprecated-marked exports that have no external consumer.
- Compatibility wrappers around functions whose only callers are also in the diff.

**Evidence requirements**: cite the shim's file:line, the original (now-obsolete) producer/consumer file:line, and a git evidence line (e.g., "the old shape was added in commit X on this branch, not in main").

**How to determine lifecycle**:

- Run `git log --oneline <base>..HEAD -- <file>` to see if the contract change is purely on-branch.
- Check `git log main -- <file>` (or equivalent base branch) to see whether the old shape ever reached the base.
- For migrations, check if they have been applied to any deployed environment (`migrations` table state if available, otherwise look for `.applied` markers or deployment manifests).
- If unclear, state the assumption explicitly.

**Out of scope**: shims that exist for clear external reasons (released SDK clients, deployed databases, cross-branch dependencies). Note these as intentional.

---

## Agent 6 — Performance & Hidden Coupling Sentinel

**Mission**: Find performance regressions and hidden coupling introduced by the change.

**Signals to look for — performance**:

- N+1: per-row awaits, per-row queries inside loops, per-item HTTP fetches.
- Serial async work that could be parallelized (a chain of `await`s with no data dependency between them).
- Broad database or IndexedDB scans followed by client-side filtering.
- Repeated parsing of the same payload.
- Accidental polling (intervals introduced and never cleared, or polling where push/subscription is available).
- Missing indexes for new access paths (new WHERE clauses on un-indexed columns).
- Excessive re-renders from inline object/array literals or unstable callbacks in dependency arrays.
- Large synchronous work on the main thread or request thread.
- Cache invalidation gaps: a write that should invalidate caches but doesn't, or a read that bypasses cache for no reason.
- Sync amplification: a single user action triggering more network traffic than necessary (e.g., a save that triggers full sync instead of delta).

**Signals to look for — hidden coupling**:

- Implicit ordering dependencies between calls (function A must be called before B, but nothing enforces it).
- State-machine bypasses: code that mutates state outside the legal transitions.
- Scattered invariants: the same business rule re-checked in 3 places, none of them authoritative.
- Modules that share state through global singletons or module-level mutables.
- Event handlers that depend on the order other handlers ran in.
- Tests that pass only because of side effects from earlier tests.
- New shared mutable state introduced in the diff.

**Evidence requirements**: cite file:line. For performance, briefly state the scale at which it bites (per request, per user, per item in a list of N).

**Out of scope**: micro-optimizations with no measured cost; performance issues in unchanged code unless the change makes them worse.

---

## Common Return Format

Each discovery agent returns a list of findings in this shape:

```
[AGENT_NAME] Findings

1. [Tentative severity] Short title
   - Evidence: path/to/file.ts:123 (and others if relevant)
   - Problem: what's wrong and why it matters
   - Lifecycle hypothesis: shipped | branch-local | unknown — brief reasoning
   - Suggested action sketch: one line; the adversarial team will refine
   
2. ...
```

Then a closing line:

- **Coverage**: which directories/files I traced, and any I skipped with reasoning.
- **Confidence**: any findings I'm unsure about, flagged for adversarial review.

Severity at this stage is tentative. The Severity Calibrator (adversarial agent 2) re-rates everything.
