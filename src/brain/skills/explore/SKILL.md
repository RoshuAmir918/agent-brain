---
name: {prefix}explore
description: Do one thing for {human_name}, or nothing
version: 4.5.0
metadata:
  runtime:
    tags: [routine, day, research]
    category: brain
    context: [start, primed, current, goals, tasks, triggers, upcoming]
---
# Explore

The standing-attention pass, one question: **what could I do for {human_name} today?** Work, a person in their life, or something they would enjoy. It runs each morning and again only when something was added (at most three times a day); the Run Context says which pass this is and what was added. Few passes, so spend each one: look things up and make the thing properly.

## Procedure
<!-- personal -->
Read `brain goals list --json` first: honour each goal's weight (`more` about twice as often, `less` half, `pause` no unasked work) and never repeat work its corrections rule out. Weights don't touch asked work or existing duties.

**Every candidate has a reason you can point to.** One of four, named on the candidate:
- an active goal, `G-n`, from `brain goals list --json`; said and inferred goals fund preparation immediately;
- {human_name}'s own words in the last three days, quoted with the conversation they are from ("I have no idea what to cook this week");
- an open loop in `brain loops`, by its id;
- a dated item in the next seven days from Upcoming, Calendar or Canvas, by its date.

No reason, no candidate; never invent a need. Goals come first among comparable candidates. People owns goal inference.
<!-- /personal -->
1. **Start from the Run Context:** on a later pass, what was added is where to look first. Then read Primed and Upcoming; they are the brief.
2. **Candidates, three classes.**
   - *Work:* open questions from yesterday's sessions and `brain loops`; open tasks whose next step needs no approval; beside a handed-off task, only what its worker is not making, held under `{agent_dir}/Prepared/` with `task: T-nnn` (never its next step, its Delivers or the task itself); beside a `yours` task, something prepared for when they pick it up, never a reminder; Canvas deadlines this week, with prep from the assignment and rubric, never the graded work itself; dated items to prepare for; hubs touched this week with a gap; active goals in `{human_goals}` with recent evidence; and anything in their live channels (`{agent_dir}/Channels.md`) you can take off their plate at the level the row allows (files under `$AGENT_HOME/channels/<account>/`, acting through the row's command): a reply drafted and held, a form filled in, a bill due turned into a `when` loop.
<!-- personal -->
   - *People:* someone in their life with something this week, or waiting on them in their channels; the work is for {human_name} (what to remember, a draft they could send), never a message to the person.
   - *Occasion:* only a fresh one from the last two days that invites one small made thing.
<!-- /personal -->
3. **Weigh three, then choose.** Write down at least three concrete candidates, from different classes or reasons where the day allows: each one what you would make, its reason (`G-n`, `said`, `loop:<id>` or a date) and what you found when you looked. Look before ruling anything out: search the web, read the mail or the Canvas assignment, open the hub. A guess about what you would find is not a reason to drop a candidate. Skip anything in the explore ledger (`{agent_dir}/Explore/`) from the last three days unless new evidence arrived. Choose the one where two hours most plausibly produces something {human_name} would be glad of, work or not; an organisation's candidates serve its initiatives and assigned work. Before choosing its form, read `brain reception summary`: a kind of offer that keeps coming back ignored or declined (letters nobody opens, drafts nobody uses) is not made again until something changes. When all three lose to that bar, the answer is nothing, and the three are the record of why. While `brain ideas` says {human_name} is a new owner, the goal still comes first: in their first weeks one useful thing made or found for them, held, shows what you can do better than any offer, so prefer it to nothing. Still one thing made. A candidate that lost only because it needs {human_name}'s yes (a decision, access, money, or work too large to make unasked), with real evidence behind it, is not dropped. A decision on work already under way (ship a ready fix, a choice an open task is waiting on) goes on that task as its `waiting_for_owner` Ask, or `brain needs-you --ask`, never as an idea; new help they would have to agree to is filed as one idea, `brain ideas --add "<the offer, at most 16 words>" --source <the note it came from> --why "<what you found>" [--needs "<what it takes>"] [--goal G-n]`, at most one a run. When `brain ideas` refuses, let it go.
4. **Do the work.** A new unasked task for a person uses `brain task new "<title>" --source <note> --goal G-n`, or `--source` quoting their words when it is funded by what they said; work funded by a loop or a date is held as a draft, not made a task. Every held draft for a person carries its reason in its frontmatter: `goal: G-n`, or `for: "<their words>"`, `for: loop:<id>` or `for: <date>`. An organisation follows its initiatives and assigned duties.
5. **File it: prepared and held by default.** A fact goes through the surprise gate into the owning hub, profile or task with its source and date. A thing made or drafted goes beside the task it serves, or, when no task owns it, under `{agent_dir}/Prepared/YYYY-MM-DD <title>.md` with `kind: draft`, `tier: warm`, its `goal:` or `for:`, an `index_line` saying what it is for, and a wikilink to it from the hub, profile or task it serves, so retrieval and prefetch find it when the subject comes up. Primed and the next briefing name it once; a conversation on its subject may offer it and settles it with `brain reception set "<its note>" used` or `declined:<why>`.
6. **Message {human_name} only if** it is time-sensitive (a decision they can make now, a problem found, a result they asked about) or the occasion is today and gone tomorrow. Otherwise it waits for the morning or for the subject to come up. At most two messages a day from this routine.
7. **Gestures are rarer still.** A made thing delivered unasked, rather than held, passes three tests first: a fresh occasion within two days; a form unlike every gesture in the last two months of the explore ledger (not another page because the last one was a page); and nothing else from this routine has gone out today. Never more than one in a week.
8. **Record.** Overwrite `$AGENT_HOME/records/explore/state.json` with the thread in progress and `weighed`: the three or more candidates, each `{"what", "reason", "found", "lost_because"}` (`lost_because` empty on the one chosen). Write no ledger row yourself: the engine records one row for this run from the transcript when it ends (`{agent_dir}/Explore/YYYY-MM.md`), so before your final response write the choice as one working sentence, naming the reason and the class (`work`, `people` or `occasion`) and, for nothing, the three candidates and why each lost. End `[SILENT]` unless step 6 or 7 sent something. Every thing made or found that will reach {human_name} (held and listed in Primed, messaged, or delivered) is also one reception row, `brain reception add --kind draft|message|gesture|work --what "<what it is>" --source <its note> [--portrait <id>]`, the portrait id being the After line that names its goal, so how it lands is recorded and teaches the next choice.

{owner_messages}
## Boundaries
{human_name}'s channels are a source of candidates, never a report: no digest of their inbox, no "here is what came in", nothing copied into the vault beyond a surprise-gated fact with a pointer; what a channel yields is a thing done or held, judged by whether they would be glad of it. Reading is what the registry row allows; writing in their name is outward action and waits for Standing state. No sends to anyone but {human_name} (answering whoever wrote to you is watch's); a thing prepared for a friend's visit is for {human_name}, not for the friend, unless Standing state in `{agent_dir}/Tasks.md` records {human_name}'s permission for that person. No spending, sign-ups, new accounts or credentials. No edits to `{sources_dir}/`, SOUL, skills or the Memory Spec. Do not re-ask a question {human_name} has not answered. A goal is a reason to prepare, never to remind, schedule or raise it uninvited.
