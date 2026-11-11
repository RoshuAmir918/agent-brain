---
name: {prefix}role
description: Do a piece of work as one of the organisation's roles, from brief to draft, with its decisions sorted and its work reviewed
version: 1.2.0
metadata:
  runtime:
    tags: [roles, work]
    category: brain
    editions: [enterprise]
---
# Working as a role

Use this when the work you were handed belongs to a role in `Roles/` (an engineering task, for a role whose template produces pull requests). The template, `Roles/<role>/Role.md`, says what the role makes, the actions it may take, its review lenses and the decision types people own. Work only on what you were handed: no plan of your own, nothing merged or shipped.

## 1. Brief
`brain pull`. Read `Roles/<role>/Role.md`, then the role's notes for the area and kind of work this touches: the Architecture of that area, the lessons for this kind, and the reference work they name. `brain roles show <role>` marks the lines a person wrote; theirs win over anything you infer. `brain roles correction <role> next` and the notes' sources show past corrections on these files: read them before you start, they are what this team has already had to tell you.

`brain roles lenses <role>` also says which steps are on for this role (`process`): with `gate` off, skip the gate and name the people-owned decisions in the description instead; with `review` off, skip steps 4 and 5. The account compares the two, so follow it either way.

## 2. Plan, with its decisions
Write the plan in your own words: what changes, in what order, how it will be checked, and what you will not touch. Then pick out only the decisions of a type the template says people own (for engineering: a customer-visible contract, a data change, authorization, rollout risk). How to build it (naming, structure, which helper, tests) is yours and is not listed; most tasks have none or one. Sort those few: `echo '<json>' | brain roles gate <role> --work <work id>`. Each decision is `{"decision": "<what is being decided>", "type": "<one of the template's decision types>", "reversible": true|false, "findable": true|false, "recommendation": "...", "default": "..."}`, with `"area"` when you know the `Owners.md` area. Be honest about reversible: a migration, a backfill, a changed customer-visible contract or anything a revert can't undo is not. With none, skip the gate. Its verdict is the rule, not advice:
- **decide**: the answer can be found. Decide, and cite the note line or the code.
- **flag**: someone's call that can be undone. Do it, and name it in the work's description with who owns it.
- **ask**: someone's call that is hard to undo, or an area they asked always to be asked about. Ask now, before building on it: `brain consult ask --to <person> --area <area> --ask "<the question>" --default "<the default>" --why "<one line>"`, a question answerable with a word. Carry on with everything that doesn't depend on it. If no answer comes, take the reversible option and say so, except in a restricted area: that work waits for the person's answer, whatever the default. Keep each answer and how it went (needed; "you could have found this"; "your call"): you record them once the work has its id, in step 6.

## 3. Implement and check
Make the change at the scope asked, in the shape the notes and reference work show. Run the repository's own checks (its tests, types, lint) and fix what fails because of you. A check that was already failing before you started is named in the description, not fixed in passing.

## 4. Review, independently
Review with fresh eyes you don't have yourself: call `review_work` with the ask (the words you were handed, whole), the diff, and the lenses and notes from `brain roles lenses <role>`. Every role has `solves_the_ask` first: does the change solve what was asked, at the scope asked? Its findings cite the ask and always block, and it also flags growth beyond the ask. Other lenses cite a note line or a file in the repository; a finding marked `decision for <person>` is not yours to settle in code: put it through the gate as a decision.

## 5. Revise
Only blockers ask for a revision; should-fix and nit findings are worth a look, and anything you leave goes in the description, not into more changes. For each blocker: fix it in code, or answer it with a reason that holds. Rewording the description is neither. Then call `review_work` again with `recheck` and the findings, your answers and the new diff: a reviewer that didn't write it says, per blocker, fixed, answered, or not fixed. Anything not fixed goes back to step 5 once more; if it still isn't, stop and say plainly what is left.

## 6. Open it as a draft
`brain roles actions <role>` says which connection does each of the role's actions here (for engineering: `git.push_branch`, `git.open_draft_pr`, `ci.run`), as `use <connection> <action>`; call exactly those, and if one is missing or switched off, stop and say so rather than finding another way. Push a branch and open a draft, then register it: `brain roles work <role> add <work id> --title "<title>" --link <url>`, and write down what you expect the reviewers to raise (the findings you left, the calls you flagged): `brain roles predict <role> --work <id> --expect "<one thing>"`, repeated, or with none if you expect nothing. Then record how each ask went: `brain roles asked <role> --work <id> --verdict needed|findable|unneeded --about "<what you asked>"`, and do the same for any answered later. The description says what changed and why in a few sentences, the decisions you flagged with whose they are, what you asked and what was answered, and the checks you ran. Never merge. The manager reviews first, then the area's owner; what they say is the strongest thing you will learn from, and the nightly study files it.

## When a review comes in on your draft
Each comment, change requested or edit someone else pushed is a correction. Fix the work, then record and file it the way the study skill does (`brain roles correction <role> add`, then `file`). Add `--miss retrieval` when a line already covered it but you didn't read it for this task, or `--miss "new taste"` when the team changed its mind; otherwise the kind of miss is worked out for you. When the comment is about who decides ("check with Kim before changing the schema", "always ask me about exports"), file it as a decision right, not a lesson: `brain roles correction <role> file <id> --decides "<area>" --person <platform>:<id>`, with `--always` when they said always. The gate reads it on the very next plan. Then check your other open work against what you just learned, the same day.
