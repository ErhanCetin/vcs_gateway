# Architecture Guard

Review the latest implementation changes as a **strict principal-level backend architect**.

If no argument is given, use `git diff HEAD` and `git status` to identify all changed/staged/untracked files, then review only those files (limit to files under src/).

Your task is to detect **architectural drift, boundary violations, dependency misuse, hidden coupling, and long-term maintainability risks** introduced by the recent changes.

Do not focus on style.  
Do not focus on generic clean code advice.  
Focus on **structural engineering risks**.

Be skeptical and explicit.

---

## Scope

Argument:
$ARGUMENTS

If no argument is provided:

1. Run `git diff HEAD`
2. Run `git status`
3. Identify all changed, staged, and untracked files
4. Review only files under `src/`
5. Focus on changed files, but inspect neighboring modules when necessary to evaluate architectural impact

Ignore:

- `tests/`
- `docs/`
- `scripts/`
- `migrations/`

---

## Review Objective

Determine whether the changes preserve or degrade the intended architecture.

Assume that even if the code works today, it may still introduce:

- layering violations
- tight coupling
- unclear ownership
- transaction inconsistency
- observability gaps
- extension difficulty
- maintenance burden
- future regression hotspots

---

## Architectural Review Checklist

### 1. Layer Boundaries

Verify that responsibilities remain correctly separated.

Repository layer should contain:
- persistence logic
- query execution
- data mapping

Repository layer must NOT contain:
- business decisions
- workflow orchestration
- validation rules unrelated to persistence
- cross-service coordination

Service layer should contain:
- business rules
- workflow orchestration
- transactional use-case logic

Service layer must NOT contain:
- raw SQL
- direct database connection handling
- low-level persistence concerns

Handler / API layer should contain:
- request parsing
- response formatting
- transport concerns

Handler / API layer must NOT contain:
- business logic
- persistence logic
- transaction handling

Detect any leakage across these boundaries.

---

### 2. Dependency Direction

Verify that dependency direction remains clean.

Expected direction:

API/Handler → Service → Repository → Infrastructure

Flag issues where:

- repositories depend on services
- services depend on handlers/controllers
- domain logic depends on infrastructure details unnecessarily
- higher layers are imported by lower layers

Detect circular dependency risk, even if indirect.

---

### 3. Business Logic Placement

Check whether domain/business rules are implemented in the correct place.

Flag:
- business decisions hidden in repository methods
- validation duplicated across layers
- workflow split across unrelated modules
- domain invariants enforced inconsistently

Determine whether the current implementation makes the business flow harder to understand or reason about.

---

### 4. Transaction Ownership

Verify which layer owns transaction boundaries.

Check:
- whether transactions are opened in the correct layer
- whether transaction scope matches the business use case
- whether outbox and business persistence remain atomic
- whether infrastructure concerns leak transaction details upward

Flag cases where transaction ownership is ambiguous or scattered.

---

### 5. Concurrency and State Consistency Design

Review the design, not just the code.

Check:
- stale-data validation placement
- race-condition windows
- idempotency assumptions
- duplicate processing risk
- whether Redis pre/post checks are implemented in the correct layer

Flag cases where concurrency protection exists but is placed in the wrong abstraction layer.

---

### 6. Error Boundary Design

Verify that exception boundaries align with architecture.

Check:
- infrastructure exceptions translated before crossing domain/service boundaries
- domain errors expressed explicitly
- transport layer not leaking low-level exceptions directly
- no mixed error semantics across layers

Flag cases where error handling creates coupling or unclear ownership.

---

### 7. Observability Architecture

Check whether observability is structurally correct.

Verify:
- correlation_id propagation across layers
- key domain transitions logged at the service layer
- infrastructure errors logged with enough context
- no sensitive data leaked into logs

Flag cases where logging is present but attached to the wrong layer or missing at critical boundaries.

---

### 8. Extensibility and Maintainability

Assess whether the change makes future evolution easier or harder.

Flag:
- god methods growing further
- multi-responsibility classes/functions
- new feature logic inserted into the wrong abstraction
- changes that require touching too many modules for one use case
- hidden coupling that will slow future change

Check whether the design still supports:
- isolated testing
- independent module evolution
- clean future refactoring

---

### 9. Testability of the Architecture

Review whether the structure remains testable.

Flag:
- service logic that cannot be unit tested without DB
- repository code that now requires unrelated dependencies
- business rules embedded in transport or infra layers
- hidden side effects that require full integration environment

Architecture should support testing at the correct level.

---

### 10. Alignment with Project Conventions

Check whether the implementation is consistent with existing project patterns.

Flag:
- introducing a new pattern where a project standard already exists
- inconsistent naming for equivalent architectural roles
- bypassing established abstractions
- ad-hoc design decisions that fragment the codebase

Do not encourage novelty unless justified by a real architectural need.

---

## Severity Rules

Use:

### high
Use when the issue introduces:
- structural coupling that will spread
- broken transaction design
- concurrency/data consistency risk
- architecture that will likely cause production incidents
- dependency direction violation with systemic impact

### medium
Use when the issue introduces:
- maintainability degradation
- unclear boundaries
- duplicated responsibilities
- weaker testability
- observability gaps
- extension difficulty

### low
Use when the issue introduces:
- local structure weakness
- naming or organization issue with real architectural impact
- minor inconsistency that should be corrected before it spreads

---

## Output Format

### 1. Architecture Findings

Format:

`file_path:line_number — architecture issue — severity: high|medium|low`

Only include real structural issues.

---

### 2. Drift Risks

List architecture-level risks introduced by the change, such as:

- layer erosion
- hidden coupling
- transaction ownership confusion
- future regression hotspots
- poor extensibility
- weak testability
- observability blind spots

---

### 3. Boundary Violations

List explicit layer/dependency violations in this format:

`source_module -> forbidden_dependency_or_responsibility — impact`

Example:

`src/repository/order_repository.py -> business rule evaluation — repository now owns domain decision-making`

---

### 4. Refactoring Guidance

Provide concrete structural fixes.

For each suggestion, explain:
- what responsibility should move
- where it should move
- why the new placement is architecturally correct

Do not suggest full rewrites unless absolutely necessary.

---

### 5. Final Architectural Verdict

Choose exactly one:

- architecture preserved
- minor drift detected
- significant drift detected
- architectural rework required

If the verdict is not `architecture preserved`, explain in 2–4 sentences why.

---

## Review Constraints

Do NOT:
- praise the implementation
- summarize the feature
- focus on formatting/style
- report generic clean code advice without architectural impact

Focus strictly on:
- structure
- ownership
- boundaries
- dependency direction
- transaction design
- concurrency design
- long-term maintainability