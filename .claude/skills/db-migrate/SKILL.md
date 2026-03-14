Create a new Alembic database migration: $ARGUMENTS

Steps:
1. Check existing migrations in migrations/versions/ to understand current schema
2. Generate the migration file:
   uv run alembic revision --autogenerate -m "$ARGUMENTS"

3. Review the generated file in migrations/versions/
   - Verify upgrade() contains the correct CREATE TABLE / ALTER TABLE statements
   - Verify downgrade() reverses the upgrade correctly
   - Add missing indexes if any
   - Ensure the schema prefix is set correctly (search_path)

4. Apply the migration to local DB:
   uv run alembic upgrade head

5. Verify the migration applied:
   uv run alembic current

Report:
- Migration file created (path)
- Tables/columns added or modified
- Indexes created
- Any manual corrections made to the auto-generated file
