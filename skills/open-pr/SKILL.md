---
name: open-pr
description: Open a GitHub Pull Request (draft or real) from the current branch. Analyzes commits vs main, auto-detects Linear issue context from branch name or conversation, writes a PR title and body, pushes the branch if needed, and creates the PR with default reviewers. Never modifies any files or creates commits.
argument-hint: "[draft] [LINEAR-ID] [scope description...]"
---

# open-pr

Open a GitHub Pull Request from the current branch.

## Usage

```
/open-pr [draft] [LINEAR-ID] [scope description...]
```

**All arguments are optional.** If no arguments are provided, the skill auto-detects context from the branch name, conversation history, and git history.

**Argument parsing rules (in order from the start of `$ARGUMENTS`):**

1. If the first token is the literal word `draft` (case-insensitive) → mark PR as draft, consume the token.
2. If the next token matches a Linear issue ID pattern (e.g. `SET-789`, `ENG-42`) → record it as the Linear issue ID, consume the token.
3. Everything remaining is the **scope description** (optional, multi-word).

**Examples:**

```
/open-pr
/open-pr draft
/open-pr SET-789 Add email classification node for inbound responses
/open-pr draft SET-789 Add email classification node for inbound responses
/open-pr draft Fix null pointer in retail agent price monitoring
/open-pr Add email classification node for inbound responses
```

---

## CRITICAL CONSTRAINT — READ-ONLY OPERATIONS ONLY

This skill must **NEVER**:
- Modify, edit, write, or create any files (code, tests, configs, documentation, anything)
- Run linting, formatting, or test commands (no ruff, no pytest, no pre-commit)
- Create any git commits
- Use `git add`, `git commit`, `git reset`, `git checkout`, or any state-changing git command (except `git push`)

The ONLY mutation allowed is `git push -u origin HEAD` to push the existing branch.

All other operations must be **read-only**: `git log`, `git diff`, `git status`, `git rev-parse`. The only external mutation is `gh pr create`, which is the skill's purpose.

---

## Execution Steps

### Step 1 — Validate inputs and detect context

Parse `$ARGUMENTS` using the rules above.

- Detect the current branch with `git rev-parse --abbrev-ref HEAD`.
- If the current branch is `main` or `master`, stop and tell the user:
  `Cannot open a PR from the main/master branch. Please switch to a feature branch.`

**Auto-detect Linear issue ID (if not explicitly provided):**

1. Check if the branch name itself matches a Linear issue ID pattern (e.g. branch `SET-821` or `SET-821-some-description` → issue ID `SET-821`). Extract the ID using the regex pattern `^([A-Z]+-\d+)`.
2. If still no Linear ID found, look back through the **current conversation context** for any Linear issue that was discussed or worked on during this session.

**Auto-detect scope description (if not explicitly provided):**

The scope description will be synthesized in Step 3 from commit messages, diffs, and Linear issue context. No need to abort.

### Step 2 — Analyze commits on this branch

Run the following and capture the output:

```bash
git log main..HEAD --oneline
```

- If the output is empty (no commits ahead of main), stop and tell the user:
  `No commits found on this branch compared to main. Nothing to open a PR for.`
- Also capture the full commit messages and diff for body generation:

```bash
git log main..HEAD --pretty=format:"- %s%n%b"
git diff main...HEAD --stat
```

### Step 3 — Fetch Linear issue context (if ID available)

If a Linear issue ID was parsed or auto-detected:

- Call `mcp__linear-server__get_issue` with the issue identifier.
- Extract: issue title, description, and status.
- If the call fails (issue not found or MCP error), log a warning and continue without Linear context. Do NOT abort.

### Step 4 — Generate PR title and body

Synthesize the PR title and body from all available context:
- User's scope description (if provided — use as primary signal)
- Git commit messages and diff stats from Step 2
- Linear issue title and description (if available) from Step 3
- Conversation context (what was discussed/worked on in this session)

**PR Title rules:**
- Must be under 70 characters.
- Format: `[LINEAR-ID] <concise title>` if a Linear issue ID is available, otherwise just the concise title.
- Do NOT include generic filler like "feat:", "fix:" prefixes unless the commits already use that convention.

**PR Body format (clean markdown, no Claude collaborator signature):**

```markdown
## Summary

- <bullet 1 — key change>
- <bullet 2 — key change>
- <bullet 3 — key change if applicable>

## What was done

<1-2 paragraph prose description synthesized from commit messages and Linear issue context.
Focus on the "why" and "what", not implementation minutiae.>

## Linear issue

[LINEAR-ID: Issue Title](https://linear.app/settlemate/issue/LINEAR-ID)

(Omit this section entirely if no Linear issue is available.)

## Test plan

- [ ] <test step 1>
- [ ] <test step 2>
- [ ] <test step 3 if applicable>
```

The test plan should be inferred from the nature of the changes (e.g. if it touches an API endpoint: "Test the endpoint with valid and invalid payloads"; if it touches a workflow node: "Run the full workflow with a sample email").

### Step 5 — Check for existing PR

Check if a PR already exists for the current branch:

```bash
gh pr view HEAD --json url 2>/dev/null
```

- If a PR already exists, stop and tell the user:
  `A PR already exists for this branch: <PR URL>. Use \`gh pr edit\` to update it.`

### Step 6 — Push branch to origin if needed

Check whether the branch has a remote tracking branch:

```bash
git status -sb
```

If there is no upstream tracking branch (output contains `## <branch>` with no `...origin/`), push it:

```bash
git push -u origin HEAD
```

If the push fails, stop and report the error to the user.

### Step 7 — Create the Pull Request

Construct the `gh pr create` command:

```bash
gh pr create \
  --title "<generated title>" \
  --body "<generated body>" \
  --reviewer EFMelo,joovitor12,hugocarvalhopc \
  [--draft]   # include only if draft mode was requested
```

Use a heredoc or temp file for the body to handle multi-line content safely.

**Reviewers (hardcoded defaults — update here if team changes):**
- `EFMelo` — Edvaldo Melo
- `joovitor12` — João Vitor Machado
- `hugocarvalhopc` — Hugo Carvalho

### Step 8 — Report result

Print the PR URL returned by `gh pr create`.

If `--draft`, also print a reminder: `Draft PR created. Remember to mark it as Ready for Review when complete.`

---

## Error Handling Reference

| Situation | Action |
|-----------|--------|
| On `main`/`master` branch | Abort with clear message |
| No commits vs `main` | Abort with clear message |
| PR already exists for this branch | Abort and show existing PR URL |
| Linear issue not found | Warn and continue without Linear context |
| Push to origin fails | Abort and report the git error |
| `gh pr create` fails | Report the full error output |
