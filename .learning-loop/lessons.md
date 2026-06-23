# Lessons log — candidates between Distill and Promote

Status: `candidate` → `held` / `promoted` (→ `retired`). Bump **Occurrences**
when a new trace supports the same candidate; that's how the pattern gate is met.

| ID | Statement (general principle) | Source traces | Occurrences | Status | Decision note |
|----|-------------------------------|---------------|-------------|--------|---------------|
| C1 | Probe the environment before declaring a limitation: never report "offline / not installed / unsupported" from a single failure — run a direct check (curl a known host + read `x-deny-reason`; surface the real ImportError) and report the specific cause. | T2, T3, T4 | 3 | **promoted → L-E1** | Confirmed pattern; high cost (gave the user a wrong explanation). Clears all six gates. |
| C2 | Isolate logic from heavy/conflicting deps for testing: stub the dependency in `sys.modules` to test pure logic; run repo code with `PYTHONPATH=<repo-root>` (or editable install) so local source isn't shadowed by a pip release; confirm via `module.__file__`. | T1, T3 | 2 | **promoted → L-E2** | Two distinct occurrences; medium cost (blocked all local testing). |
| C3 | Distinguish infra/repo-config CI failures from code failures (read the job-log error line first) before attempting a code fix. | T5 | 1 | held | True and useful, but single occurrence; hold for pattern confirmation per gate 1. |
| C4 | Friendly "dependency missing" error handlers should include the original exception text, not replace it. | T4 | 1 | held | Single occurrence; also a concrete tweak available to `uae_rent_watch.py`. Hold; apply on request. |
| C5 | For vague non-technical requests, ship low-cost sensible defaults aligned to stated constraints and state them, rather than blocking on questions. | T7 | 1 | held | Generic, low cost-of-relearning; hold to keep the brain scarce. |
| C6 | After adapting a template, re-read the diff for carried-over dead code before committing. | T6 | 1 | held | Low severity; standard hygiene. Hold. |
