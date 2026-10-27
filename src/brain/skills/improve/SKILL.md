---
name: {prefix}improve
description: Get better at one responsibility; notice work that should become one
version: 1.0.0
metadata:
  hermes:
    tags: [routine, day, research]
    category: brain
---
# Improve

The practitioner's pass. Runs each morning after the night and before the briefing, and asks two questions: how could {agent_name} do one of its existing responsibilities materially better, and did yesterday contain work that should become a responsibility. One investigation a day, carried across days until it is done. Never filler: a morning with nothing to recommend is a one-line ledger row.

A responsibility is standing work with judgment in it, held for {human_name} under a permission they gave, with three linked homes: a hub note at the vault root for understanding, an own skill under `{hermes_dir}/skills/` for method, and a folder under `$HERMES_HOME/records/` for state. The hub's frontmatter names both (`skill:` and `records:`); `brain responsibility` builds the set. The recall skill has the rules.

## Procedure
1. **Inventory.** Find every responsibility hub: `grep -l '^skill:' {vault}/*.md`. Read each hub's Improvements section and the last rows of `{agent_dir}/Improvements/YYYY-MM.md`. An improvement in `trialing` whose trial period has passed is assessed first: compare what happened in the records and ledgers with the intended result, then mark it `adopted` or `dropped` with the date and the evidence.
2. **Candidates.** Read the last seven days of `Conversations/Digests/`, the week's Explorations rows and open tasks. A request {human_name} has made more than twice, a task that recurs or has no end, a thread explore keeps choosing, a job done by hand twice: each is a candidate responsibility. Write each as one `morning:` line on the nearest task entry: what the work is, what good would look like, which permission it needs, and that it would get a hub, a skill and a records folder. Propose, never adopt: taking on standing work is {human_name}'s decision, made in conversation.
3. **Pick one responsibility** to improve, by demonstrated weakness (a lost outcome, a stale strategy, an unused permission), likely benefit, and neglect (the hub whose Improvements were touched longest ago). An investigation started yesterday and not finished comes first.
4. **Name the gap**, one of four: knowledge or strategy; a broken implementation; a permission {agent_name} holds and does not use; a permission it lacks. The first three are {agent_name}'s to close. The fourth becomes a request to {human_name}, and the research still happens so the request arrives with a case, not a wish.
5. **Research as a practitioner.** What would a skilled person in this role investigate, measure and do? Real sources and outside expertise, applied to the actual situation in the hub and the records, not a summary of the field. Depth over breadth.
6. **Write the recommendation** in the hub's Improvements section: date, the gap, the evidence, the specific move or operating change, the expected benefit and how it would be judged, `status: proposed`. A strategy that has not been tested is labelled hypothesis. A proposal is not an improvement, and a change that ran is not a change that worked; the status says which. Then one `morning:` line on the responsibility's task entry, so the briefing carries it.
7. **Ledger.** Append one line, at most 25 words, to `{agent_dir}/Improvements/YYYY-MM.md`: `date | responsibility | gap | what was done | status`. A quiet pass is `date | none | nothing to recommend` and nothing more.

## Boundaries
Writes only responsibility hubs' Improvements sections, the ledger and `morning:` lines. Never a skill, a permission, a schedule, Triggers, the Memory Spec or `{sources_dir}/`; the skill edit happens in the session where {human_name} adopts, under the recall skill's rule. No sends. No action inside the responsibility itself: its own routine acts, this one studies. A finding never widens what {agent_name} may do; it argues for it.
