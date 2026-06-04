# Mutation Sanity-Check Protocol

The point of coverage and assertions is to **catch a bug if one is introduced**. Mutation testing proves that directly: deliberately break the source, and confirm a test goes red. A mutation that leaves the suite green ("survives") is hard evidence that the tests are tautological for that line — no amount of coverage percentage can refute it.

This is a **manual, judgment-driven** sanity-check (not an automated tool run). It mutates real source files, so it carries real risk of leaving the tree dirty. The restore protocol below is **non-negotiable** and is mechanized by `scripts/restore_guard.sh`.

---

## What to mutate (3–5 targets, by judgment)

Do **not** mutate broadly. Pick the **3–5 riskiest lines** in the in-scope source. No rigid cap — use judgment, but stay small and meaningful. Prioritize:

- **Money / quantity / state transitions** — anything where a wrong value has real consequence.
- **Security / authorization** — checks that gate access.
- **High branching** — the line with the most decisions feeding output.
- **Recently changed** — the diff under review is where regressions hide.
- **Boundaries** — comparisons, off-by-one candidates, loop bounds.

### Semantically meaningful mutations (pick per target)

Each mutation must be a *plausible* bug a human could write — not random noise:

- **Drop a forwarded argument** — call `f(a)` instead of `f(a, b)`; tests that pin call-args go red.
- **Return a constant** — replace a computed return with `return None` / `return 0` / `return True`.
- **Flip a boundary** — `<` ↔ `<=`, `>` ↔ `>=`, `==` ↔ `!=`.
- **Skip a side effect** — comment out a cleanup, a commit, an enqueue, or a state mutation.
- **Invert a condition** — `if eligible:` ↔ `if not eligible:`.

---

## The per-mutation loop (strictly one mutation at a time)

Never have two mutations live at once. For **each** target:

### 1. Pre-flight — prove the file is clean
```
scripts/restore_guard.sh <path/to/source_file>
```
If it exits non-zero, the file has uncommitted changes. **Stop.** Never mutate uncommitted source — a botched restore could destroy the user's work. Resolve the working tree first (commit or stash the user's real changes), then re-run the guard.

### 2. Record the pristine reference
Have a guaranteed way back to the original:
- The file is git-clean (verified in step 1), so `git checkout -- <path>` restores it; **and/or**
- Save the blob: `git stash` is too broad — prefer copying the file to a temp path **outside the working tree** (e.g. `"$TMPDIR/<name>.orig"`) as a belt-and-suspenders backup.

### 3. Apply exactly ONE mutation
Use the Edit tool to make a single, semantically meaningful change from the list above. One file, one edit.

### 4. Run the narrowest relevant test(s) — expect RED
Run only the test(s) that should pin this behavior (a single test file or `-k` selection), not the whole suite — fast feedback.
- **RED (test fails):** good. The tests catch this bug. Record "killed".
- **GREEN (test passes):** the mutant **survived** → **tautology finding.** The tests do not actually verify this line. Record it (severity by risk: money/state/security → P0, otherwise P1).

### 5. Restore — then PROVE pristine
```
git checkout -- <path/to/source_file>     # or copy back from the temp backup
scripts/restore_guard.sh <path/to/source_file>
```
If the guard exits non-zero, the restore did not fully succeed. **HARD-STOP.** Do not proceed to the next mutation and do not end the skill. Restore manually (copy-back from the temp backup) and re-run the guard until it passes.

### 6. Repeat
Only after the guard confirms clean, move to the next target. Reuse the temp backup slot or make a fresh one.

---

## Final gate (must pass before reporting)

1. Whole-tree check: `git status --porcelain` is empty (no stray mutation residue anywhere).
2. Full in-scope suite is green again.

**The skill must never end with a dirty tree.** If either gate fails, stop and surface it — clean residue is a correctness failure of this skill, not a minor note.

---

## Reporting mutation results

For each mutation, report: file:line, the mutation applied, the test(s) run, and **killed** vs **survived**. Surviving mutants are the headline findings — they are proof, not opinion, that the tests are tautological for that behavior. Route the fix to `pytest-test-specialist` (strengthen the assertion to pin the real value/call-args) and re-run this protocol on that line to confirm the mutant is now killed.

---

## Optional escalation (off by default)

After manual sanity-checks have already found something on a file, the user may want deeper assurance on that one file:

- **`mutmut` full sweep on ONE hot file** — let the tool generate the full mutation set for a single high-risk module and report the survival rate. Off by default; offer it, don't run it automatically. It is slow and must run against a clean tree with the same restore discipline (the tool manages its own apply/restore, but verify the tree is clean before and after).

Do **not** run a repo-wide mutmut sweep — it is slow, noisy, and not the goal. The goal is targeted proof on the riskiest lines.
