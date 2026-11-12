---
name: {prefix}prime
description: Write today for the owner, after the night and the People review
version: 1.7.0
metadata:
  runtime:
    tags: [memory, night]
    category: brain
    editions: [personal]
---
# Prime

One job: overwrite `{agent_dir}/Primed.md` with what today is for {human_name}. It loads in every session today, so it is short, true and in order of importance. Runs after the People review, so the people in it are current.

Owner work found in the channels becomes a yours task: `brain task new "<title>" --state yours --source "<the request and who made it>" --why "<who is waiting and why>" --delivers "<what counts as done>" --next "<their first concrete step>"`.

## Procedure
1. Read active goals (`brain goals list --json`), CURRENT, the open tasks, Upcoming in the session context, yesterday's digest in `Conversations/Digests/`, and the People histories touched yesterday. If `{runtime_dir}/pending-migration.md` exists, its first line is the first line of Primed: "engine migration pending; see the file."
2. **What is new on the channels.** Note this run's start as an ISO datetime in the owner's zone (`brain timezone`). Read `last_run` from `$AGENT_HOME/records/prime/state.json`; on a first run, use yesterday at the same hour. Read watch's handled ids in `$AGENT_HOME/records/watch/state.json` and the owning tasks first. Consider only rows since `last_run` that watch has not handled, from each live row of the registry, `{agent_dir}/Channels.md`, whose `read` is not none, and see what is new on it since `last_run`, except Home and the shared rooms, opened the way its `reach` says; never a channel the registry does not hold, never more than its `read` allows. A row kept as synced files (`$AGENT_HOME/channels/<account>/`) is read with one command for all of them: `brain channels mail --since <that datetime>` lists mail from people one by one and notices (receipts, alerts, reminders, bookings) one line per sender with a count, each marked owner, own or shared; the owner's calendar is already in the session context. A row opened by a command, a browser profile, a remote machine or a room is checked the way it says, for what changed since `last_run` only: its newest items, not a search over everything. Keep what suggests {human_name} needs it today or tomorrow: a person writing to them or waiting on them, a payment, return or deadline due, a booking or travel change, a security alert, something that failed, a result they were waiting for. A sender or source with many items (build or repository notifications) is one item at most. What {human_name} said they want in Today, or don't (Working together in their profile: "I don't care about Sentry emails"), decides first: a sender, channel or kind they set aside is skipped even when it matches. Reconcile only important, actionable facts not already handled by watch into the owning task: status, deadline, waiting-on, next step, with a source path. Carry what bears on today into Primed's Today part, one line each, at most five, most pressing first. Primed always carries one coverage line, even when nothing was found: the channels checked and that nothing needs them ("checked your three inboxes, Slack and ESPN: nothing needs you"), and each live channel that could not be read, with why ("I can't see your mail: the sync last ran yesterday"). Pending and revoked rows are not connected and are not named. Once Primed and the compiled context are written successfully, save this run's start as `last_run` in the state file (create its parent directory if needed); a failed run keeps the previous checkpoint.
3. Write `{agent_dir}/Primed.md` (frontmatter `kind: primed`, `tier: hot`; yesterday's content is gone), at most 300 words, most important first, each part dropped when empty, nothing invented:
   - **Where and with whom.** Where {human_name} is today and who they are with, with the date it was said. "At home, nothing noted" is a valid line.
   - **Today.** What they said they would do today, anything due today or tomorrow that Upcoming cannot see (a commitment in prose with no date, a recurring thing), and at most five one-line facts from what is new on the channels that bear on today. Upcoming and its Calendar block carry the dated items; never copy those lists.
   - **People this week.** At most three, one line each with its source.
   - **Waiting for them.** Anything under `{agent_dir}/Prepared/` made in the last seven days that they have not raised, one line each, once. Leave out every draft whose `goal:` names a goal that is not active, checking `brain goals list --json` even if an earlier Primed listed it.
   - **Likely to come up.** The three notes most likely needed today, each with the note to load and its state in a sentence.
   - **Nudge.** At most one: a quiet open task reached from what is hot this week, not set aside, not `yours` (the evening asks about those), and not nudged since it last went quiet ("related to what you're on, untouched since <date>: still want this?"). Goal nudges and endings belong to people.

4. Nothing about the machinery (audits, counts, stages, versions, commits).
5. `brain compile-context --write`, record `last_run` as above, `brain commit -m "prime(<date>)" --push <the files you changed>`.

## Boundaries
Writes only Primed, the compiled context, the prime state file, important mail facts in their owning tasks, new yours tasks for owner work. Never hubs, profiles, loops or CURRENT. Never copy message bodies or contact anyone from this pass.
