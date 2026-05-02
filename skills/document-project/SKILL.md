---
name: document-project
description: Update SettleMate Autopilot project documentation based on current codebase state
argument-hint: [data-model|workflows|api|database|jobs|tests|evaluators|all]
disable-model-invocation: true
allowed-tools: Read, Grep, Glob, Bash, Write, Edit
---

# Skill: document-project

You are a documentation specialist for the SettleMate Autopilot project. Your task is to update the project documentation by scanning the current source code, comparing it with what is already documented, and updating only what has changed.

**Project root**: The `autopilot/` directory (current working directory when this skill runs)
**Source code**: `src/`
**Documentation**: `docs/`

---

## Routing

Parse the argument passed to this skill:

- `data-model` → run the [Data Model](#route-data-model) route
- `workflows` → run the [Workflows](#route-workflows) route
- `api` → run the [API](#route-api) route
- `database` → run the [Database](#route-database) route
- `jobs` → run the [Jobs](#route-jobs) route
- `tests` → run the [Tests](#route-tests) route
- `evaluators` → run the [Evaluators](#route-evaluators) route
- `all` → run ALL routes sequentially in this order: data-model, workflows, api, database, jobs, tests, evaluators
- (no argument or unrecognized argument) → print a menu and ask the user to pick one:

```
Available documentation routes:
  data-model   → docs/data_model_overview.md
  workflows    → docs/workflow_overview.md + docs/agents/
  api          → docs/api/
  database     → docs/database/
  jobs         → docs/jobs/
  tests        → docs/tests_overview.md
  evaluators   → docs/evaluation_overview.md
  all          → run all of the above
```

---

## General Rules (apply to ALL routes)

1. **READ BEFORE WRITING**: Always read the existing documentation file before modifying it. Never overwrite a file without first reading its current contents.
2. **UPDATE, do not rewrite**: Preserve existing content, structure, formatting, and links. Only change what is actually different from the source code.
3. **Source of truth is the code**: If the code contradicts the docs, trust the code.
4. **Detect changes by comparison**: Read the relevant source files, identify what has been added, removed, or changed, then apply minimal edits to the doc.
5. **Preserve Mermaid diagrams**: If a diagram is still accurate, keep it. If it is wrong, update it. If there is none and the route instructions say to add one, add it.
6. **Preserve relative links**: All links from `docs/` files should use relative paths to source (e.g., `../../src/...`) or other doc files (e.g., `../workflow_overview.md`). Do not use absolute paths.
7. **Document version / last updated**: At the top of each doc, update the "Last Updated" date to today's date in YYYY-MM-DD format if you made changes.
8. **After every route completes**: Append an entry to `docs/CHANGELOG.md` following the format described in [Updating CHANGELOG.md](#updating-changelogmd).

---

## Route: data-model

**Target file**: `docs/data_model_overview.md`

### Step 1 — Read existing doc

Read `docs/data_model_overview.md`.

### Step 2 — Scan source for data structures

Read or grep these files for current entity definitions and schemas:

- `src/schemas/` — all files (these are the LangGraph state TypedDicts and Pydantic models used in the workflow)
- `src/database/repositories/` — skim each repository file to identify entity fields referenced in SQL or model constructors
- `src/api/schemas/` — API request/response schemas
- `src/services/tasks/` — task input/output schemas if any

Also check:
- Do all entities in the doc still exist? (Search `src/` for class names or table names)
- Are there new entities in the code that are missing from the doc?
- Have any field names, types, or JSONB shapes changed?

### Step 3 — Update the doc

Update `docs/data_model_overview.md` with any changes found:

- Entity Map: ASCII or text diagram of entity relationships
- Relationship Summary table
- JSONB Fields section: document `RefundRequest.history`, `Product.attributes`, `VoiceCallTracking.payload` shapes if they have changed
- Common SQL Queries: only update if the schema changes affect the queries

Do NOT rewrite the entire file. Apply surgical edits only.

---

## Route: workflows

**Target files**:
- `docs/workflow_overview.md`
- `docs/agents/retail_agent.md`
- `docs/agents/airtickets_agent.md`
- `docs/agents/refund_response_agent.md`

### Step 1 — Read existing docs

Read all four docs listed above.

### Step 2 — Scan source for workflow definitions

Read these files:

- `src/workflow.py` — main StateGraph: nodes registered, edges, conditional edges
- `src/agents/retail_agent.py` — retail agent sub-workflow graph
- `src/agents/refund_response_agent.py` — refund response agent logic
- `src/nodes/router.py` — routing logic, LLM model chain, fallback order
- `src/nodes/free_trial.py` — free trial check logic
- `src/nodes/llm_classifiers/` — list files and read them
- `src/nodes/retail_agent/` — list all node files and note any additions/removals
- `src/nodes/airtickets_agent/` — list all node files
- `src/nodes/price_monitoring/` — orchestrator and nodes
- `src/agents/list.py` — agent class definitions used in router tool binding

Also check:
- Has the main workflow graph changed (new nodes, removed nodes, new edges)?
- Has the router fallback model chain changed?
- Has the retail agent sub-workflow changed (new nodes, new conditional edges)?
- Is there a new agent that does not have a docs/agents/ entry?

### Step 3 — Update the docs

**`docs/workflow_overview.md`**:
- Update the Nodes Registered table if nodes were added or removed
- Update the Mermaid graph if the workflow topology changed
- Update the Quick Reference scheduled jobs table if beat schedule changed
- Update any model names in the Routing Decision section if LLMs changed

**`docs/agents/retail_agent.md`**:
- Update the Sub-Workflow Graph Mermaid diagram if the retail sub-graph changed
- Update the Key Nodes section if nodes were added, removed, or renamed
- Update the Price Monitoring Process section if orchestration logic changed

**`docs/agents/airtickets_agent.md`**:
- Apply the same approach: read the file, compare with `src/agents/airtickets_agent.py` (if it exists) or `src/nodes/airtickets_agent/`, update what changed

**`docs/agents/refund_response_agent.md`**:
- Read the file, compare with `src/agents/refund_response_agent.py`, update what changed

If a new agent exists in `src/agents/` that has no corresponding doc in `docs/agents/`, create a new doc for it following the structure of `docs/agents/retail_agent.md`:
1. Overview section with entry point
2. Sub-Workflow Graph Mermaid diagram (if it has a StateGraph)
3. Key Nodes section
4. Related Documents section

---

## Route: api

**Target files**:
- `docs/api/routes.md`
- `docs/api/autopilot_controller.md`
- `docs/backend_overview.md`

### Step 1 — Read existing docs

Read all three docs listed above.

### Step 2 — Scan source for API definitions

Read these files:

- `src/main.py` — FastAPI app setup, router registrations, lifespan
- `src/api/controllers/main.py` — main routes (GET /, GET /health)
- `src/api/controllers/autopilot.py` — all autopilot endpoints
- `src/api/schemas/main.py` — request/response Pydantic models
- Check if `src/api/retail_agent.py` and `src/api/refund_response_agent.py` exist — read them for route definitions
- Check `src/api/` for any new controller files not currently in the docs

Also check:
- Are there new endpoints not documented?
- Have any request/response schemas changed?
- Has the lifespan startup/shutdown sequence changed?
- Have any router prefixes changed?

### Step 3 — Update the docs

**`docs/api/routes.md`**:
- Update each endpoint's Request Body and Response schemas if Pydantic models changed
- Add new endpoints that are missing
- Remove endpoints that no longer exist in the source

**`docs/api/autopilot_controller.md`**:
- Update controller-level notes (auth, error handling patterns) if they changed

**`docs/backend_overview.md`**:
- Update the Router Structure section if new routers were added
- Update the Mermaid architecture diagram if it changed
- Update the Key Repositories table if new repositories were added

---

## Route: database

**Target files**:
- `docs/database/repositories.md`
- `docs/database/entities.md`

### Step 1 — Read existing docs

Read both docs listed above.

### Step 2 — Scan source for repository and entity definitions

Read these files:

- `src/database/repositories/` — list all files, then read each one
- `src/database/database_service.py` — high-level facade methods
- `src/database/connection/base_repository.py` — base class methods
- `src/database/services/` — service classes

For each repository file, extract:
- Class name
- All public methods with signatures
- What entity it operates on

Also check:
- Are there new repository files not in the docs?
- Have any method signatures changed (parameters added or removed)?
- Have any methods been added or removed from existing repositories?
- Has the `DatabaseService` facade changed (new high-level operations)?

### Step 3 — Update the docs

**`docs/database/repositories.md`**:
- Add new repository entries for any repository class not currently documented
- Update method signatures tables for existing repositories where methods changed
- Update the Database Service Facade section if `database_service.py` changed

Each new repository entry should follow this structure:
```markdown
### [ClassName]

**File**: [relative path from docs/database/]

**Entity**: [entity name]

**Key Methods**:

| Method | Signature | Purpose |
|--------|-----------|---------|
| ...    | ...       | ...     |

**Usage Example**:
```python
# example usage
```
```

**`docs/database/entities.md`**:
- Update entity descriptions and ER diagram if new tables/columns were added
- Update relationship descriptions if foreign keys changed

---

## Route: jobs

**Target files**:
- `docs/jobs/scheduled_jobs.md`

### Step 1 — Read existing doc

Read `docs/jobs/scheduled_jobs.md`.

### Step 2 — Scan source for job definitions

Read these files:

- `src/celery_app.py` — `beat_schedule` dict (job names, task paths, cron schedules, queues)
- `src/services/jobs/` — list all files, then read each one for:
  - Celery task decorator and task name
  - Function signature and return type
  - High-level logic description (docstring + first 30 lines)
- `src/nodes/price_monitoring/tasks/` — list and read daily task files

Also check:
- Are there jobs in `beat_schedule` not documented?
- Have any job schedules (cron expressions) changed?
- Have any task paths changed (renamed modules)?
- Are there new job files in `src/services/jobs/` not in the docs?

### Step 3 — Update the doc

**`docs/jobs/scheduled_jobs.md`**:
- Update the Job Schedule table: correct task paths, schedules, and descriptions
- Add new Job Details entries for jobs not currently documented
- Update existing Job Details entries if the logic changed significantly
- Remove entries for jobs that no longer exist

Each new job detail entry should include:
```markdown
### N. [Job Title]

**File**: [relative path from docs/jobs/]

[1-2 sentence description of what the job does]

```python
# key task decorator and signature
```

**Steps**:
1. ...
2. ...

**Configuration**:
- `ENV_VAR_NAME`: description
```

---

## Route: tests

**Target file**: `docs/tests_overview.md` (create if it does not exist)

### Step 1 — Read existing doc (if it exists)

Try to read `docs/tests_overview.md`. If it does not exist, you will create it from scratch (this is the only route where creating a new file is allowed by default).

### Step 2 — Scan the test suite

Run and read:

```bash
ls tests/
ls tests/e2e/
ls tests/workflows/
ls tests/services/
```

For each test file, read its contents to identify:
- What component or feature is being tested
- Test markers used (e.g., `@pytest.mark.unit`, `@pytest.mark.slow`)
- Whether it is a unit test, integration test, or e2e test
- Notable test fixtures or mocks used

Also read `tests/conftest.py` to understand shared fixtures.

### Step 3 — Write or update the doc

`docs/tests_overview.md` should contain:

1. **Overview**: Brief summary of the test suite structure
2. **Test Structure** table:
   | File | Type | What it tests |
   |------|------|---------------|
   | ...  | unit | ... |

3. **Running Tests**: How to run specific subsets
   ```bash
   uv run pytest                        # all
   uv run pytest -m "not slow"          # skip slow
   uv run pytest tests/e2e/             # e2e only
   ```

4. **Fixtures & Mocking**: Key fixtures from conftest.py, what they mock, why
5. **Coverage gaps**: Based on what is in `src/` vs. what is tested, list components that appear to have no dedicated test file

Update date at top if you made changes.

---

## Route: evaluators

**Target file**: `docs/evaluation_overview.md` (create if it does not exist)

### Step 1 — Read existing doc (if it exists)

Try to read `docs/evaluation_overview.md`. If it does not exist, you will create it from scratch.

### Step 2 — Scan the evaluation framework

Read these files:

- `src/evaluation/evaluators/` — list and read each evaluator file for: class name, dataset used, `predict()` logic
- `src/evaluation/scorers/` — list and read scorer files for metric definitions
- `src/evaluation/utils/` — list files, skim for Langfuse/HuggingFace upload utilities
- `src/evaluation/evaluators/base_evaluator.py` — base class interface

### Step 3 — Write or update the doc

`docs/evaluation_overview.md` should contain:

1. **Overview**: What the evaluation framework does and why
2. **Architecture**:
   - How `BaseEvaluator` works (dataset loading, predict loop, scoring)
   - How Langfuse dataset integration works
3. **Evaluators Table**:
   | Evaluator Class | Component Tested | Dataset Name | Key Metric |
   |-----------------|-----------------|--------------|------------|
   | ...             | ...             | ...          | ...        |

4. **Scorers Reference**: List all scorer classes with description
5. **Running an Evaluation**:
   ```bash
   uv run python -m src.evaluation.evaluators.email_classifier_evaluator
   ```
6. **Adding a New Evaluator**: Step-by-step guide (based on BaseEvaluator pattern)

---

## Updating CHANGELOG.md

After completing any route (or all routes if running `all`), append a new entry to `docs/CHANGELOG.md`.

### Step 1 — Read the existing CHANGELOG

Read `docs/CHANGELOG.md` to understand the current format.

### Step 2 — Append the new entry

The entry format is:

```markdown
---

## [YYYY-MM-DD] - Automated Documentation Update

### Changed

- **[Document Title]** (`docs/path/to/file.md`)
  - [specific change 1]
  - [specific change 2]

### Added (if new files were created)

- **[Document Title]** (`docs/path/to/file.md`)
  - [what was added]
```

Rules:
- Use today's date (format: YYYY-MM-DD)
- Only include files you actually changed
- Be specific about what changed (e.g., "Added `ScheduledEmailRepository` entry", "Updated router fallback chain from X to Y")
- If nothing changed in a file (docs were already up to date), do not include it in the changelog entry
- If absolutely nothing changed across all files, add a single line: "No changes — documentation was already up to date."

Append the new entry at the top of the changelog entries (after the title and intro paragraph, before the first existing `---` separator and entry), so newest entries appear first.

---

## Tone and Style Guidelines

Follow the style of the existing documentation:

- Use Mermaid `graph TD` or `graph TB` for flow diagrams
- Tables use GitHub-flavored Markdown format
- Code blocks specify the language (` ```python `, ` ```sql `, ` ```json `, ` ```bash `)
- Section headers follow a consistent hierarchy (H2 for major sections, H3 for subsections)
- Relative links use the pattern: `[Display Text](relative/path/file.md)`
- Source code links: `[filename.py](../../src/path/to/filename.py)`
- Every doc has a "Related Documents" section at the bottom
- Agent docs have a "Back to: [Workflow Overview]" link at the top

Do NOT:
- Add emojis to documentation files
- Add promotional or subjective language ("powerful", "elegant", "robust")
- Invent details not supported by the source code
- Speculate about future plans or roadmap items
- Include temporary implementation notes or TODOs from code comments as documented behavior
