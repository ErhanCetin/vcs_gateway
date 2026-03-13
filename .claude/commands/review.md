Review the following code as a strict senior backend engineer: $ARGUMENTS

If no argument is given, review the most recently modified files in src/.

Review checklist:
1. **Correctness** — does the logic match the intended behavior from DATA_FLOW_V2.md?
2. **Async safety** — any blocking calls in async context? Missing await?
3. **Transaction boundaries** — is the outbox INSERT inside the same transaction as the business INSERT?
4. **Stale detection** — is Redis pre-check and post-check applied correctly?
5. **Error handling** — are domain exceptions used? Any bare except? Silent failures?
6. **Type safety** — missing type hints? mypy would fail?
7. **SQL injection** — any string interpolation in queries?
8. **Logging** — are key steps logged with structlog? Is correlation_id bound?
9. **Test coverage** — is there a corresponding test?
10. **Architecture violations** — business logic in repository? DB access in service?

Return format:
1. **Findings** (file:line — description — severity: high/medium/low)
2. **Risks** (concurrency, data loss, security)
3. **Suggested fixes** (concrete, not generic)
