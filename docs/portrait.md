# Portrait

*Draft, 2026-11-06; amended the same day, when the build began (section 9). How an intellect comes to know its owner well enough to work on their behalf, the way a good agent works for a client: always thinking about what it can do for them, and doing it from an understanding of who they are. Read with `docs/engine.md` (Goals, Needs you, Ideas, Follow-ons) and `docs/access.md` (the owner's profile and the impression).*


*Being reworked: `docs/image.md` moves the portrait's lines into facets (an image, one note per area of a life), fed by source modules through one evidence format, with `Portrait.md` compiled from them. What this document says about a line itself still holds.*

## 1. Why

An audit of Orphan's vault and of this engine, on November 6, found that the intellect knows facts about its owner accurately and does not know them.

- **Provenance works.** Notes keep what the owner said, what the agent suggested and what it inferred apart, with dates and sources. Explicit corrections become dated lines in Working together and change later behaviour.
- **Understanding is thin and scattered.** The owner's material is about 2% of the vault's files, spread over a profile, a History, Goals and half a dozen topic hubs. After months, the Impression holds one confirmed point, and the taste hub's list of observed invariants is empty. Under 100 lines of engine code (`owner_card`, `working_together`, `noted`) touch the owner at all; the rest is one paragraph of the People skill.
- **The bar forbids judgment.** An impression point needs a pattern seen twice, its sources, a confidence and a confirm, at most two a week. So the agent rarely forms a view, and a sports agent without a view of their client is a secretary.
- **Attention points at the machinery.** Explore's output is almost all infrastructure, product and work memos. The owner's personal threads have no trigger, watch item or prepared card.
- **Nothing learns from reception.** "Silence is unknown." Letters record that they were sent, never how they landed; three of five Library works were never opened; gestures for friends are "unverified". A `no` blocks the same text and teaches nothing else.
- **A new owner starts empty.** The profile is blank, and the only cold-start rule is a lower bar for ideas in the first 21 days.

The proposal is two notes and one rule change: a **portrait** that holds the agent's understanding as ranked, sourced claims, including guesses; a **reception ledger** that records how every offer landed; and permission to act cheaply on a guess in order to test it. A cold start becomes building the first portrait from what the owner has connected, mail first, and showing it to them. Sharing what the intellect learns with friends' intellects is a separate project, in `docs/sharing.md`.

## 2. The portrait

`<human_profile's folder>/Portrait.md`, `kind: orientation` like Goals, pinned, compiled into `.context.md` after CURRENT. One note, rewritten in place, never appended to. Older versions of a line move to `History.md` beside it, dated, with their id. The owner can always read and edit their own portrait: being correctable is the point, and in Demiurge it is a standing surface on the intellect's home, not only an item in Needs you.

Six sections, in this order. Order within a section is rank.

| Section | Holds |
| --- | --- |
| After | what the owner is trying to get or become, each with a horizon: `now` (weeks), `year`, `longer` |
| Cares about | what matters to them and what they would say yes to: values, standards, the trade-offs they make |
| Taste | what lands: food, books, music, places, humour, forms of help, the made things they used |
| How they decide | how they weigh options, what persuades them, how much they want laid out, what they leave to others |
| Energy | what they return to unprompted, what they put off, the hours and conditions they do their best in |
| Not | standing negatives: what they do not want, do not care about, or corrected the agent on (`not a runner`, `doesn't want reading turned into a curriculum`) |

Each line:

```md
## After
- Move to Lisbon within the year · said · high · 2026-10-30 · [[2026-10-12 Lisbon conversation]] ^3f2a
  - horizon: year
  - for: apartments, neighbourhoods, visas, remote roles
- Wants to start climbing · guess · low · 2026-11-02 · [[Fitness]] ^91c0
  - horizon: now
  - test: hold a beginner gym shortlist near the office; offer once
  - against: hasn't mentioned it since Oct 20
```

The fields are text, basis, confidence, last evidence date and source, separated by ` · `, then the line's id. Indented lines: `horizon:` (After only, required), `for:` (what attention should watch on this line's behalf; After only), `test:` (how to check a guess cheaply), `against:` (counterevidence, kept rather than dropped).

**Basis** is one of three:

- `said`: the owner stated it, or confirmed it when shown.
- `seen`: a pattern in what they did, at least twice, stated as behaviour (today's impression rule).
- `guess`: an inference from one signal or from their sources. Allowed, labelled, and tested before it is relied on.

**Confidence** is `high`, `medium` or `low`, and the agent changes it with evidence.

**Ids.** The agent writes a new claim without an id; the night assigns one (`^` and four hex digits) and writes it on the line. The id belongs to the claim, not its wording, so rewording or re-ranking a line keeps it, and reception rows and explore accounts that point at it stay valid. An id is never reused, including those retired to History. (The first draft hashed the section and text, which broke every pointer whenever a line was reworded.)

Caps: 700 words and at most eight lines per section, which exist for the context budget. There is no cap on the share of guesses: it would bring back the timidity the audit found, and a seeded portrait is all guesses. The labels, the tests and retirement keep guesses honest. `brain validate` reports malformed lines, an unknown basis, confidence or horizon, a section over its cap, a reused id, and a `guess` older than 30 days with no evidence since; those lines are retired by the weekly pass.

**Sensitive subjects.** Health, mood, relationships and other sensitive traits appear only with basis `said`, in the owner's framing, and never as a `guess` or `seen`. The People skill's existing rule against sensitive-trait inference stands. The portrait, like the impression, is never disclosed to anyone, whatever a rule allows.

### What moves into it

The portrait replaces the scattered homes rather than adding one:

- Profile keeps Who, Working together, Their people and On my own, the operational sections. What they're about and Impression move into the portrait; the Impression's points become `seen` lines.
- `Goals.md` folds into After. Active goals become `said` lines with their horizon; Aspirations become `longer`; Adopted practices go to Energy; Set aside moves to History. The goal rules carry over unchanged: a line needs a second day of evidence before it ranks above `low` confidence, is never raised uninvited as a reminder, and is moved out after ninety silent days.
- Topic hubs about the owner (interests, taste, learning, career) stay as hubs for detail. The portrait links them and holds only the conclusion.

`owner_card` sends Working together plus the portrait's top lines (After top three, then Cares about, Taste and Not) into every owner session, still capped at 450 words.

## 3. Guesses are for testing

A guess is useful only if the agent finds out whether it is right. Initiative's existing three tiers decide how far a guess can go:

| Tier | On a `guess` | On `seen` or `said` |
| --- | --- | --- |
| Prepare (free) | yes: the usual way to test one | yes |
| Mention (rate-limited) | yes, as an offer, counted against explore's two messages a day | yes |
| Act outward (recorded permission) | never | as today |

Explore takes candidates from the portrait first: the top After lines' `for:` lists, then guesses with a `test:`. Its account names the line it served (`portrait:<id>`), beside the class and disposition it already records. A guess is promoted to `seen` after two `used` or `liked` receptions, or to `said` when the owner confirms it. It is retired after a `declined` reception, an owner correction, or thirty days without evidence, and a retired guess the owner corrected becomes a `Not` line, so it is not guessed again.

The People skill's limit of two confirms a week stays for single points. The reveal (section 5) and a migration are exempt, each sent as one batched item.

## 4. The reception ledger

`<agent_dir>/Reception/YYYY-MM.md`, `kind: log`, one row per thing offered to the owner:

```md
2026-11-04 09:12 · letter · On small rooms · opened · [[Drafts/2026-11-04 On small rooms]] · portrait:3f2a
2026-11-04 15:40 · idea · Beginner climbing gyms near the office · declined:not-now · [[Prepared/2026-11-04 Gyms]] · portrait:91c0
2026-11-05 08:00 · brief · Parcel addressed to the wrong city · used · [[Primed]]
```

The fields are time, kind, what, outcome, source, and the portrait line it served when there is one. Kind is one of `message`, `brief`, `idea`, `draft`, `gesture`, `letter`, `work` or `ask`. Outcome is one of:

- `pending`: offered, nothing heard yet.
- `delivered`: seen to reach a surface that reports opens, nothing heard yet.
- `opened`: seen, with nothing further.
- `used`: replied to substantively, acted on, kept, reopened, or turned into a task.
- `liked`: an explicit positive.
- `declined:<reason>`: no, not-now or stop-asking, from the ideas and needs answers.
- `ignored`: not opened within seven days, on a surface that reports opens.
- `unknown`: delivered where opens cannot be seen (mail, iMessage), after seven days.

"Silence is unknown" stays true where the surface cannot see. Where it can, silence is a signal.

**Who writes it.** The engine writes a `pending` row for each idea (`brain ideas --add`) and each Needs you ask, and explore adds one with `brain reception add` for each thing it makes or finds that will reach the owner. Answering an idea or an ask settles its row: yes, or an ask resolved, is `used`; no, not-now or stop-asking is `declined:<answer>`. `brain reception set <id> <outcome>` sets any row. `brain nightly --write` settles rows with nothing heard for seven days: `delivered` to `ignored`, `pending` to `unknown`.

**Dormant: `delivered` and `opened`.** They are for a runtime that sees an offer reach a surface that reports opens, and Demiurge reports neither yet. Until it does, an unanswered row settles to `unknown`, never `ignored`, and the People pass's promotion of a guess after two `used` or `liked` offers fires only from answered ideas and asks, or rows the agent sets itself.

**Who reads it.** `brain reception summary [--days 30] [--json]` gives rates by kind and by portrait line: offered, used, ignored, declined. The People pass reads it daily for Taste and for promoting or retiring guesses. Explore reads it before choosing a form (no more letters if letters go unopened). The improve routine reads it weekly to propose changes to what the agent offers.

## 5. Cold start: the first portrait

A new owner should feel known on the first day. Quest gets this by reading everything at once, and so does this design. The difference is that everything read becomes a guess with a source, which the owner can correct line by line.

When the owner's first account connects (and again, more narrowly, for each later one), a **seed** pass runs once:

1. **Read broadly, keep little.** Mail is the main source: it holds most of a life's specifics. The pass reads twelve months of headers; the full bodies of every thread the owner wrote in; and all of the owner's transactional mail (bookings, receipts, confirmations, appointments, subscriptions, deliveries), which records where they go, what they use and what they keep. It also reads twelve months of calendar and whatever else the account's synced files hold. Raw content is not copied into the vault. Each conclusion keeps its source pointer, as everywhere else.
2. **Write the first portrait.** Every line is a `guess`, except what the owner has said in conversation, which is `said`. Up to the caps. Sensitive subjects only as the owner wrote about them, never inferred.
3. **The reveal.** One ask in Needs you, "Here's who I think you are", linking the portrait. In Demiurge it opens as a card where each line can be marked **right**, **wrong** or **not this**. Right becomes `said`. Wrong is retired, with the owner's wording kept as a `Not` line when they give one. Not this is removed, and its source's subject is added to `Not`, so the agent stops drawing on it.
4. **Then the usual loop.** Explore starts from the top After lines on the first day. The new-owner window's lower bar stays.

A connection made later runs the same pass scoped to that account, and adds or updates lines rather than rewriting the portrait. The 0.54.0 behaviour of a new connection asking once what the owner would like done with it stays as is.

### The person's seed, as built (0.65.0)

The pass reads what the account has synced: Demiurge keeps the last thirty days of mail and the calendar from a week back to a month ahead. The sync also writes a one-time twelve-month backfill of the owner's sent and Updates mail and past calendar, which the seed reads once and clears; the pass reads whatever window the files hold.

**The arithmetic is the engine's.** `brain portrait seed --account <address|all> [--json]` reads the owner accounts' synced files under `$AGENT_HOME/channels/<account>/` and prints a reading list in which every item already says whose it is (`docs/sharing.md`):

| kind | whose | how the engine tells |
| --- | --- | --- |
| `sent` | own | the `SENT` label, or a sender that is one of the owner's addresses |
| `transactional` | own | `CATEGORY_UPDATES` or an automated sender, addressed to the owner in `to`, not forwarded |
| `forwarded` | someone else's | a `Fwd:` or `Fw:` subject, or a forwarded-message block in the body |
| `cc` | someone else's | the owner in `cc` and not in `to` |
| `group` | someone else's | more than one other person in `to` and `cc` |
| `person` | someone else's | any other mail to the owner |
| `event` | own | a calendar event the owner accepted, or one with no attendees; declined and cancelled events are left out |

Promotions, social, forum and spam are skipped, as everywhere. Transactional mail is grouped by sender with counts, so a dentist's three reminders read as one relationship; recurring events are grouped by title. Each list is capped, newest first, with what was left out counted.

**The judgment is the seed skill's,** in its personal section. It writes the first portrait (every line a `guess` with a `test:`, except what the owner said in conversation, which is `said`; sensitive subjects only as the owner wrote about them), and it files into `Tellable.md` every fact the reading list marks as the owner's own and that fits a subject, with a `gist:` line. An item marked someone else's may inform a portrait guess, which only the owner reads, and never a tellable line. It records the run as a Seeds note, as the company seed does, so `brain portrait coverage` knows the account was read.

**The reveal is two items in Needs you, not one,** because they differ in what silence means:

- a **`confirm`**: "Here's who I think you are", linking the portrait and naming how many lines friends may now hear, with the most surprising guess quoted. Unanswered, it settles as accepted after five days like any confirm; that is safe, because the owner can correct the portrait at any time and the guesses are never told.
- an **`ask`**, only when the vault has no close-friends group: the people the intellect would treat as close friends, from `brain people friends`, which ranks confirmed profiles with a platform id by the evidence that the owner and their intellects already deal with each other (requests in the access log, a registered intellect whose owner is that person, Noted lines from them in the last sixty days). Silence never creates a group, since a group widens what its members hear; a yes is the owner's grant, recorded in the group note with their words as its source.

A connection made later runs the pass scoped to that account. The 0.65.0 migration runs it once for every owner account already synced.

## 6. Transparency and sharing

Signup says it plainly: the intellect reads your mail, your calendar and whatever else you connect, and it gets better the more it has. Everything connected is read, and nothing read is used for anything but this owner's intellect. What it may tell other people's intellects, experiences as a subject, the sharing defaults and asking around are a separate project, drafted in [`docs/sharing.md`](sharing.md); nothing in this design widens what is shared.

## 7. Commands

- `brain portrait [--json]` prints the portrait with line ids, basis, confidence and last evidence.
- `brain portrait --top N` prints the top After lines with their `for:` lists, for explore and prime.
- `brain portrait seed --reviews DIR` runs an organisation's seed arithmetic over merged pull requests' review comments (which to read, in what window, what repeats where) and prints the reading list; the judgment is in the `seed` skill. `brain portrait seed --plans DIR` does the same over the tracker's projects and finished issues, for After and Energy. `brain portrait seed --account <address|all> [--days N] [--json]` reads a person's synced owner mail and calendar, classifying whose each item is; without `--days` it reads everything synced.
- `brain reception add --kind K --what TEXT --source NOTE [--portrait ID] [--at TIME]`, `brain reception set <id> <outcome>`, `brain reception summary [--days N] [--json]`, `brain reception burden` (correction burden on the intellect's own pull requests).
- `brain state --json` gains `portrait` (lines with ids, no `against:` text) and `reception` (the last 30 days), with the Demiurge reveal card that reads them. Demiurge shows them to the owner only.

## 8. Skills

- **Image**: the nightly image pass maintains the portrait. It reads `brain reception summary` and the day's answered items, promotes, demotes and retires lines, and rewrites rather than appends.
- **Explore**: candidates start from `brain portrait --top 3` and testable guesses; the account names `portrait:<id>`; the form is chosen with reception rates in view.
- **Prime**: lists held drafts only while their goals are active; People owns goal nudges and endings.
- **Image, weekly**: the first run after Sunday re-ranks every facet. It re-ranks After, retires stale guesses, checks the caps, and reports **coverage**: which of the top three After lines got no attention that week.
- **Seed**: new, `editions: [personal]`, for the cold-start pass.

## 9. Releases

What shipped, each its own engine release:

1. **0.55.0, the note.** The format, `brain portrait [--top N] [--json]`, line ids stamped by the night, `brain validate`'s checks, and the portrait in `.context.md` after CURRENT.
2. **0.56.0, the company portrait.** The same note in an enterprise vault, in its context with the team's rule, and its stated and seen lines in the state export for the stage.
3. **0.57.0, seeding an organisation's Taste from corrections.** Taste from its review rules and the comments on its recent merged pull requests, with the evidence kept and one confirm to the operator.
4. **0.58.0, seeding an organisation's direction.** After from the tracker's projects and initiatives, ranked by horizon from their target dates; Energy from the issues finished on each, with the gap between where it says it is heading and where effort goes.
5. **0.59.0, learning the company.** The seed skill researches the organisation from everything public and everything connected, details into `Company/` hubs and conclusions into the portrait, with confidential material kept out of what the team sees; `brain portrait coverage` shows what each section holds and which sources are never read or stale.
6. **0.63.0, reception in the engine.** The ledger with row ids, rows written and settled by ideas and asks, the night's settling, `brain reception summary`, and correction burden on the intellect's own pull requests. The runtime does not report `delivered` or `opened` yet (section 4).
7. **0.64.0, the tellable note.** The owner's own facts, the only thing anyone else hears about them ([`docs/sharing.md`](sharing.md)).
8. **0.65.0 and 0.67.0, the person's seed.** The seed pass and the two-item reveal of section 5; 0.67.0 adds the sync's one-time twelve-month backfill, seeded by the daily people pass as each account's lands.
9. **0.69.0, rewiring beside goals.** Goals (0.66.0) became the only reason for unasked work, so the portrait does not hold them: an After line names its goal (`goal: G-n`) and adds horizon and where to help, and other After lines are guesses tested by asking. People keeps the portrait, explore records each offer, Sleep 2 reviews it weekly, and the owner card drops the Impression.
10. **0.75.0, the owner's answers.** `brain portrait stamp` and `brain portrait mark <id> right|wrong|not-this [--words TEXT]`, which Demiurge's portrait card sends; the person's portrait goes out only for them, as `ownerPortrait`, with their profile and portrait marked `audience: owner`.
11. **0.78.0, a new connection's seed.** `brain portrait seed --account <address> --hand-off` hands off one seed task for a newly connected account, and the state export's `seeds` says which accounts are read or being read.

The 0.69.0 migration (`migrations/0.69.0.md`), unattended, in the owner's existing vault:

- An organisation's intellect with no portrait gets one ask to learn the company, and skips the rest.
- Fold the profile's What they're about and Impression (or its own such sections) into `Portrait.md`: Impression points become `seen`, the owner's own statements `said`, the agent's inferences `guess` with a `test:`; sensitive subjects only as `said`. Existing lines and ids are kept.
- Link goals: one After line for each active goal, naming it, with a `horizon:` and a `for:`. No goal is restated any other way, and none is created.
- Rewrite the profile to Who, Working together, Their people and On my own, ending with a link to the portrait; dated detail goes to `History.md` beside it.
- Write reception rows backward from this and last month's ideas and asks in `Answered/`, then, unless one is already open, one portrait confirm in Needs you.

## 10. Measuring it

The point is a better agent, not a longer note. Three numbers, from `brain reception summary` and image's report, tracked weekly:

- **Hit rate:** used and liked over offered, by kind. Rising means the portrait is pointing attention at the right things.
- **Reveal accuracy:** right over marked, on the first portrait and on each later batch.
- **Coverage:** weeks in which every top-three After line received some attention.

`agent-brain-evals` gains cases for the rules: a guess is never acted on outwardly; a corrected guess becomes `Not` and is not proposed again; a sensitive subject is never inferred; a declined form is not repeated.

## 11. Open

- **Who sees the portrait.** Settled: the owner always reads and edits their own (section 2). The impression's "never disclosed" was about third parties, and still holds for them.
- **How much to read on seed.** Settled: full bodies of the threads the owner wrote in and all transactional mail, not headers first. A timid first portrait loses the first day, and reveal accuracy is the measure that says whether reading more is worth it.
- **Energy and sensitivity.** What drains someone is close to mood. The section is limited to behaviour and the owner's own words. Revisit after the first reveals.
- **Enterprise.** Settled by Demiurge's `docs/judgment.md` section 3: an organisation's intellect has one portrait, of the company (where it is heading, its standards, how it likes work done, how it decides), in the same note and format. It is the team's to read, guesses about direction excepted, which go to the operator as a confirm. Its first seed is Taste from the review corrections the company has already written down (judgment section 6, step 0).
