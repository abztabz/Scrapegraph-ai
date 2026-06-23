# Traces — append-only

Work-stream: **claude-engineering** (how this assistant builds in this repo).
Format per entry: Outcome / Surprise / Lesson candidate. Never edit past traces.

---

## 2026-06-23 / cycle 1 — build "UAE Rent Watch" example + watch PR #4

### T1
Outcome: Built a full example tool (engine + CLI + stdlib web app + README +
GitHub Actions workflow) by closely mirroring the existing
`examples/amazon_uae_product_research` example. Logic verified with tests.
Surprise: Could not import the new module directly — `pydantic` and
`scrapegraphai` were not installed in the sandbox.
Lesson candidate: Stub heavy deps in `sys.modules` before import to unit-test pure
logic without installing them.

### T2
Outcome: Told the user the sandbox had "no internet" and that a live run wasn't
possible. Later probing showed the network was up but behind an egress allowlist.
Surprise: `pip install` succeeded (PyPI reachable) while the property portals
returned HTTP 403 with header `x-deny-reason: host_not_allowed`; the Gemini API
host was actually allowlisted. Network present, not absent.
Lesson candidate: Don't infer "offline" from a failed import or one blocked host;
probe connectivity explicitly and read the deny reason before reporting it.

### T3
Outcome: `import scrapegraphai.graphs` failed from the example dir but succeeded
from the repo root; the `--demo` run only worked after `PYTHONPATH=<repo-root>`.
Surprise: Same import, different result by cwd — the pip-installed release shadowed
the repo's local source and had a `langchain_community` version-drift bug
(`cannot import name 'ChatOllama'`).
Lesson candidate: When a repo is also pip-installed, import resolution depends on
cwd/sys.path; run repo code with `PYTHONPATH=<repo-root>` and confirm via
`module.__file__` which copy loaded.

### T4
Outcome: My module's `except ImportError: raise SystemExit("scrapegraphai is not
installed…")` masked the real error and sent me chasing "not installed" when the
true cause was a dependency version conflict.
Surprise: The friendly error message actively misled the diagnosis.
Lesson candidate: Friendly "dependency missing" handlers should surface the
original exception text, not replace it.

### T5
Outcome: The `dependency-review` CI check failed on every push; root cause was a
repo setting (Dependency graph not enabled), unrelated to the PR. Correctly
diagnosed as non-actionable and did not attempt a code fix.
Surprise: A persistently-red CI check that is a repo-config issue, re-firing on
every push.
Lesson candidate: Distinguish infra/repo-config CI failures from code failures —
read the job log's error line first — before attempting any code fix.

### T6
Outcome: First draft of the web app's `run_job` carried leftover dead code (an
unused `type("C", …)` shim and unused imports) copied from the template; caught
and removed before commit.
Surprise: none.
Lesson candidate: After adapting a template, re-read the diff for carried-over
dead code before committing.

### T7
Outcome: User gave vague, non-technical requirements (zero budget, no coding,
"not sure"). Shipped sensible zero-cost defaults (email alerts, free GitHub
Actions scheduling, free LLM tiers) and stated the choices explicitly.
Surprise: none.
Lesson candidate: For vague non-technical requests, choose low-cost/low-friction
defaults aligned to stated constraints and state them rather than blocking on
questions.
