---
kind: orientation
pinned: true
tier: hot
uses: []
uses_inferred: []
---
# Retrieval checks

the owner approved this design on October 5, 2026. It tests whether the agent's memory answers his questions correctly. It does not by itself show retrieval accuracy.

## The bank

Twelve questions, three each for correct answers, corrections, uncertainty and quiet commitments. Most are questions the owner actually asked. Any others are labeled synthetic probes grounded in existing notes, never attributed to the owner. Eight questions form a stable core; four rotating slots may be refreshed after a full run.

## Running

Sleep 2 samples three questions nightly and runs all twelve on its first run after Sunday ([[memory/hermes/skills/brain-sleep2/SKILL|brain-sleep2]]). the owner gave standing permission on October 10 for separate fresh read-only testers. Each tester starts fresh and sees only the question. It never sees the answers, earlier results or these check notes. It never reads credential or code notes, including `sources/github-recovery-codes.txt` and `sources/Important Codes.md`.

## Grading

Grade on content and appropriate uncertainty, never wording. A pass needs the material facts, verified corrections, bounded uncertainty and any open commitment still open. A stale expectation or leaked answer is blocked or invalid, not a failure. Test fixtures earn no memory uses and never become facts.

## Files

The bank lives in `agent/Retrieval Checks/Questions.md` and results in `agent/Retrieval Checks/Results.md`. Sleep 2 creates both on its first Hermes run, from real questions.
