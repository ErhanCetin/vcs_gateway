# Post-Implementation Self-Review

Review the code changes you have just made as a **strict senior backend engineer performing an adversarial self-review**.

Your task is **not** to justify the implementation.  
Your task is to find flaws, risks, regressions, missing tests, and architecture violations in your own changes.

Be skeptical. Assume your implementation may be wrong.

## Scope

If arguments are provided, review only:
$ARGUMENTS

If no arguments are provided:

1. Run `git diff HEAD`
2. Run `git status`
3. Identify all changed, staged, and untracked files
4. Review only files under `src/`
5. Focus primarily on the lines changed in the current implementation, but inspect surrounding code when needed to detect regressions

Ignore:
- `docs/`
- `scripts/`
- `migrations/`

## Review Mode

Perform the review as if:
- the code was written by another engineer
- you are responsible for preventing production incidents
- false negatives are worse than being overly critical
- agreement with the implementation is irrelevant

Do **not** praise the code.  
Do **not** summarize what was implemented.  
Do **not** explain intent unless necessary to describe a defect.

## Required Review Checks

### 1. Correctness
- Does the changed logic actually satisfy the intended behavior?
- Does it violate expectations defined in `DATA_FLOW_V2.md`?
- Are there broken edge cases or incorrect state transitions?

### 2. Regression Risk
- Could these changes break existing behavior?
- Did any previously safe path become unsafe?
- Are there hidden side effects on adjacent flows?

### 3. Async Safety
- Any blocking call inside async code?
- Any missing `await`?
- Any misuse of async DB/Redis/HTTP clients?
- Any event loop blocking risk?

### 4. Transaction Boundaries
- Is the business write atomic?
- Is the outbox insert in the same transaction as the business insert?
- Any chance of partial commit or inconsistent state?

### 5. Stale Data / Concurrency
- Is Redis stale pre-check applied correctly?
- Is post-check applied correctly before commit/finalization?
- Any race condition window?
- Any lost update / duplicate processing risk?

### 6. Error Handling
- Any swallowed exception?
- Any `bare except`?
- Any infrastructure exception leaking into domain layer?
- Are domain errors explicit and actionable?

### 7. Type Safety
- Missing or incorrect type hints?
- Wrong nullable assumptions?
- Any usage that would likely fail mypy?
- Unsafe `Any` or implicit casting?

### 8. Data / SQL Safety
- Any SQL string interpolation?
- Unsafe dynamic query building?
- Any risk of malformed persistence logic?

### 9. Logging / Observability
- Are important state transitions logged?
- Is `correlation_id` bound?
- Are failures logged with enough context for debugging?
- Any sensitive data accidentally logged?

### 10. Architecture Compliance
- Is business logic placed inside repository layer?
- Is DB access leaking into service layer?
- Any violation of layering, boundaries, or project conventions?

### 11. Test Adequacy
- Is there a matching test for the changed behavior?
- Are failure paths tested?
- Are edge cases and regression scenarios covered?
- If no test exists, explicitly call that out

## Severity Rules

Use:
- **high** → production bug, data corruption, security issue, concurrency issue, transaction integrity risk
- **medium** → reliability, architecture, regression, or observability problem
- **low** → maintainability or clarity issue with real engineering impact

## Output Format

### 1. Findings
Format each item as:

`file_path:line_number — issue — severity: high|medium|low`

Only include real findings.

### 2. Risks
List systemic risks introduced by the change, such as:
- regression risk
- partial write risk
- duplicate processing
- stale data acceptance
- security exposure
- insufficient observability

### 3. Missing Tests
List concrete tests that should exist but do not.

Format:

`test_file_or_area — missing scenario — priority: high|medium|low`

### 4. Suggested Fixes
Provide concrete fixes only.
For each fix, explain:
- what to change
- where to change it
- why it resolves the issue

### 5. Final Verdict
Choose exactly one:
- **approve**
- **approve with fixes**
- **needs rework**

If the verdict is not `approve`, explain in 1–3 sentences why.