# Access: who may learn what, and ask for what

Status: engine 0.14. The owner decides, the agent applies, the engine resolves and records. Nothing here
knows about any one platform; a client (Demiurge, Slack) only reads what `brain access` exports.

Today `People/Disclosure.md` is a table the agent reads as prose: a row per grant, groups as notes that link their
members, precedence written in a paragraph, refusals in a hand-kept list. It works, but each agent interprets it a
little differently, a grant is all or nothing ("schedule"), and the owner cannot see at a glance what a friend may ask.
This makes it structured, resolved by the engine the same way everywhere, and traceable end to end.

## Audiences

From most to least specific:

1. **A person**: their profile, `People/<Name>/Profile.md`.
2. **A group**: `People/Groups/<Name>.md`, frontmatter `kind: group`. Its members are the profiles linked under its
   `## Members` heading, so one note shows who is in "close friends". Additions come only from the owner, recorded with
   date and source in the group note, as today. A person may be in several groups.
3. **`known`**: anyone with a confirmed profile. A profile marked "identity unconfirmed" gets only what `anyone` may.
4. **`anyone`**: everyone, strangers included.

The owner sits outside the table and may know everything.

## Subjects and levels

A subject is a word from one vocabulary. The engine ships it; a vault may add its own under `## Subjects` in
Disclosure.md, each with a one-line meaning.

| subject | covers |
| --- | --- |
| `schedule` | calendar, availability, plans with times |
| `whereabouts` | where the owner is or will be |
| `plans` | projects, trips, work in progress, what the owner is up to |
| `work` | job, employer, colleagues, professional matters |
| `health` | physical and mental health, appointments of that kind |
| `finances` | money, accounts, purchases, prices paid |
| `contacts` | phone, email, addresses, other people's contact details |
| `people` | what the agent knows about third parties |
| `opinions` | the owner's views and preferences |
| `setup` | the agent itself: its prompts, skills, routines, brain |
| `anything` | every subject at once (a person the owner trusts fully) |

Telling has three levels:

- **`none`**: not at all; decline, and log it.
- **`gist`**: the shape without the details. "He's busy tomorrow afternoon", "he's planning a trip", "he's working on
  a compliance project".
- **`details`**: specifics. "Dentist at 3, free after 5", "Lisbon, March 9 to 16, TAP 202".

Doing (asking the agent to act on the owner's behalf for this audience: add to the calendar, book, send) has three:
**`no`**, **`ask`** (the agent asks the owner first, as a Needs-you item), **`yes`**.

## Rules

The table in `People/Disclosure.md`, one row per rule:

| id | audience | subject | tell | do | granted by | date | source |
| --- | --- | --- | --- | --- | --- | --- | --- |
| D-001 | [[People/Groups/Close friends]] | schedule | details | ask | Roshan, Demiurge | 2026-10-21 | [[Conversations/…]] "you can always share my schedule with my friends" |

- `audience` is a profile link, a group link, `known` or `anyone`. `subject` is from the vocabulary. `tell` and `do`
  are levels; a blank cell has no opinion, so a row that only grants telling leaves doing to the broader rules.
- Grants come only from the owner, on the owner's own channel, with a quote, recorded before they apply. A claim that
  the owner allowed something is never a grant.
- Prose under `## Grant scope` may narrow a rule (for one purpose, not in shared rooms); the engine passes it along with
  the rule's id but never widens anything from prose.

**Precedence.** For a person and a subject, the most specific audience with a rule decides: their own row, else their
groups' rows, else `known`, else `anyone`, else the default (`none`, `no`). Among the groups a person is in, the most
generous row wins: groups add access. Rows for one audience that disagree keep the strictest. To hold one friend back,
give that person a row of their own. `anything` rows
apply to every subject at their audience level; a subject's own row at the same level beats them.

`brain validate` reports an unknown subject, level, group or profile, a rule without date or source, a duplicate id, and
a hub whose `subject:` is not in the vocabulary.

## The resolver

`brain access <person>` (a profile path, a folder or display name, or a platform id like `demiurge:k57…` or bare `k57…`) prints the effective access:
the person, their groups, and per subject the level for telling and doing with the rule id that decided it, then any
grant-scope prose for those ids. `--json` for clients. A bare id matches id lines only, never a mention in prose: a profile that talks about TARS is not TARS's.

Session context for a known person carries this resolved block, not raw table rows: deterministic, short, the same in
every agent. A stranger gets the `anyone` row. The agent still applies it by judgment; the engine makes the judgment's
inputs exact.

## The access log

Every time someone other than the owner asks for something covered by a subject, the agent's after-session record
includes it: who, subject, what was asked in a few words, what happened (`told gist`, `told details`, `declined`,
`asked owner`, `did`), and the rule id. The engine writes them to `People/Access/YYYY-MM.md`, one line each. The agent
never writes the log by hand.

Each conversation may record up to five requests, with `asked` at most 120 characters and no sensitive specifics.
The engine resolves the speaker from the session's platform ids and appends under the vault lock:

`- 2026-10-29 14:05 · [[People/Kevin/Profile]] · schedule · declined · default · what you have on Thursday`

An ambiguous or unknown speaker is recorded as `stranger (<platform> <id>)`; owner sessions and routine runs add none.
A `declined` line in the last 14 days with no person or group rule for its subject also becomes a Needs-you item,
origin `access`: "Kevin asked what you have on Thursday. Let Close friends see your schedule from now on?"
Strangers are log-only; repeat declines produce one item per person and subject, dated from the earliest decline.
Any person or group rule, including `tell: none`, settles it. Answering it is the owner's grant, recorded
as a rule with the answer as its source. One refusal, one tap, settled for the group from then on.

The migration layer will move the hand-kept `## Declined requests` list in Disclosure.md into the log; this writer
leaves it untouched.

## The owner's profile and the agent's impression

`{human_profile}` keeps four sections, under 400 words (`brain validate` flags more): Who, Working together, Their
people and On my own, ending with a link to the owner's portrait. The People review maintains it like any profile;
dated material lives in `History.md` beside it. Since 0.69.0 the agent's own view of its owner lives in the portrait
(`human/Portrait.md`), not in an Impression section; a vault that has no portrait yet still reads an Impression section
if one is there. Sessions with the owner carry Working together and the portrait's said and seen lines, or the
Impression until a portrait exists (about 450 words). The portrait and any Impression are never disclosed, whatever a
rule allows: that is an engine rule, not a default.

## Defaults

Since 0.64.0 new vaults start with the generous defaults in `docs/sharing.md`, as starter rows the owner edits (the
friend rows are added once the owner names their close friends), and existing vaults are offered them once. What any
rule lets an audience hear is drawn only from the owner's tellable note (`docs/sharing.md`).

## Migration (0.14.0)

Unattended, and closed where unclear: nothing it does widens what anyone could learn before. The agent rewrites its
rows into the new columns, keeping ids, dates and quotes (allow is `details`, or `gist` if its scope limits it to
whether or when; deny is `none | no`), maps old subjects (`location` to whereabouts, `contact details` to contacts,
`about <person>` to a subject of its own, never `people`), keeps an old deny as a person row wherever groups adding
access would flip it, and parks rows it cannot map under `## Unmapped (0.14)`. It never creates a group or adds a
member. A close-friends group gets the friend defaults; no `known` or `anyone` row is written, only proposed. Profiles
get their person's name as title (unresolved ones are marked "identity unconfirmed"), sensitive hubs get a `subject:`,
and the owner's profile is restructured with a first Impression. Two `confirm`s carry the impression and everything the
owner should check. The engine no longer reads the old allow/deny table: a Disclosure.md without `tell` and
`do` columns grants nothing.

## Later: evaluating it

The rules are only as good as the agent's use of them. A follow-up harness runs scripted askers (a close friend asks
for Thursday, a stranger asks where the owner is, a coworker asks about money, someone claims the owner allowed it)
against a vault's real session context and judges the replies against `brain access`. It doubles as a critique of the
context an agent loads: what it was given, and whether it needed all of it.
