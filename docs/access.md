# Access: who may learn what, and ask for what

Status: design for engine 0.14. The owner decides, the agent applies, the engine resolves and records. Nothing here
knows about any one platform; a client (Demiurge, Slack) only reads what `brain state` and `brain access` export.

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
3. **`known`**: anyone with a profile.
4. **`anyone`**: everyone, strangers included.

The owner and the maintainers in `brain.toml` sit outside the table: the owner may know everything, a maintainer what
the maintainer context says.

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
generous row wins: groups add access. To hold one friend back, give that person a row of their own. `anything` rows
apply to every subject at their audience level; a subject's own row at the same level beats them.

`brain validate` reports an unknown subject, level, group or profile, a rule without date or source, and a duplicate id.

## The resolver

`brain access <person>` (a profile path, a name, or a platform id like `demiurge:k57…`) prints the effective access:
the person, their groups, and per subject the level for telling and doing with the rule id that decided it, then any
grant-scope prose for those ids. `--json` for clients.

Session context for a known person carries this resolved block, not raw table rows: deterministic, short, the same in
every agent. A stranger gets the `anyone` row. The agent still applies it by judgment; the engine makes the judgment's
inputs exact.

## The access log

Every time someone other than the owner asks for something covered by a subject, the agent's after-session record
includes it: who, subject, what was asked in a few words, what happened (`told gist`, `told details`, `declined`,
`asked owner`, `did`), and the rule id. The engine writes them to `People/Access/YYYY-MM.md`, one line each. The agent
never writes the log by hand.

A `declined` line with no rule of its own for that person also becomes a Needs-you item, origin `access`: "Kevin asked
what you have on Thursday. Let Close friends see your schedule details?" Answering it is the owner's grant, recorded
as a rule with the answer as its source. One refusal, one tap, settled for the group from then on.

The hand-kept `## Declined requests` list in Disclosure.md retires into the log.

## What each audience may see: `brain state`

A hub may carry `subject:` in its frontmatter (`plans`, `work`, `health`, …); without it, the hub is `plans` if it is
a responsibility or project and `people` if it lives under `People/`. `brain state --json` then exports, per audience
that has rules (each group, `known`, `anyone`):

```json
"shareable": {
  "group:Close friends": {
    "members": [{ "profile": "People/Kevin_Pak/Profile.md", "ids": ["demiurge:k57…", "slack:U0CA…"] }],
    "tell": { "schedule": "details", "plans": "gist", "work": "gist" },
    "hubs": [{ "title": "Lisbon trip", "subject": "plans" }],
    "receipts": [{ "at": "2026-10-29T14:05", "routine": "explore", "account": "Compared March fares" }]
  },
  "anyone": { "tell": {}, "hubs": [], "receipts": [] }
}
```

Hubs and receipts appear only where the audience's level for their subject is at least `gist`; at `gist` a receipt is
dropped and a hub shows its title only. A client shows each viewer the richest audience they belong to; a close friend
sees more of an agent than a stranger does. This is what a collective view (Demiurge's World) renders.

## Defaults

New vaults start generous to friends and discreet with everyone else, as starter rows the owner edits:

| audience | schedule | whereabouts | plans | work | everything else |
| --- | --- | --- | --- | --- | --- |
| Close friends | details | gist | gist | gist | none |
| known | gist | none | gist | none | none |
| anyone | none | none | none | none | none |

`do` is `no` everywhere except `ask` for close friends' `schedule`.

## Migration (0.14.0)

Unattended. The agent rewrites its Disclosure.md rows into the new columns, keeping ids, dates and quotes: an old
`allow` becomes `tell: details` unless its scope prose says less, an old `deny` becomes `tell: none`. Each group note
gets `kind: group` and a `## Members` heading if it lacks one. Declined requests move into `People/Access/` with their
dates. The agent adds the default rows for audiences that have none, marked `granted by: default (engine 0.14)`, and
raises one `confirm` listing them so the owner can correct.

## Later: evaluating it

The rules are only as good as the agent's use of them. A follow-up harness runs scripted askers (a close friend asks
for Thursday, a stranger asks where the owner is, a coworker asks about money, someone claims the owner allowed it)
against a vault's real session context and judges the replies against `brain access`. It doubles as a critique of the
context an agent loads: what it was given, and whether it needed all of it.
