# Sharing

*Draft, 2026-11-06; the first slice built in 0.64.0. Split out of `docs/portrait.md`, where it was section 6. Read with `docs/access.md` and Demiurge's `docs/network.md`.*

## Transparency and sharing

This only works if the intellect reads the owner's life, and that is stated plainly at signup: it reads your mail, your calendar and whatever else you connect, and it gets better the more it has. Someone who is not comfortable with that is not the person it is for. Two things are kept apart, because they are different promises:

- **Intake is radical.** Everything connected is read. Nothing read is used for anything but this owner's intellect.
- **Sharing is generous about the owner and closed about everyone else.** An owner's intellect tells their friends' intellects a lot by default, but only what is the owner's own to tell.

The two halves are built differently. Generosity is a matter of defaults, which the owner can narrow. Closedness about everyone else is a property of the code: another person's session never sees anything that is not the owner's own.

## Whose information it is

The owner's mail is not only about the owner. A friend's news, a sibling's trouble, a colleague's salary all arrive in it, from people who never agreed to anything. So every fact is the owner's or someone else's, and that comes from its source:

| The owner's own | Someone else's |
| --- | --- |
| what the owner said, in any conversation | what someone else wrote to the owner |
| mail the owner sent | a fact about a third party, however it was learned |
| transactional mail addressed to the owner: bookings, receipts, confirmations, appointments | what another intellect told this one |
| calendar events the owner created or accepted | `Noted` lines filed as "X said:" |

Mail the owner was cc'd on, group threads and forwarded mail count as someone else's, unless the fact is about the owner and the owner wrote it. A fact the owner said about someone else ("Faisal is getting divorced") is a fact about a third party, and someone else's.

**Someone else's information is never told to anyone but the owner, whatever a rule says.** That is an engine rule, not a default an owner can widen. The person it belongs to can share it through their own intellect.

## The tellable note

What the intellect may tell anyone about its owner lives in one note, `Tellable.md` beside the owner's profile and portrait. It holds the owner's own facts, filed by subject, each with where it came from. A fact that is not in it is not told, to anyone. That makes the second promise hold by construction: what is not the owner's own never gets in, so it cannot get out.

The note is filled broadly, not curated. Every fact the night files that is the owner's own and fits a subject also gets a line here, so the note grows as fast as the vault does; which of it reaches whom is decided by the disclosure rules, not by what was put in.

```md
## experiences
- Has been seeing Dr. Lee, a dentist on Mission St, since 2024 and likes her · said · 2026-10-30 · [[Conversations/2026-10-30 Noted]]
  - gist: has a dentist they like in the Mission
- Stayed at the Memmo Alfama in Lisbon in March; would go back · transactional · 2026-03-16 · mail: Memmo booking confirmation

## schedule
- Climbing at Mission Cliffs most Tuesday evenings · calendar · 2026-11-04 · calendar: Climbing (weekly)
  - gist: usually busy Tuesday evenings
```

- **Sections** are subjects from the vocabulary (`docs/access.md`), lower case. `people` and `anything` are not sections: the first is someone else's by definition, and the second is not a subject of facts. `setup` is not one either.
- **A line** is text, then the kind of source, then the date of the evidence, then the source, separated by ` · `. The kind is one of `said` (the owner said it), `sent` (mail the owner sent), `transactional` (mail addressed to the owner by a business) or `calendar` (an event the owner created or accepted). There is no kind for someone else's information, so it cannot be filed.
- **`gist:`**, indented under a line, is the shape without the specifics, written when the line is filed. At `gist` level only gist forms are told, and a line without one is not told at all.
- **Health and finances** are filed like anything else; the defaults tell them to nobody, and the owner widens them per person or group.
- The portrait and the Impression stay out of this note and are never disclosed. A taste the owner stated may appear in both, as a `said` line here and as a portrait line; the portrait's ranking, guesses and basis never do.

`brain validate` reports an unknown section, a malformed line, a kind outside the four, a bad date, and a `gist:` with no line above it.

## What another session sees

`brain tellable <person>` (the same person forms as `brain access`, or `known`, or `anyone`) resolves that person's access and prints the lines of each subject they may hear: at `details` the lines themselves, at `gist` only their gist forms. `--json` for clients.

A session with anyone but the owner, a maintainer or the agent's own routines gets that block in its session context, after the access lines, under one fixed heading:

`What you may tell <name> about <owner>, and nothing else:`

The runtime keys on that heading. A session that carries it is answered from the session context alone: it gets no compiled context (no CURRENT, portrait, Primed or Upcoming), no prefetched vault notes, and no tools that read the vault (`read_file`, `search_files`, `terminal`, `write_file`, `patch`). It keeps the tools that reach the owner, so it can still ask them anything it cannot answer. What it learns in the conversation is filed by the night as usual. Acting for someone, which a `do` level of `ask` or `yes` allows, goes through the owner in this slice.

The block is capped at 600 words, newest evidence first within each subject. With nothing tellable, it says so, and the agent declines or asks the owner.

## A new subject: experiences

`experiences`: places, services, products and people for hire the owner has used, and how it went. The dentist, the restaurants, the hotel in Lisbon, the contractor who never called back, the headphones they returned. It is mostly drawn from transactional mail and is what friends ask about most. Prices paid stay under `finances`.

## Defaults

New vaults start with these rows, replacing the discreet defaults in `docs/access.md`:

| audience | experiences | opinions | plans | schedule | whereabouts | work | health, finances | people |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Close friends | details | details | details | details | gist | gist | none | none |
| known | details | gist | gist | gist | none | none | none | none |
| anyone | gist | none | none | none | none | none | none | none |

`do` stays `no` everywhere except `ask` for close friends' `schedule` and `plans`. `health` and `finances` are widened only by the owner, per person or group, as today. `people` covers what the owner said about others and stays `none`; what others said is never told at all.

An existing vault is never widened on its own. The 0.64.0 migration proposes these defaults as one `ask`, and skips any subject and audience where the owner has already set a rule, so a no that was said stays said.

## Asking around

The point of wider defaults is that an intellect can ask its owner's friends' intellects and get a useful answer: "know a good dentist?", "anyone been to this place?", "who's free Saturday?". Each answering intellect replies from its tellable note, within its rules, and says whose experience it is. The answers come back to the owner as one brief. An introduction (two people put in touch because their intellects saw a match) always needs both owners' yes.

The access log already records each request and what was told, so the owner can see what their intellect said about them, and to whom.

## Open

- **The profile in a known person's session.** It is cut to 250 words and can hold what the owner said about that person. It stays, so the agent knows who it is talking to; the access rules still say `people` is told to no one.
- **Acting for someone** without the vault's tools: an `ask` already goes to the owner; a `yes` grant does too in this slice.
- **Seeding from mail.** The seed pass (`docs/portrait.md` §5) fills `experiences` from transactional mail once it reads it; until then the note grows from conversation and the calendar.
