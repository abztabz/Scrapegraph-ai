# Promotion record — audit trail

One line per promotion: what, why, which cycle confirmed the pattern.

- **L-E1** "Probe the environment before declaring a limitation" — promoted
  2026-06-23 after cycle 1 confirmed a pattern across 3 traces (T2/T3/T4). High
  cost of relearning: gave the user an incorrect explanation twice ("no internet";
  "not installed"). Clears all six gates.
- **L-E2** "Make logic testable independently of heavy/conflicting deps" —
  promoted 2026-06-23 after cycle 1 showed it twice (T1 missing deps; T3 a
  pip-installed release shadowing the repo source). Medium cost: blocked local
  testing until resolved.

_Held (not promoted) this cycle: C3, C4, C5, C6 — single occurrences kept as
candidates per gate 1 (pattern, not incident). They may earn promotion later._
