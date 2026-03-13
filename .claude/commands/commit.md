Create a git commit for the current changes.

Steps:
1. Run: git status
2. Run: git diff (staged + unstaged)
3. Run: git log --oneline -5 (to follow existing commit style)

4. Analyze the changes:
   - What type: feat / fix / refactor / test / docs / chore
   - What scope: the module or component changed
   - What was the intent (why, not what)

5. Stage relevant files (NOT .env.local, NOT secrets):
   git add <specific files>

6. Create commit with conventional format:
   git commit -m "type(scope): concise description

   Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"

Conventional commit types:
- feat: new feature
- fix: bug fix
- refactor: code change without behavior change
- test: adding or fixing tests
- docs: documentation only
- chore: build, deps, config changes

Do NOT commit:
- .env.local
- Any file containing secrets, API keys, or passwords
- __pycache__ or .mypy_cache directories
