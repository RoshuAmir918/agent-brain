---
name: {prefix}portrait
description: The rules of the portrait and its facets, for every pass that writes them
version: 1.0.0
metadata:
  runtime:
    tags: [memory, portrait]
    category: brain
---
# The portrait

The rules every pass that writes the portrait follows: the People review, Sleep 2 and the seed. What each pass reads and when is in its own skill; what a line is, where it lives and how evidence becomes one is here, once.

## A line
`- <text> · <said|seen|guess> · <high|medium|low> · <latest evidence, YYYY-MM-DD> · <source> ^<id>`, under one of six headings: `After`, `Cares about`, `Taste`, `How they decide` (an organisation's may say `How it decides`), `Energy`, `Not`. No ` · ` inside the text. The source is a link to the note it rests on (`[[<note>]]`) or, for a line drawn from evidence, `evidence:<source>` in plain text (`evidence:imessage`). Indented under it:
- `  - horizon: now|year|longer` and `  - for: <where you can help get there>` on every After line, and `  - goal: G-n` on the After line that reflects an active goal;
- `  - test: <a cheap way to check, asked once at a natural moment>` on every guess;
- `  - against: <counterevidence>` where what is said and what is done differ; it never leaves the vault.

**Bases.** `said`: {human_name} said it about themselves, in their framing. `seen`: a pattern seen at least twice, stated as behaviour (what they do, decide, ask for, return to, push back on). `guess`: an inference from one signal, with its test. Health, mood, relationships, money and other sensitive subjects are only ever `said`, in their own words. Someone else's words never become {human_name}'s statement. Be honest, not flattering.

**One claim, one line.** A line that says the same thing as a new one gets the newer date and, with more evidence, a higher confidence; never a duplicate. A line contradicted by new evidence is revised, never appended to, and its old version goes to `History.md` beside the portrait, dated, with its id. A claim retired or corrected (in History, or as a `Not` line) is never proposed again: read both before writing.

## Where lines live
Once the `Portrait/` folder beside `{human_portrait}` holds notes (`brain portrait facets`), each line lives in the facet it is about, and you write there: `{human_portrait}` is compiled from the facets each night and on `brain portrait compile`, and `brain validate` names a hand edit to it. A person's facets start as Work, People, Places, Food & drink, Music, Reading & ideas, Play, Home & routine, Health and Money; an organisation's as Direction, Customers, Product, Engineering, Operations and Culture. An area becomes its own facet once three lines share it. Start a facet, or give a line a better one, with `brain portrait move <id> <Facet>`, which writes the facet's frontmatter (`facet`, `rank`, `sensitivity`, `subjects`); Health and Money are `sensitivity: said-only` and take only `said` lines. When a facet is about something narrower than the area its evidence arrives under (Climbing within Play), give it `hints:`, the words its rows carry (`hints: [climb, boulder]`), and those rows come to it instead.

Before there are facets, sort the portrait once: `brain portrait stamp`, then move every line to the one facet it is about; the portrait compiles itself when the last has moved. Before there is any portrait, create `{human_portrait}` with `kind: orientation`, `pinned: true`, `tier: hot` and the six headings.

## Caps and ids
At most eight lines a section, ranked. A facet holds at most forty lines and 1,500 words; a portrait written by hand at most 700 words, links and fields included; the compiled portrait keeps to that itself. Keep every line's `^id`; write a new line without one, then `brain portrait stamp`. When {human_name} answers a line in their app (right, wrong or not this), the engine applies it with `brain portrait mark`; never undo it.

## Promote and retire
A guess whose offers came back `used` or `liked` twice in `brain reception summary` becomes `seen`; one {human_name} confirmed becomes `said`. A guess whose offer was declined, or that they corrected, moves to History; when they corrected it, their words become a `Not` line. A guess with no new evidence for thirty days moves to History (`brain validate` names it), as does an After line whose goal was reached, superseded, set aside, refused or converted.

## From evidence
What the sources found waits in evidence, outside the vault. `brain portrait due --json` lists the facets with rows newer than their last pass (an entry marked new is an area no facet covers yet). For each one taken, read `brain portrait evidence <facet> --json`, the rows routed to it since its last pass, and add, revise, promote or retire lines in that facet only. A pattern across rows is `seen` (texts the same two friends most days; four restaurant bookings a month); one row is a `guess` with a test. A row whose `whose` is `other:<who>` is someone else's: a link a friend sent says what they share, not what {human_name} likes, unless {human_name} shared it on or answered it. Name other people only by their relationship to {human_name} and how they keep in touch, never by what they confided. Then `brain portrait judged <facet> --through <newest row's date>`; for an entry marked new, run it before writing, since it creates the facet note. Health and Money never come from evidence.
