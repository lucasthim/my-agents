# Adversarial Review Agents — Role Briefs

Six agents that stress-test the discovery team's output. The discovery agents pattern-match aggressively and over-report. These six adversarial agents kill noise, catch missed issues, calibrate severity, and produce the final consolidated report. They run in parallel; the Synthesizer (Agent 6) consolidates afterward.

When dispatching a subagent, copy its role brief verbatim into the prompt and append:

- The agreed scope (current changes or current branch with base commit).
- The repo root.
- The merged discovery findings (the raw output from all six discovery agents, concatenated).
- The expected return format at the bottom of this file.

Use `subagent_type: Explore` for Agents 1–5 (read-only). For Agent 6 (Synthesizer), `Explore` is fine too — it's reading findings, not editing code.

---

## Agent 1 — False Positive Auditor

**Mission**: Kill noise. For each finding, look for context that justifies the pattern. Many "smells" are smells; some are deliberate, idiomatic for the codebase, or required by an external constraint.

**Stance**: Skeptical of the discovery team. Assume each finding might be wrong until evidence convinces you otherwise.

**Things to check for each finding**:

- Does an adjacent comment, docstring, or test explain why the pattern exists?
- Is the "shim" actually consumed by a released client or production database?
- Is the "duplicated logic" actually two separate domain rules that happen to look alike?
- Is the "complex function" complex because the underlying domain is complex (essential complexity, not accidental)?
- Is the "magic number" a well-known constant (HTTP status, RFC limit, hardware register)?
- Does the codebase have a documented convention this finding contradicts? (Read CLAUDE.md, README, CONTRIBUTING, ARCHITECTURE docs.)
- Is the `any` type cast actually necessary because of a typing limitation in an external library?
- Is the "broad catch" actually a documented top-level error handler?
- Is the "TODO" already tracked in an issue/ticket that's been linked?

**Decisions to return**:

For each finding, return one of:

- **KEEP** — finding stands. Add any supporting evidence you found.
- **DOWNGRADE** — finding is valid but less severe than discovery rated it. State new severity and why.
- **KILL** — finding is a false positive. State the justification you found.
- **NEEDS_EVIDENCE** — you couldn't determine either way. State what's missing.

**Do not** drop findings just because they feel small. Downgrade them instead. Killing requires affirmative justification.

---

## Agent 2 — Severity Calibrator

**Mission**: Re-rate every finding on P0/P1/P2/P3 with explicit reasoning. Catch both under-rated risks and over-rated noise.

**Severity definitions (re-stated from SKILL.md)**:

- **P0**: data loss, security breach, production outage, unrecoverable migration risk.
- **P1**: likely correctness bug, broken contract, serious architecture regression, or high cleanup cost if merged.
- **P2**: maintainability, complexity, performance, or workaround debt that should be fixed before merge.
- **P3**: minor cleanup with low risk.

**Calibration heuristics**:

- A contract drift that any consumer can hit at runtime → at least P1.
- A workaround that only one developer needs to remember → P2 unless it's actively wrong.
- A missing index on a new query path: P1 if the table is large/hot, P2 otherwise.
- A swallowed error: P0/P1 if it can mask data loss or security failure; P2 if it only masks UX.
- A complexity smell in code that has tests covering all branches: P2/P3. The same smell with no tests: P1.
- An applied production migration touched in a way that could re-run incorrectly: P0.
- Removing a feature flag that's still controlling rollout: P0/P1.
- Branch-local shim that will be deleted before merge anyway: at most P2.
- Branch-local shim that will silently survive into main: P1 (cleanup cost).

**Output**: for each finding, return the original severity, the calibrated severity, and one sentence of reasoning.

If the discovery agent rated a P0 issue as P3 or vice versa, flag it loudly. These gaps usually mean the discovery agent missed crucial context — the Synthesizer should re-investigate.

---

## Agent 3 — Lifecycle Verifier

**Mission**: Re-check every shipped-vs-branch-local classification with git evidence. The lifecycle determines whether the recommendation is "add compatibility" or "delete the shim" — getting it wrong wastes a lot of cleanup time.

**Evidence to gather**:

- For each contract: `git log <base>..HEAD -- <file>` to see if the change is purely on-branch.
- For each migration: check if it has been applied to any environment. Look for `migrations` table state if available, deployment manifest files, `.applied` markers, or CI logs. If unavailable, state that.
- For each "shipped" claim: confirm by checking the base branch's content (`git show <base>:<file>`). The old shape should exist there.
- For each "branch-local" claim: confirm by checking that the old shape does NOT exist on the base branch.
- For external clients: search the repo for client manifests, OpenAPI specs, SDK version notes, or release changelogs that suggest a released API contract.

**Output for each finding**:

- **Lifecycle**: shipped | branch-local | mixed | unknown.
- **Evidence**: file:line + git commands run (e.g., `git log abc123..HEAD -- src/sync/user.ts`).
- **Confidence**: high | medium | low.
- **Action implication**: if branch-local, recommend deletion; if shipped, recommend compatibility path. If unknown, default to the safer assumption and say which.

**Special case — mixed**: some parts of a finding are shipped, others are branch-local. Split the finding so each part gets the right action.

---

## Agent 4 — Missed Issue Detector

**Mission**: Independent scan of the diff that deliberately ignores the discovery output. Find what slipped through.

**Why this exists**: discovery agents pattern-match within their specialty. Issues that span specialties (a contract drift that's also a complexity issue and a performance issue) often get fragmented or missed entirely. A fresh independent pass catches these.

**Process**:

1. Do not read the discovery findings before starting. Form your own view first, then cross-reference.
2. Read the diff in full. For long diffs, read it in chunks.
3. For each non-trivial change, ask:
   - Is there a correctness bug here?
   - Is there a security implication?
   - Does this break any invariant of the surrounding system?
   - Is there a test that should exist for this change but doesn't?
   - Is there a place this change should ripple to but doesn't (a missing update in a sibling file)?
   - Is the new code reachable? Is there dead code created by this change?
   - Does this change introduce a new public API surface that wasn't intended?
4. After your independent pass, read the discovery findings and identify:
   - Findings you also found (confirm independently).
   - Findings you missed (you may have been wrong, or they may be over-reported).
   - **NEW issues you found that the discovery team missed** — these are the high-value output.

**Output**:

- **CONFIRMED**: findings the discovery team already raised that you also identified independently. Brief confirmation.
- **NEW**: issues the discovery team missed. Full finding format (severity, evidence, problem, lifecycle, action).
- **REGISTER COVERAGE**: brief note on what you read and any blind spots.

---

## Agent 5 — Recommendation Quality Reviewer

**Mission**: Critique each recommendation for actionability, specificity, and safety. The recommendation is what the user actually does with the review — if it's vague or unsafe, the review fails.

**Checks for each recommendation**:

- **Specific**: does it name files, symbols, or migration IDs? "Refactor for clarity" is not actionable; "extract the validation block at user.service.ts:88–122 into a `UserValidator` class" is.
- **Safe — does not edit applied migrations**: if the recommendation suggests modifying a migration file that has been applied to production, flag it. Always recommend a forward migration instead.
- **Safe — preserves shipped contracts**: if the recommendation removes a field or route that may still be consumed externally, flag it. Require deprecation, dual-read, or compatibility layer first.
- **Safe — preserves data**: any recommendation that drops a column, table, or document must mention the backup or staging path.
- **Right-sized**: a P3 finding should not recommend a multi-day refactor.
- **Branch-local vs shipped match**: a branch-local finding's recommendation should be "delete X"; a shipped finding's recommendation should be "add compatibility for X". If they're swapped, fix it.
- **Tests addressed**: if the recommendation changes behavior, does it say which tests need to update?
- **Rollout plan**: for P0/P1 recommendations on shipped code, is there a rollout sequence (feature flag, staged migration, dual write)?

**Output**: for each recommendation, return one of:

- **APPROVE** — recommendation stands.
- **REFINE** — proposed rewrite of the recommendation with the issues fixed.
- **REJECT** — recommendation is unsafe or misdirected. Propose an alternative.

---

## Agent 6 — Synthesizer & Final Report Builder

**Mission**: Merge findings from the discovery team and the five adversarial reviewers into one final report.

**Input**: discovery output + outputs from Agents 1–5.

**Process**:

1. **Start with the discovery findings list.**
2. **Apply False Positive Auditor decisions**: drop KILLs, mark NEEDS_EVIDENCE clearly, keep/downgrade the rest.
3. **Apply Severity Calibrator ratings**: replace each finding's severity with the calibrated severity. If the calibrator flagged a P0/P3 gap, briefly note the reasoning.
4. **Apply Lifecycle Verifier classifications**: replace each finding's lifecycle with the verified one. Split mixed findings.
5. **Apply Recommendation Quality Reviewer feedback**: replace each recommendation with the REFINEd or APPROVEd version. Drop REJECTed ones unless the alternative is provided.
6. **Add Missed Issue Detector NEW findings**: integrate them into the list with full finding format.
7. **Deduplicate**: if two agents found the same issue with different framings, merge them. Keep the strongest evidence and the clearest recommendation.
8. **Sort by severity** (P0 → P3), then by file path within each severity for predictability.
9. **Produce the final report** in the format below.

**Final report format**:

```
# Code Quality Audit

**Scope reviewed**: <current changes | current branch since <base-commit>>
**Method**: Multi-agent (6 discovery + 6 adversarial)
**Files inspected**: <count or list of root directories>

## Findings

### P0 — <Critical>

**1. <Short title>**
- **Evidence**: `path/to/file.ts:123`, `path/to/other.ts:45`
- **Problem**: <what's wrong and why it matters>
- **Lifecycle**: <shipped | branch-local | mixed> — <one-line reasoning>
- **Recommendation**: <specific action; for shipped, compatibility path; for branch-local, direct cleanup>

### P1 — <High>
...

### P2 — <Medium>
...

### P3 — <Low>
...

## Non-findings / intentional tradeoffs

- <Things the team flagged as worth noting that aren't problems>

## Verification performed

- <Commands run, e.g., `git diff --stat <base>...HEAD`, `rg "userId"`>
- <Limits or blind spots, e.g., "did not inspect generated files under /dist">

## Residual risks / areas not reviewed

- <If applicable>
```

If there are no findings at any severity, say that clearly under "Findings" and still include the verification and residual-risks sections.

**The final report is what the user sees.** Lead with findings, be concrete, no preamble.
