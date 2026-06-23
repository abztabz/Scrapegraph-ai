# Engineering lessons — the Context brain

Promoted, proven lessons. **Read this before starting work in this repo.**
Keep it scarce: every entry is read on every relevant Run, so each must earn its place.

---

### L-E1 — Probe the environment before declaring a limitation
Promoted: 2026-06-23 (pattern confirmed across 3 traces in cycle 1).
**Rule:** Never tell the user the environment is "offline", a dependency is "not
installed", or a capability is "unsupported" based on a single failed command.
Probe first and report the *specific* cause:
- Network: `curl -sS -o /dev/null -w '%{http_code}\n' <known-good-host>` and read
  any `x-deny-reason` header — an egress **allowlist** (`host_not_allowed`) is not
  the same as being offline, and some hosts may be allowed while others aren't.
- Imports: surface the underlying exception (e.g. `python -c "import pkg.sub"`);
  don't trust a wrapper's "not installed" message — it may hide a version conflict.
**Why:** In cycle 1 I told the user there was "no internet" (network was up, just
allowlisted) and chased "scrapegraphai not installed" (it was a `langchain`
version-conflict ImportError). Both were wrong explanations given to the user.
Owner: repo maintainer.

### L-E2 — Make logic testable independently of heavy/conflicting deps
Promoted: 2026-06-23 (pattern across 2 traces in cycle 1).
**Rule:** To test a module that imports a heavy or environment-broken dependency:
- inject a stub into `sys.modules` *before* importing the module, to exercise pure
  logic offline; and
- when the repo is also pip-installed, run its code with `PYTHONPATH=<repo-root>`
  (or an editable install) so the local source is used, not a shadowing release.
- Confirm which copy loaded with `module.__file__`.
**Why:** `pydantic`/`scrapegraphai` were absent (stub let logic tests run), and a
pip-installed `scrapegraphai` shadowed and broke the repo source until `PYTHONPATH`
pointed at the repo root.
Owner: repo maintainer.
