# Portrait

*Draft, 2026-11-06. How an intellect comes to know its owner well enough to work on their behalf, the way a good agent works for a client: always thinking about what it can do for them, and doing it from an understanding of who they are. Read with `docs/engine.md` (Goals, Needs you, Ideas, Follow-ons) and `docs/access.md` (the owner's profile and the impression).*

## 1. Why

An audit of Orphan's vault and of this engine, on November 6, found that the intellect knows facts about its owner accurately and does not know them.

- **Provenance works.** Notes keep what the owner said, what the agent suggested and what it inferred apart, with dates and sources. Explicit corrections become dated lines in Working together and change later behaviour.
- **Understanding is thin and scattered.** The owner's material is about 2% of the vault's files, spread over a profile, a History, Goals and half a dozen topic hubs. After months, the Impression holds one confirmed point, and the taste hub's list of observed invariants is empty. Under 100 lines of engine code (`owner_card`, `working_together`, `noted`) touch the owner at all; the rest is one paragraph of the People skill.
- **The bar forbids judgment.** An impression point needs a pattern seen twice, its sources, a confidence and a confirm, at most two a week. So the agent rarely forms a view, and a sports agent without a view of their client is a secretary.
- **Attention points at the machinery.** Explore's output is almost all infrastructure, product and work memos. The owner's personal threads have no trigger, watch item or prepared card.
- **Nothing learns from reception.** "Silence is unknown." Letters record that they were sent, never how they landed; three of five Library works were never opened; gestures for friends are "unverified". A `no` blocks the same text and teaches nothing else.
- **A new owner starts empty.** The profile is blank, and the only cold-start rule is a lower bar for ideas in the first 21 days.

The proposal is two notes and one rule change: a **portrait** that holds the agent's understanding as ranked, sourced claims, including guesses; a **reception ledger** that records how every offer landed; and permission to act cheaply on a guess in order to test it. A cold start becomes building the first portrait from what the owner has connected, mail first. Sharing widens with it: generous about the owner's own experiences and plans, closed about anyone else's.

## 2. The portrait

`<human_profile's folder>/Portrait.md`, `kind: portrait`, pinned, compiled into `.context.md` after CURRENT. One note, rewritten in place, never appended to. Older versions of a line move to `History.md` beside it, dated.

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
- Move to Lisbon within the year · said · high · 2026-10-30 · [[2026-10-12 Lisbon conversation]]
  - for: apartments, neighbourhoods, visas, remote roles
- Wants to start climbing · guess · low · 2026-11-02 · [[Fitness]]
  - test: hold a beginner gym shortlist near the office; offer once
  - against: hasn't mentioned it since Oct 20
```

The fields are text, basis, confidence, last evidence date and source, separated by ` · `. Optional indented lines: `for:` (what attention should watch on this line's behalf; After only), `test:` (how to check a guess cheaply), `against:` (counterevidence, kept rather than dropped).

**Basis** is one of three:

- `said`: the owner stated it, or confirmed it when shown.
- `seen`: a pattern in what they did, at least twice, stated as behaviour (today's impression rule).
- `guess`: an inference from one signal or from their sources. Allowed, labelled, and tested before it is relied on.

**Confidence** is `high`, `medium` or `low`, and the agent changes it with evidence. Each line's id is a hash of its section and text, stable across rank changes, so reception rows and explore accounts can point at it.

Caps: 700 words, at most eight lines per section, at most a third of all lines `guess`. `brain validate` reports malformed lines, an unknown basis or confidence, a section over its cap, and a `guess` older than 30 days with no evidence since; those lines are retired by the weekly pass.

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
- `opened`: seen, with nothing further.
- `used`: replied to substantively, acted on, kept, reopened, or turned into a task.
- `liked`: an explicit positive.
- `declined:<reason>`: no, not-now or stop-asking, from the ideas and needs answers.
- `ignored`: not opened within seven days, on a surface that reports opens.
- `unknown`: delivered where opens cannot be seen (mail, iMessage), after seven days.

"Silence is unknown" stays true where the surface cannot see. Where it can, silence is a signal.

**Who writes it.** The engine writes the `pending` row whenever an offer passes through it: `brain ideas --add`, `brain needs-you --ask`, a drafted or delivered explore output, the briefing's items, a letter sent. The runtime reports what it sees with `brain reception set <id> <outcome>`, as it already reports engagement with `brain continue --engaged`: Demiurge reports opens of works, Today cards and threads, and replies. `ideas.respond` and `needs.dismiss` write their answers through it. `brain nightly --write` settles `pending` rows older than seven days to `ignored` or `unknown`.

**Who reads it.** `brain reception --summary [--days 30] [--json]` gives rates by kind and by portrait line: offered, used, ignored, declined. The People pass reads it daily for Taste and for promoting or retiring guesses. Explore reads it before choosing a form (no more letters if letters go unopened). The improve routine reads it weekly to propose changes to what the agent offers.

## 5. Cold start: the first portrait

A new owner should feel known on the first day. Quest gets this by reading everything at once, and so does this design. The difference is that everything read becomes a guess with a source, which the owner can correct line by line.

When the owner's first account connects (and again, more narrowly, for each later one), a **seed** pass runs once:

1. **Read broadly, keep little.** Mail is the main source: it holds most of a life's specifics. The pass reads twelve months of headers; the bodies of threads the owner wrote in at least twice; and the owner's transactional mail (bookings, receipts, confirmations, appointments, subscriptions, deliveries), which records where they go, what they use and what they keep. It also reads twelve months of calendar and whatever else the account's synced files hold. Raw content is not copied into the vault. Each conclusion keeps its source pointer, as everywhere else, and is filed as an experience where it is one (section 6).
2. **Write the first portrait.** Every line is a `guess`, except what the owner has said in conversation, which is `said`. Up to the caps. Sensitive subjects only as the owner wrote about them, never inferred.
3. **The reveal.** One ask in Needs you, "Here's who I think you are", linking the portrait. In Demiurge it opens as a card where each line can be marked **right**, **wrong** or **not this**. Right becomes `said`. Wrong is retired, with the owner's wording kept as a `Not` line when they give one. Not this is removed, and its source's subject is added to `Not`, so the agent stops drawing on it.
4. **Then the usual loop.** Explore starts from the top After lines on the first day. The new-owner window's lower bar stays.

A connection made later runs the same pass scoped to that account, and adds or updates lines rather than rewriting the portrait. The 0.54.0 behaviour of a new connection asking once what the owner would like done with it stays as is.

## 6. Transparency and sharing

This only works if the intellect reads the owner's life, and that is stated plainly at signup: it reads your mail, your calendar and whatever else you connect, and it gets better the more it has. Someone who is not comfortable with that is not the person it is for. Two things are kept apart, because they are different promises:

- **Intake is radical.** Everything connected is read. Nothing read is used for anything but this owner's intellect.
- **Sharing is generous about the owner and closed about everyone else.** An owner's intellect tells their friends' intellects a lot by default, but only what is the owner's own to tell.

### Whose information it is

The owner's mail is not only about the owner. A friend's news, a sibling's trouble, a colleague's salary all arrive in it, from people who never agreed to anything. So every fact the engine files is the owner's or someone else's, and that comes from its source, which the vault already keeps:

| The owner's own | Someone else's |
| --- | --- |
| what the owner said, in any conversation | what someone else wrote to the owner |
| mail the owner sent | a fact about a third party, however it was learned |
| transactional mail addressed to the owner: bookings, receipts, confirmations, appointments | what another intellect told this one |
| calendar events the owner created or accepted | `Noted` lines filed as "X said:" |

**Someone else's information is never told to anyone but the owner, whatever a rule says.** That is an engine rule, like the impression's, not a default an owner can widen. The person it belongs to can share it through their own intellect.

### A new subject: experiences

`experiences`: places, services, products and people for hire the owner has used, and how it went. The dentist, the restaurants, the hotel in Lisbon, the contractor who never called back, the headphones they returned. It is mostly drawn from transactional mail and is what friends ask about most. Prices paid stay under `finances`.

### Defaults

New vaults start with these rows, replacing the discreet defaults in `docs/access.md`:

| audience | experiences | opinions | plans | schedule | whereabouts | work | health, finances | people |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Close friends | details | details | details | details | gist | gist | none | none |
| known | details | gist | gist | gist | none | none | none | none |
| anyone | gist | none | none | none | none | none | none | none |

`do` stays `no` everywhere except `ask` for close friends' `schedule` and `plans`. `health` and `finances` are widened only by the owner, per person or group, as today. `people` covers what the owner said about others and stays `none`; what others said is never told at all.

The migration does not widen an existing vault's rules on its own. It proposes these defaults as one `ask`, in the reveal for an owner who already has a vault.

### Asking around

The point of wider defaults is that an intellect can ask its owner's friends' intellects and get a useful answer: "know a good dentist?", "anyone been to this place?", "who's free Saturday?". Each answering intellect replies from its own owner's experiences and opinions, within its rules, and says whose experience it is. The answers come back to the owner as one brief. An introduction (two people put in touch because their intellects saw a match) always needs both owners' yes.

The access log already records each request and what was told, so the owner can see what their intellect said about them, and to whom.

## 7. Commands

- `brain portrait [--json]` prints the portrait with line ids, basis, confidence and last evidence.
- `brain portrait --top N` prints the top After lines with their `for:` lists, for explore and prime.
- `brain portrait seed --account <reach>` runs the seed pass's arithmetic (which files to read, in what window) and prints the reading list; the judgment is in the `seed` skill.
- `brain reception set <id> <outcome>`, `brain reception --summary`.
- `brain state --json` gains `portrait` (lines with ids, no `against:` text) and `reception` (the last 30 days).

## 8. Skills

- **People**: step 5 maintains the portrait instead of the profile's last two sections. It reads `brain reception --summary` and the day's answered items, promotes, demotes and retires lines, and rewrites rather than appends.
- **Explore**: candidates start from `brain portrait --top 3` and testable guesses; the account names `portrait:<id>`; the form is chosen with reception rates in view.
- **Prime**: Today's single nudge may come from a top After line.
- **Sleep 2**: a weekly portrait pass. It re-ranks After, retires stale guesses, checks the caps, and reports **coverage**: which of the top three After lines got no attention that week.
- **Seed**: new, `editions: [personal]`, for the cold-start pass.

## 9. Migration (0.55.0)

Unattended, in the owner's existing vault:

- Build `Portrait.md` from the profile's What they're about and Impression, `Goals.md`, and the owner-topic hubs. Impression points become `seen`; goals become `said` with horizons; anything the agent inferred from the hubs becomes `guess`.
- Remove the two sections from the profile and leave a link to the portrait. Leave `Goals.md` as a stub pointing to After for one release, then retire it.
- Write reception rows backward from `Answered/`, the letter log and the Library's open records, where the outcome is known, so the summary starts with history.
- One batched `confirm`: the portrait as built, with the guesses marked, for the owner to mark right or wrong. It is the reveal for an owner who already has a vault.

## 10. Measuring it

The point is a better agent, not a longer note. Three numbers, from `brain reception --summary` and Sleep 2's report, tracked weekly:

- **Hit rate:** used and liked over offered, by kind. Rising means the portrait is pointing attention at the right things.
- **Reveal accuracy:** right over marked, on the first portrait and on each later batch.
- **Coverage:** weeks in which every top-three After line received some attention.

`agent-brain-evals` gains cases for the rules: a guess is never acted on outwardly; a corrected guess becomes `Not` and is not proposed again; a sensitive subject is never inferred; a declined form is not repeated.

## 11. Open

- **Who sees the portrait.** Showing the owner their own portrait is the correctability promise and the reveal's whole point. Showing the impression has been off-limits so far ("never disclosed"), which was about third parties; this design assumes the owner may always read and edit their own.
- **How much to read on seed.** Twelve months of mail is a cost question more than a privacy one, given the stance in section 6. Start with headers, threads the owner wrote in and transactional mail, and measure reveal accuracy before reading more.
- **Classifying whose a fact is.** Mail the owner was cc'd on, group threads and forwarded mail sit between the two columns. Default to someone else's until a rule says otherwise.
- **Energy and sensitivity.** What drains someone is close to mood. The section is limited to behaviour and the owner's own words. Revisit after the first reveals.
- **Enterprise.** The portrait is personal-edition only. An organisation's intellect knows its members through their profiles, as today.
