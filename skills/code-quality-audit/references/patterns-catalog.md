# Patterns Catalog — Extended Checklist

Concrete examples for the eight pattern categories named in SKILL.md. Use as a reference when a category needs a refresher mid-review. Not exhaustive — judgment still matters.

## 1. Branch-Local Backwards Compatibility

The most common waste in long-running feature branches. Code that exists only to keep earlier branch commits working. By definition, deletable.

**Concrete examples**:

- Dual property reads:
  ```ts
  const id = payload.userId ?? payload.user_id; // user_id was renamed earlier this branch
  ```
- Route aliases:
  ```ts
  @Get('users/:id')      // new
  @Get('user/:id')       // old, same handler, only old commits used it
  ```
- Schema fallback readers in sync code that accept a version no released client ever sent.
- Aliased exports for renamed symbols, with no external consumer.
- DTO compatibility wrappers that map between two shapes when both shapes are on-branch.
- Optional fields added "for backwards compatibility" with no migration path.
- `if (legacyMode)` branches where `legacyMode` is set by an earlier branch commit and nowhere else.

**How to spot**: grep the codebase for the "old" symbol. If every consumer is in the diff itself, the shim is branch-local.

**Recommendation pattern**: delete the shim, update consumers to use the canonical name, remove any "version check" branches.

## 2. Development Migration Churn

Migrations that create, modify, or undo state that never deployed. They make reviewers waste time tracing schema history that has no production meaning.

**Concrete examples**:

- A migration that adds column X, followed by a migration that renames X to Y, followed by a migration that drops X — all on the same branch, none applied.
- Hand-edited migration timestamps used to reorder unreleased migrations after a rebase.
- Normalization migrations whose source data was created by earlier migrations on the same branch.
- Down migrations that don't actually reverse the up, because the column was renamed mid-branch.
- Migrations that touch tables only populated by branch-local test fixtures.

**How to spot**: list all migrations added since the base. If their schemas conflict or supersede each other, and none are applied, they're candidates for squashing.

**Recommendation pattern**: squash into one clean final migration. Preserve the final schema state, drop the intermediate ones. If anyone has run them locally, document the reset step.

**Caution**: only squash migrations that were never run in any environment. If one developer ran them locally, they need to reset their DB; that's a coordination cost.

## 3. Contract Splits Across Layers

A "contract" is any shape (DB row, DTO, sync payload, frontend type, API response) that crosses a boundary. Drift means the layers disagree.

**Concrete examples**:

- Frontend type requires `email: string` but the backend DTO has `email: string | null`.
- DB column allows `status = 'archived'` but the service enum is `'active' | 'deleted'`.
- Sync schema version 5 produced by client, parsed as version 4 by server (or vice versa).
- Validator accepts a field shape the entity rejects.
- Test fixtures use the old vocabulary (`user_id`) while production code uses the new (`userId`).
- OpenAPI spec out of sync with controller signatures.
- A new enum value added to backend without updating the discriminated union in frontend types.

**How to spot**: for each touched field/type, follow it across layers. Grep its name and inspect every callsite.

**Recommendation pattern**: align on the final shape. For shipped contracts, version the change (add new, deprecate old). For branch-local contracts, just update the lagging layer.

## 4. Abstraction Erosion

Abstractions that no longer mean what they say. Boundaries that leak. "Shared" code that knows about specific features.

**Concrete examples**:

- A "generic" `PaginationHelper` that has a special case for the `users` table.
- A `BaseService` whose only subclass has the same methods but ignores most of the base.
- A repository that returns view-formatted strings instead of domain values.
- A controller that runs business rule checks the service layer should own.
- A hook `useUserData` that also handles routing, form validation, and toast notifications.
- An `EventBus` with one publisher and one subscriber that should have been a direct call.
- A `StrategyPattern` where the strategies are all selected by a compile-time flag, not runtime polymorphism.
- A new interface introduced "for future flexibility" with only one implementer in the diff.

**How to spot**: for each abstraction in the diff, count its implementers/callers. One implementer often means premature abstraction. Check if "shared" or "common" files import from feature modules.

**Recommendation pattern**: collapse the abstraction (inline the only implementer), or restore the boundary (move feature-specific logic out of "shared", split the multi-concern hook into focused pieces).

## 5. Complexity Spikes

Code that's hard to reason about because the structure obscures the intent.

**Concrete examples**:

- Functions with cyclomatic complexity >10 (many branches, switch arms).
- Functions >50 lines of net new logic without clear sub-steps.
- Deeply nested conditionals (>3 levels deep).
- Boolean parameter explosion: `doThing(true, false, true, true, false)`.
- Duplicate switch chains on the same discriminator in 3+ files.
- Multi-step flows where state transitions aren't named.
- Inline destructuring with default values inside complex predicates.
- Reduce/map/filter chains spanning many lines without intermediate names.
- Regexes >40 characters without a comment.

**How to spot**: read each changed function. If you have to re-read, that's a finding. If the function does multiple things, count them — the count is the cognitive load.

**Recommendation pattern**: extract sub-steps, replace boolean parameters with named options, name intermediate values, push state transitions into a state machine or strategy.

## 6. Dirty Workarounds

The author hit a wall and pasted in a workaround. Common signal: a comment explaining why the workaround is necessary.

**Concrete examples — typing escape hatches**:

- `any` introduced where a real type would work.
- `as unknown as X` casts.
- `@ts-ignore` or `@ts-expect-error` without an issue link.
- `// eslint-disable-next-line` on rules like `no-floating-promises` or `no-explicit-any`.

**Concrete examples — error handling**:

- `catch {}` — empty catch.
- `catch (e) { console.log(e) }` — log and continue.
- `try/catch` that catches everything to hide a specific failure.
- Important promises returned to `void` or not awaited.
- Retries with no backoff or termination.

**Concrete examples — synchronization**:

- `await new Promise(r => setTimeout(r, 100))` as a "wait for state to settle".
- `requestAnimationFrame` chains used as synchronization rather than rendering.
- Polling where a subscription/event is available.

**Concrete examples — state pollution**:

- Module-level mutable state introduced as a workaround.
- Global flags toggled at module load time.
- Test-only branches in production code (`if (process.env.NODE_ENV === 'test')`).
- TODOs that describe a real production gap.

**How to spot**: grep the changed files for `any`, `@ts-ignore`, `eslint-disable`, `TODO`, `FIXME`, `HACK`, `XXX`, `setTimeout`. Read every match.

**Recommendation pattern**: replace the workaround with the real fix. If the workaround is genuinely required (third-party type bug, race fix in legacy code), document why and link an issue.

## 7. Low Cohesion / Coherence

Cohesion is about a unit doing one thing. Coherence is about names and concepts being consistent.

**Cohesion examples**:

- A file with HTTP plumbing, business logic, presentation formatting, and DB access in the same module.
- A hook that owns routing, persistence, validation, AND rendering state.
- A service whose name implies one responsibility but whose methods span four.
- A "utils" file that grows with unrelated helpers.
- A component that conditionally renders three completely different UIs from one file.

**Coherence examples**:

- The same concept named differently in different layers (`userId` / `user_id` / `accountId` / `ownerId` for the same thing).
- Verb inconsistency for the same operation (`fetch`, `load`, `get`, `retrieve`).
- Boolean inversions (`isNotDisabled`, `hasNoErrors`).
- Pluralization drift (`user`/`users` referring to the same collection).
- Abbreviation drift (`ctx`, `context`, `c` in the same module).
- A concept renamed in one layer but not in adjacent layers.

**How to spot**:

- For cohesion: try to write one sentence describing what each touched file owns. If you can't, the file lacks cohesion.
- For coherence: for each new domain term, grep for synonyms. For each rename, check that every layer was updated.

**Recommendation pattern**: split the file along its responsibility lines (extract hooks, separate concerns into focused modules). For coherence, choose one canonical name and update everywhere; record the choice in a comment or doc if non-obvious.

## 8. Hidden Performance Issues

Performance regressions introduced by the change. Focus on what the change adds, not pre-existing slow code.

**Concrete examples — query patterns**:

- N+1: `for (const id of ids) { await repo.findOne(id) }`.
- Loading a full table and filtering client-side instead of using a WHERE clause.
- New WHERE clauses on columns without indexes.
- Per-row updates inside a transaction that should be a bulk update.

**Concrete examples — async**:

- Serial awaits where parallel would work (no data dependency between the calls).
- Promise chains used as orchestration where `Promise.all` would suffice.
- A handler that awaits long-running work synchronously instead of queueing.

**Concrete examples — caching**:

- A write that mutates underlying data but doesn't invalidate the cache.
- A read that always bypasses cache without reason.
- Cache keys that include unstable values (timestamps, user-specific data) when they shouldn't.
- Stampede: many callers hitting the slow path simultaneously because there's no single-flight protection.

**Concrete examples — rendering**:

- Inline object/array literals in dependency arrays causing re-renders.
- Unstable callbacks in `useEffect` dependencies.
- Expensive computations inside render bodies without memoization, called per row in a list.
- Synchronous large work on the main thread.

**Concrete examples — sync/network**:

- A save action triggering a full resync instead of a delta.
- A polling loop introduced and never cleared.
- WebSocket handler that does heavy work in the message callback.

**How to spot**: for each new loop, check for awaits or queries inside. For each new query, check for an index. For each new caller of cached data, check the invalidation story. For each new React component, scan for stable identities of props/deps.

**Recommendation pattern**: state the scale at which the issue bites (per request, per user, per item in list of N) so the reader can decide urgency. Recommend the specific fix (batch query, parallel await, index addition, memoization).
