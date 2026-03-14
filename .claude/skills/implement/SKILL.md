Implement the following feature for this service: $ARGUMENTS

Before writing any code:
1. Read CLAUDE.md to understand service context and rules
2. Read the relevant section in DATA_FLOW_V2.md for this service's step
3. Read the service documentation file if it exists
4. Check existing patterns in src/ to follow established conventions

Implementation steps:
1. Identify which files need to be created or modified
2. Follow the architecture rules in CLAUDE.md strictly:
   - Async everywhere
   - Repository pattern (no DB access in services/)
   - Outbox pattern for critical queue publishing
   - Pydantic v2 for all models
   - Full type hints
   - structlog for logging
3. Write the implementation
4. Write unit tests in tests/unit/
5. If DB access is involved, write integration tests in tests/integration/
6. Run: uv run ruff check src/ tests/ --fix
7. Run: uv run mypy src/
8. Run: uv run pytest tests/unit -m unit

Report:
- Files created/modified
- Test results
- Any decisions made that deviate from the template (with justification)
