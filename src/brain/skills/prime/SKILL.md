---
name: {prefix}prime
description: Write today for the owner, after the night and the People review
version: 1.2.0
metadata:
  runtime:
    tags: [memory, night]
    category: brain
    editions: [personal]
---
# Prime

One job: overwrite `{agent_dir}/Primed.md` with what today is for {human_name}. It loads in every session today, so it is short, true and in order of importance. Runs after the People review, so the people in it are current.

## Procedure
1. Read CURRENT, the open tasks, Upcoming in the session context, yesterday's digest in `Conversations/Digests/`, and the People histories touched yesterday. If `{runtime_dir}/pending-migration.md` exists, its first line is the first line of Primed: "engine migration pending; see the file."
2. **What is new on the channels.** Note this run's start as an ISO datetime in the owner's zone (`brain timezone`). Read `last_run` from `$AGENT_HOME/records/prime/state.json`; on a first run, use yesterday at the same hour. Then go through every live row of the registry, `{agent_dir}/Channels.md`, whose `read` is not none, and see what is new on it since `last_run`, opened the way its `reach` says; never a channel the registry does not hold, never more than its `read` allows. A row kept as synced files (`$AGENT_HOME/channels/<account>/`) is read with one command for all of them: `brain channels mail --since <that datetime>` lists mail from people one by one and notices (receipts, alerts, reminders, bookings) one line per sender with a count, each marked owner, own or shared; the owner's calendar is already in the session context. A row opened by a command, a browser profile, a remote machine or a room is checked the way it says, for what changed since `last_run` only: its newest items, not a search over everything. Keep what suggests {human_name} needs it today or tomorrow: a person writing to them or waiting on them, a payment, return or deadline due, a booking or travel change, a security alert, something that failed, a result they were waiting for. A sender or source with many items (build or repository notifications) is one item at most. What {human_name} said they want in Today, or don't (Working together in their profile: "I don't care about Sentry emails"), decides first: a sender, channel or kind they set aside is skipped even when it matches. Check later replies before treating a request as open. As watch does, reconcile only important, actionable facts into the owning task: status, deadline, waiting-on, next step, with a source path. Never copy message bodies into notes. Carry what bears on today into Primed's Today part, one line each, at most five, most pressing first. Primed always carries one coverage line, even when nothing was found: the channels checked and that nothing needs them ("checked your three inboxes, Slack and ESPN: nothing needs you"), and each live channel that could not be read, with why ("I can't see your mail: the sync last ran yesterday"). Pending and revoked rows are not connected and are not named. Once Primed and the compiled context are written successfully, save this run's start as `last_run` in the state file (create its parent directory if needed); a failed run keeps the previous checkpoint.
3. Write `{agent_dir}/Primed.md` (frontmatter `kind: primed`, `tier: hot`; yesterday's content is gone), at most 300 words, most important first, each part dropped when empty, nothing invented:
   - **Where and with whom.** Where {human_name} is today and who they are with, with the date it was said. "At home, nothing noted" is a valid line.
   - **Today.** What they said they would do today, anything due today or tomorrow that Upcoming cannot see (a commitment in prose with no date, a recurring thing), and at most five one-line facts from what is new on the channels that bear on today. Upcoming and its Calendar block carry the dated items; never copy those lists.
   - **People this week.** At most three, one line each with its source.
   - **Waiting for them.** Anything under `{agent_dir}/Prepared/` made in the last seven days that they have not raised, one line each, once.
   - **Likely to come up.** The three notes most likely needed today, each with the note to load and its state in a sentence.
   - **Nudge.** At most one: a quiet open task reached from what is hot this week, not set aside, not `yours` (the evening asks about those), and not nudged since it last went quiet ("related to what you're on, untouched since <date>: still want this?"); or a goal in `{human_goals}` with no dated evidence for sixty days, once ("you said <goal> on <date>; nothing since. Still a goal?"). A goal silent for thirty more days after its nudge moves under Set aside with today's date; that is the one edit to Goals this routine makes.
4. Nothing about the machinery: no audits, counts, stages, versions, commits or routine drift. Those are the Sleep Log's.
5. `brain compile-context --write`, record `last_run` as above, `brain commit -m "prime(<date>)" --push <the files you changed>`.

## Boundaries
Writes only Primed, the compiled context, the prime state file, important mail facts in their owning tasks, and a dormant goal's move to Set aside. Never hubs, profiles, Triggers or CURRENT. Never copy message bodies or contact anyone from this pass.
