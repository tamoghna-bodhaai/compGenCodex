# Application Completeness Remediation Plan

## Problem Statement

The application has most of the visible parts of a question-ingestion, question-variation, paper-authoring, and CBT test engine, but several existing flows do not complete safely from end to end.

The most serious gaps are not missing optional features. They are broken contracts between already-enabled features:

- ingested questions can be published without completing the advertised human-review step;
- the JEE Main/NTA paper template is validated by the backend but cannot be assembled in the current single-subject authoring flow;
- invalid sessions can enter a redirect loop and logout currently fails through the web proxy;
- database initialization and legacy migrations run in normal request paths;
- cancellation does not stop blocking provider/OCR work promptly;
- direct student assignments and accommodations use inconsistent authorization rules;
- multi-attempt tests expose only one ambiguous “latest attempt” view;
- mobile navigation, paper editing, and CBT attempt screens become inefficient or unusable at realistic paper sizes;
- current automated tests do not exercise the browser, responsive layout, or frontend-to-backend proxy boundary.

The target is a dependable existing workflow:

1. ingest a source;
2. inspect, correct, approve, and publish questions;
3. assemble single- or multi-subject papers and generate variations;
4. publish an NTA/JEE-compliant CBT test;
5. assign it to a class or student;
6. take, resume, submit, release, and review attempts;
7. operate all of those flows on desktop and mobile without inaccessible controls or misleading state.

## Scope and Priority

| Priority | Completion gap | Release consequence |
| --- | --- | --- |
| P0 | Logout fails; review approval is bypassed | Blocks a trustworthy release |
| P1 | JEE template cannot be authored; invalid-cookie loop; request-time DB migration; weak cancellation; direct-assignment accommodations; mobile editor/navigation/CBT layout | Blocks production use of advertised flows |
| P2 | Attempt history ambiguity; stale submit count; media rollback; generic proxy failure; missing approved filter; inaccessible labels/targets; native prompt/confirm flows | Causes data ambiguity or frequent operator/user failure |
| P3 | Shallow deployment health check; local startup documentation mismatch; stale frontend assertion | Weakens diagnosis and contributor reliability |

## Solution

Repair the application in dependency order rather than redesigning it wholesale.

First, establish reliable session, proxy, health, and test boundaries. Next, make question review a real state machine whose approval cannot be inferred from publication. Then remove request-time infrastructure work and make long-running jobs cancellable and rollback-safe. After those foundations are stable, introduce a canonical exam-template contract and make paper authoring multi-subject when the selected template requires it. Finally, align attempt semantics and rebuild the responsive navigation, paper editor, and CBT layout around the actual 75-question use case.

Each commit below is intentionally small. Every commit must keep migrations backward-compatible, preserve existing single-subject/custom-paper behavior, and leave the test suite runnable.

## Architectural Decisions

### Question review and publication

- Review state and bank/publication state are separate concerns.
- The review state machine is `pending_review -> approved | rejected`. Editing an approved question returns it to `pending_review` unless the edit is explicitly approved in the same authorized operation.
- Publishing a question never changes its review state.
- Only approved questions with a complete answer/response specification are selectable for normal paper authoring or variation generation.
- Existing records previously marked approved by publication remain readable. A migration records legacy approval provenance so operators can distinguish historical auto-approval from explicit review without silently removing live content.
- Review actions record reviewer, review time, and an optional note. These fields are audit metadata, not a new collaboration feature.

### Exam templates and paper subject scope

- The backend owns one canonical descriptor for each supported exam template: subjects, section order, question modes, counts, marking rules, and duration.
- The frontend reads that descriptor; it does not duplicate JEE counts in component code.
- A paper stores a subject scope list. Existing single-subject papers are migrated to a one-item list and keep their current display value for backward compatibility.
- Custom papers may remain single-subject. Selecting the existing JEE Main Paper 1 template switches creation into its required Mathematics/Physics/Chemistry structure.
- Publish validation uses the same descriptor used to render authoring progress, preventing authoring and publication rules from drifting.

### Database and background work

- Schema creation/migration runs once during application startup, before traffic is accepted.
- Opening a normal database connection may set connection pragmas, but may not inspect or mutate schema.
- Provider HTTP calls use a cancellable async client with explicit connect, read, write, and total timeouts.
- OCR/source preparation checks cancellation between bounded units of work. Shutdown has a finite grace period and reports jobs that fail to stop.
- Files copied while constructing an immutable test version are tracked and removed if the enclosing transaction fails.

### Sessions and proxy behavior

- Logout always returns an explicit HTTP 204 response after expiring the cookie.
- An invalid/expired cookie is cleared when session validation fails.
- The login route is never redirected solely because a cookie exists; authenticated route handling may still use a fast presence check, followed by authoritative session validation.
- Proxy transport failures return a structured 502 response that the existing client error handling can display.

### Assignments and attempts

- Accommodation authorization accepts a student who is directly assigned to the test or belongs to a teacher-owned assigned class. It still rejects students outside the teacher's managed scope.
- “Active attempt” means the resumable in-progress attempt.
- “Latest submitted attempt” means the newest completed/submitted attempt and is used for released results and teacher analytics.
- Attempt history is preserved and visible. Starting a later in-progress attempt must not hide an earlier submitted result.
- Teacher aggregate analytics use one latest submitted attempt per student so retries do not silently overweight a learner.

### Responsive interaction model

- Small-screen navigation uses an explicit drawer with focus management and a visible menu trigger; it does not rely on discovering horizontal overflow.
- The mobile paper editor has explicit `Questions`, `Edit`, and `Answer key` views. Selecting a question opens its edit view, so the inspector is not placed below dozens of cards.
- The CBT screen uses a persistent exam header/timer, a stable answer-action area, and a question palette that is sticky on desktop and a drawer on mobile.
- Interactive targets are at least 44 by 44 CSS pixels where practical, and all controls have persistent accessible names rather than placeholder-only labels.

## Delivery Sequence and Tiny Commits

### Phase 0 — Establish release gates

1. **Document the audited critical workflows and supported viewport matrix.** Add the seven end-to-end workflows from the problem statement and desktop/tablet/mobile viewport expectations to the test documentation. No runtime behavior changes.

2. **Correct the stale login assertion.** Make the existing frontend test assert the current generic login contract rather than a removed literal email address. Confirm this is the only pre-existing full-suite failure.

3. **Add a browser-test skeleton.** Add a Playwright configuration, isolated test database/bootstrap, web-server lifecycle, and one login-page smoke test. Keep browser tests separate from fast unit tests but available through one documented command.

4. **Add deterministic fixtures for complete question types.** Provide test-only approved single-choice and numerical questions for Mathematics, Physics, and Chemistry, including response and marking specifications. These fixtures will support later full-template and CBT tests.

**Gate 0:** backend unit/API tests, frontend unit tests, lint, typecheck, production build, and the browser smoke test all pass from a clean checkout.

### Phase 1 — Repair the session and service boundary

5. **Return a concrete logout response.** Construct the 204 response explicitly, expire the session cookie on that response, and add an API-level regression test for status and cookie headers.

6. **Exercise logout through the frontend proxy.** Add a browser/API integration test that logs in, invokes the proxied logout endpoint, verifies 204, and confirms the next session check is unauthenticated.

7. **Clear invalid cookies during session validation.** When a cookie cannot be resolved, return the unauthenticated session payload and expire the bad cookie. Add expired, unknown, and malformed-cookie tests.

8. **Remove cookie-presence redirecting from the login route.** Allow the login screen to render even if a stale cookie exists; redirect only after a successful authoritative session check. Add a browser test proving there is no `/login` ↔ `/` loop.

9. **Normalize proxy transport failures.** Catch backend connection/reset/timeout errors and return a JSON 502 with the same `detail` shape used by normal API errors. Preserve backend status and body when a response was received.

10. **Expose a truthful readiness check.** Keep a cheap liveness endpoint, add a readiness response that performs a bounded database quick-check and required storage writeability check, and point deployment health configuration at readiness.

11. **Fix route identity for administration.** Add the existing admin route to the shell's route-title mapping and cover route titles with a small component test.

**Gate 1:** logout succeeds through the real proxy, stale cookies self-heal without redirects, backend outage produces a useful 502 message, and deployment readiness fails when the database is unavailable.

### Phase 2 — Make ingestion review enforceable

12. **Centralize review states and transitions.** Replace scattered string checks with one backend review-state definition and transition validator. Existing read responses remain compatible.

13. **Add review audit columns with an idempotent migration.** Store reviewer, reviewed time, review note, and approval provenance. Backfill existing approved rows with `legacy_publication` provenance; do not silently demote them.

14. **Add a structured question correction endpoint.** Accept the editable question, options, answer/response specification, solution, classification, and exam metadata with normal schema validation. A correction to an approved item returns it to pending review.

15. **Add explicit approve and reject operations.** Require a complete, type-valid answer before approval; record actor and time; reject invalid transitions; return the updated question.

16. **Stop publication from granting approval.** Remove the publication-side review mutation. Reject publication of a non-approved or answer-incomplete question with an actionable conflict response.

17. **Turn source review into a real review form.** Let operators edit accepted ingested records using typed fields, save corrections, approve, or reject. Retain raw JSON editing only for recovery cases where parsing did not yield a normal question.

18. **Expose all review states in the question bank.** Add `approved` and `rejected` to filters, show approval provenance where relevant, and make readiness for paper use explicit.

19. **Prevent dead-end selection.** Disable selection of pending/rejected/answer-incomplete questions in paper dialogs and link the operator to the existing review/edit location instead of allowing a later publish failure.

20. **Cover the review lifecycle end to end.** Test ingest → pending → correct → approve → publish, edit-after-approval → pending, reject, and attempted publish without approval. Assert that publication never changes review metadata.

**Gate 2:** no newly ingested question can enter an authored/published paper without explicit approval, while legacy content remains visible and identifiable.

### Phase 3 — Remove request-time infrastructure work and unsafe cancellation

21. **Separate database connection opening from initialization.** Make normal connection acquisition open the database and apply connection pragmas only. Keep schema initialization in the application lifespan.

22. **Make startup migration execution explicit and observable.** Run the current idempotent migration sequence once, fail startup with a clear diagnostic on migration failure, and log the resulting schema version.

23. **Prove requests do not mutate schema.** Add a concurrency regression test that opens many normal API/database operations and asserts no migration/DDL path is invoked after startup.

24. **Replace blocking provider HTTP threads.** Move generation/provider requests to one managed async HTTP client with bounded timeouts, cancellation propagation, and startup/shutdown lifecycle hooks. Preserve current retry and error translation behavior.

25. **Bound ingestion source preparation.** Split document/OCR work into page or chunk units that check cancellation between units and update progress only after a unit completes.

26. **Make job cancellation terminal and idempotent.** Define `cancelling`, `cancelled`, `failed`, and `completed` transitions so repeated cancel requests are safe and late worker results cannot overwrite cancellation.

27. **Clean copied media on transaction failure.** Track every destination created during test-version publication and remove only those new files if validation or database commit fails. Add failure-injection tests.

28. **Add shutdown and cancellation regression tests.** Start provider and ingestion work against deliberately slow fakes, cancel it, and assert the process shuts down within the configured grace period without executor/thread warnings.

**Gate 3:** ordinary requests perform no schema work, cancellation reaches a terminal state promptly, shutdown is bounded, and failed publication leaves neither database rows nor media files behind.

### Phase 4 — Make the existing NTA/JEE template authorable

29. **Create the canonical assessment-template descriptor.** Represent the existing JEE Main 2026 Paper 1 rules once in the backend and expose a read-only template endpoint containing its subjects, sections, modes, counts, duration, and marking rules.

30. **Make publish validation consume the descriptor.** Replace hard-coded parallel checks with descriptor-driven validation while preserving current error semantics and the 75-question requirement.

31. **Add paper subject scope storage.** Introduce a subject-list field and backfill every paper from its current single subject. Continue returning the legacy single-subject field during the migration window.

32. **Extend paper creation contracts for template scope.** Accept either the current custom single-subject configuration or a known template identifier. Reject arbitrary mixed-subject custom payloads in this remediation to keep scope controlled.

33. **Refactor authoring state around subject-section slots.** Store bank selections and variation plans under subject and section rather than one page-level subject. Preserve existing custom-paper state and recovery behavior.

34. **Render template-driven paper creation.** When JEE Main is selected, show Mathematics, Physics, and Chemistry sections with required single-choice/numerical counts and source availability for each slot.

35. **Allow bank and source selection within each subject section.** Scope dialogs to the active template slot, keep selections when switching subjects, and prevent the same bank question from occupying multiple slots unless existing duplication rules explicitly allow it.

36. **Show authoring completeness before paper creation.** Display missing/excess counts and answer/readiness blockers using the canonical template descriptor. Disable final creation only for true contract violations.

37. **Preserve subject metadata through variations and paper edits.** Ensure generated and manually added questions retain their effective subject and section metadata so later publication validation sees the authored structure.

38. **Add a full-template integration test.** Assemble 20 single-choice plus 5 numerical questions for each required subject through the public authoring APIs, publish the CBT test, and assert duration, marks, order, and metadata.

39. **Add a browser test for the same flow.** Use deterministic fixtures to create the three-subject paper in the UI and reach a successful publish confirmation without direct database setup.

**Gate 4:** an operator can author and publish the existing JEE Main 2026 Paper 1 template entirely through the UI; custom single-subject papers still work unchanged.

### Phase 5 — Align assignment, accommodation, and attempt semantics

40. **Centralize student-assignment authorization.** Resolve whether a student is assigned directly or through a managed class in one service used by access-code, accommodation, and attempt endpoints.

41. **Permit accommodations for direct assignments.** Apply the same ownership/scope checks as class assignment, reject unrelated students, and add positive and negative API tests.

42. **Separate active and latest-submitted attempt queries.** Add explicit repository/service operations; stop using one “latest” row for resume, results, and analytics.

43. **Return attempt history in the student test contract.** Include all attempt summaries, the resumable active attempt, attempts remaining, and latest submitted result without removing current fields until the frontend migration is complete.

44. **Update the student test list and detail views.** Resume only the active attempt, show each submitted attempt in order, and keep an earlier result visible while a newer attempt is in progress.

45. **Make teacher analytics use latest submitted attempts.** Use at most one submitted attempt per student, label the policy in the UI, and test the case where a later in-progress attempt exists.

46. **Make linked-paper summaries use submitted results.** Resolve each component from its latest submitted attempt so an unfinished retry cannot erase a released combined result.

47. **Fix submit confirmation state.** Incorporate the currently selected unsaved answer into the prospective response set before showing answered/unanswered counts, then persist that exact set if the user confirms.

48. **Remove deprecated ambiguous attempt fields.** After frontend and tests use the explicit fields, remove or clearly alias the old latest-attempt value in a separate compatibility cleanup commit.

**Gate 5:** direct assignees can receive accommodations, every allowed attempt is discoverable, resume/result behavior is unambiguous, and analytics do not double-weight retries.

### Phase 6 — Repair the application shell and interaction accessibility

49. **Introduce an accessible mobile navigation drawer.** Add a visible trigger, active-route indication, Escape/outside-click close behavior, focus trapping/restoration, and body-scroll locking. Keep the desktop sidebar unchanged.

50. **Keep the active destination discoverable.** On desktop preserve existing active styling; on mobile put the current route first in the drawer's focus order and expose the route name in the top bar.

51. **Apply persistent labels to operational forms.** Replace placeholder-only student, class, bank-filter, access-code, and administrative inputs with the existing labeled field primitive or equivalent accessible names.

52. **Raise touch targets consistently.** Adjust navigation, sign-out, filter, editor-action, and palette controls to a minimum 44-pixel target without inflating dense desktop layouts unnecessarily.

53. **Add an application dialog primitive for data entry and confirmation.** Provide focus management, labelled title/description, keyboard dismissal where safe, error focus, and async disabled state.

54. **Replace native prompts and confirms in core flows.** Migrate section creation, accommodations, access code entry, destructive paper actions, and final test submission to the application dialog primitive.

55. **Add keyboard and screen-reader smoke coverage.** Verify landmarks, labelled controls, visible focus, drawer/dialog focus return, and keyboard completion of login, paper navigation, and answer selection.

**Gate 6:** all existing routes are reachable at 390 pixels without horizontal discovery, core forms retain labels after typing, and core dialogs are keyboard-operable.

### Phase 7 — Make the paper editor usable for realistic papers

56. **Remove the mobile action flex-basis regression.** Reset the desktop-only action sizing at responsive breakpoints and add a screenshot assertion proving the empty 560-pixel gap is gone.

57. **Introduce explicit editor view state on small screens.** Add `Questions`, `Edit`, and `Answer key` modes while retaining the current two-column desktop layout.

58. **Open the selected question in the mobile edit view.** Preserve selection when returning to the list, announce the selected question, and provide previous/next navigation without scrolling through the entire list.

59. **Keep paper-level actions reachable.** Make save/export/publish status compact and sticky where appropriate, without covering question content or native browser controls.

60. **Virtualize or paginate only the editor question list if measurement proves necessary.** First remove layout pathologies and use lightweight rendering. Introduce list windowing only if the 75-question performance budget is missed; this is a conditional corrective step, not a feature.

61. **Test long-paper edit and recovery behavior.** At 390, 768, and 1440 widths, open question 75, edit it, navigate away and back, reload recovered state, and verify all actions remain reachable.

**Gate 7:** a 75-question paper can be edited at mobile width without multi-screen blank gaps or scrolling past 74 cards to reach the inspector.

### Phase 8 — Make the CBT attempt interface exam-usable

62. **Create the stable exam layout shell.** Keep test identity, connection/save state, and remaining time visible independently of page scroll. Ensure timer updates do not cause layout shift.

63. **Use a desktop question-and-palette layout.** Place the current question in the main region and a sticky status/palette panel beside it. Keep palette status semantics consistent with saved responses and review flags.

64. **Move the palette into a mobile drawer.** Provide answered, unanswered, marked, current, and not-visited status text in addition to color. Return focus to the invoking control after selection.

65. **Add a persistent answer action bar.** Keep clear response, mark/unmark review, save-and-next/previous, and submit reachable without returning to the top or bottom of the document.

66. **Make save state truthful.** Distinguish locally selected, saving, saved, offline/retry, and failed states. Disable destructive navigation only while required to prevent data loss.

67. **Make timeout and submit converge on one idempotent path.** Prevent duplicate submissions, preserve the last visible answer, and show a recoverable result when the server already accepted submission.

68. **Add realistic CBT browser coverage.** Run a 75-question test at desktop and mobile widths, navigate by palette, mark for review, reload/resume, submit with an unsaved visible answer, and verify released results.

**Gate 8:** a student can complete the full CBT workflow without repeated long scrolling, hidden timing information, lost answers, or misleading submit counts.

### Phase 9 — Documentation, rollout, and release verification

69. **Correct local startup documentation.** Document the supported Uvicorn command or add the matching FastAPI CLI dependency; choose one and prove it in CI. Do not leave a command that fails on a clean install.

70. **Add migration and rollback notes.** Document the subject-scope and review-audit migrations, pre-deploy backup, startup failure behavior, and rollback compatibility.

71. **Add operational diagnostics.** Log request/job identifiers, review transition failures, cancellation timing, readiness failures, and media rollback cleanup without logging question source contents or session secrets.

72. **Run the complete release matrix.** Execute backend, frontend, browser, migration-from-existing-database, shutdown, and responsive screenshot suites. Record duration and remove any test whose only assertion is an implementation detail.

73. **Perform a seeded acceptance run.** Starting from an empty database, complete ingestion, review, JEE authoring, assignment, accommodation, student attempt, result release, and analytics through public APIs/UI only.

74. **Remove migration-window compatibility code.** Only after one successful release and database backup validation, remove temporary legacy response aliases and UI fallbacks in isolated cleanup commits.

**Final release gate:** every acceptance criterion below passes on a migrated database and a fresh database.

## Acceptance Criteria

- Logout returns 204 and clears the cookie through both backend and frontend proxy paths.
- A malformed or expired cookie cannot cause a login redirect loop.
- Backend unavailability produces a structured, user-visible 502 rather than an opaque Next.js 500.
- Readiness fails when the database or required writable storage is unavailable.
- Normal requests execute no DDL, schema introspection, or legacy data backfill.
- Cancelling generation/ingestion reaches `cancelled` within the defined grace period and application shutdown emits no stuck-executor warning.
- Failed immutable-version creation leaves no copied orphan media.
- Newly ingested questions cannot be selected or published before explicit approval.
- Editing an approved question invalidates its approval unless approval is explicitly re-applied by an authorized review action.
- Approved, pending, rejected, and legacy-approved questions are distinguishable in the UI.
- The UI can author and publish exactly 75 JEE Main Paper 1 questions: 20 single-choice and 5 numerical in each of Mathematics, Physics, and Chemistry, at 180 minutes with the existing marking rules.
- Existing custom single-subject papers continue to create, edit, export, and publish.
- A directly assigned student can receive an accommodation, while an unrelated student cannot.
- A student with multiple attempts can resume the active attempt and inspect every submitted attempt; an in-progress retry does not hide the prior result.
- Teacher and linked-paper results consistently use the latest submitted attempt per student.
- Submit confirmation counts the currently selected answer even before autosave completes.
- At 390 pixels, every application destination is discoverable, all core controls are reachable, and there is no document-level horizontal overflow.
- At 390 pixels, question 75 can be selected and edited without scrolling past the full question list to reach editing controls.
- During a CBT attempt, timer, current question, navigation, answer actions, and submit remain reachable on 390-, 768-, and 1440-pixel viewports.
- Core workflows are keyboard-operable with visible focus and programmatic labels.
- Backend tests, frontend tests, lint, typecheck, production build, browser E2E, migration, and shutdown tests pass from a clean checkout.

## Testing Decisions

### What makes a good test

- Assert observable behavior at a stable boundary: HTTP contract, persisted state, rendered accessible UI, file lifecycle, or job state.
- Avoid asserting private helper calls, exact CSS class names, SQL text, or incidental component structure.
- Use deterministic local provider/OCR fakes. Do not make release tests depend on external model availability.
- Test both a fresh database and an existing pre-migration fixture.
- For every failure path, assert both the response shown to the user and the absence of partial side effects.

### Test layers

- **Service tests:** review transitions, template validation, assignment resolution, attempt selection, scoring, cancellation state, and media cleanup.
- **API tests:** authentication cookies, review endpoints, paper subject scope, template publication, accommodations, attempt history, readiness, and conflict/error shapes.
- **Frontend unit/component tests:** route titles, authoring state reducers, prospective submit counts, status labels, and responsive-view state transitions.
- **Browser E2E tests:** proxy logout, stale session, full review flow, three-subject authoring, long-paper mobile editing, and full CBT attempt/resume/submit.
- **Visual checks:** focused screenshots at 1440×1000, 768×1024, and 390×844 for the shell, paper editor, selection dialog, and CBT attempt screen. Mask timer values and other nondeterministic content.
- **Operational tests:** startup migration, concurrent request behavior, provider timeout, job cancellation, graceful shutdown, database/storage readiness, and rollback cleanup.

### Test-data policy

- Maintain a minimal complete fixture for each currently supported question response mode.
- Maintain one exact 75-question JEE fixture for contract and browser testing.
- Keep production-like PDFs/images small and synthetic; test metadata preservation without committing copyrighted exam material.

## Rollout Plan

1. Ship Phases 0–3 first as a reliability release. These changes should not require operators to learn a new authoring flow.
2. Back up the database, apply review-audit and paper-subject migrations, and verify counts/provenance before enabling the new review enforcement.
3. Ship Phases 4–5 together so multi-subject authoring and canonical publish validation cannot become temporarily inconsistent.
4. Ship shell/accessibility changes before the paper and CBT responsive layouts because both depend on the drawer/dialog primitives.
5. Run the seeded acceptance flow against a copy of real data before production promotion.
6. Keep backward-compatible API fields for one release, observe logs for legacy clients, then remove compatibility code in Phase 9.

## Out of Scope

- adding new exam families or additional NTA templates;
- new AI generation capabilities, new question types, or richer variation controls;
- new analytics products, leaderboards, proctoring, payments, notifications, or collaboration features;
- a visual rebrand or broad component-library rewrite;
- native mobile applications;
- changing existing scoring policy beyond making its current attempt selection explicit and consistent;
- performance optimizations that are not required to meet the audited 75-question workflows;
- bulk review or bulk editing unless required later to resolve a measured blocker.

## Completion Order Summary

The critical path is:

`release baseline -> session/proxy -> review enforcement -> database/cancellation -> canonical template -> multi-subject authoring -> attempt semantics -> accessible shell -> mobile paper editor -> CBT layout -> acceptance run`

Work should not start on visual polishing while the corresponding data/state contract remains ambiguous. Conversely, the responsive editor and CBT phases are release requirements, not optional polish: the current layouts prevent normal use of already-supported 75-question tests.
