Run the test suite and analyze results: $ARGUMENTS

Steps:
1. Run all tests:
   uv run pytest --cov --cov-report=term-missing -v

2. If tests fail:
   - Read the failing test file and the source file it tests
   - Identify root cause (not just the symptom)
   - Fix the issue
   - Re-run only the failing tests to confirm fix:
     uv run pytest <failing_test_path> -v

3. If coverage is below 80%:
   - Identify which lines/branches are uncovered
   - Add missing tests
   - Re-run coverage

4. Report:
   - Total tests: passed / failed / skipped
   - Coverage percentage
   - Any fixes made (file:line references)
   - Any tests that were added
