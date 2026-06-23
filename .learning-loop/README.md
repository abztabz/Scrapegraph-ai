# The Learning Loop

A small, disciplined system that turns each unit of work into durable knowledge,
so the same mistakes aren't made twice. Set up from the `learning-loop` skill.

**Model:** People (judgment) → Agents (execution) → Context (this shared brain).
Work **reads the brain first**, then writes traces back after.

**Five stages per cycle:**
Run → Capture → Distill → Promote → Verify → (Run again).

- **Run** — do the work, having skimmed `brain/` first.
- **Capture** — append a raw trace to `traces.md` (Outcome / Surprise / Lesson candidate).
- **Distill** — turn traces into general lesson candidates in `lessons.md`.
- **Promote** — a human admits only *proven* lessons into `brain/` (gates below).
- **Verify** — later cycles check promoted lessons are applied and still true; retire stale ones.

**Six promotion gates** (must clear all): pattern-not-incident · generality ·
actionability · non-contradiction · cost-of-relearning · ownership.
Promote on patterns (≥2 occurrences), not single events — except a high-severity,
obviously-correct lesson may promote on one. **Keep the brain scarce.**

## Files
- `brain/` — promoted lessons, by area. The Context brain. **Read before working.**
- `traces.md` — append-only raw log, one block per cycle.
- `lessons.md` — candidates between Distill and Promote, with status + occurrences.
- `promotions.md` — audit trail: what entered the brain, why, and which cycle confirmed it.

## How to run a cycle here
1. Open `brain/engineering-lessons.md` and skim before starting work.
2. After the work, append a trace to `traces.md`.
3. Distill into `lessons.md`; bump occurrences when a trace repeats a candidate.
4. Promote (human call) only what clears the six gates; record it in `promotions.md`.
5. On the next cycle, verify the promoted lessons were actually applied.

> To make promoted lessons *auto-applied* by future Claude Code sessions in this
> repo, copy them into a top-level `CLAUDE.md` (which agents load automatically).
