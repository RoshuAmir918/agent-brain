---
kind: orientation
pinned: false
tier: cold
uses: []
uses_inferred: []
activation: -99.0
---
# Agent profile template

Copy to `agents/<Name>/Profile.md`. Keep every section; write "unknown" rather than leaving it out.

```markdown
---
kind: profile
tier: warm
pinned: false
uses: []
uses_inferred: []
---
# <Name>

**Purpose.** What it is for, in one sentence, in the owner's words if he gave them. Date and source.

**Runtime and reach.** Where it runs, what it can touch (repos, accounts, services), what it cannot. Where its access is configured, not the access itself.

**How to reach it.** Channel, handle, whether it wakes on mention. Whether it reaches the agent or only the owner.

**When the agent uses it.** The kinds of work to hand it, and the kinds never to. Whether the agent may assign work unprompted or only on the owner's instruction.

**How to work with it.** Brief shape, checkpoints, what a good report from it looks like, how to handle silence or a stalled task.

**Trust.** What its reports can be taken as (claims, verified results), and what must be checked independently.

**Status.** Active, paused or retired, with the date and who decided.

**History.** Dated lines, newest first: assignments, notable results, incidents.
```
