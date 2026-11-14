---
name: {prefix}portrait
description: The rules of the portrait and its facets, for every pass that writes them
version: 1.7.0
metadata:
  runtime:
    tags: [memory, portrait]
    category: brain
---
# The portrait

The rules every pass that writes the portrait follows: the image pass and the seed. What each pass reads and when is in its own skill; what a line is, where it lives and how evidence becomes one is here, once.

## The picture
The portrait is a picture of {human_name}, not a log of what they did. It is built in two layers.
<!-- personal -->
The **sketch** is prose: who they are, what drives them, how the parts of their life fit together and pull against each other, what you are unsure of, written the way a close friend would describe them to someone who will meet them.
<!-- /personal -->
<!-- enterprise -->
The **sketch** is prose: what the organisation does, what drives it, how its priorities fit together and pull against each other, and what you are unsure of, written for a colleague joining its work.
<!-- /enterprise -->
The **lines** are the claims the sketch rests on, each one they can mark right or wrong.

- Leave out a heading with no lines under it.
<!-- personal -->
- Each facet opens with `## Sketch`: two to four sentences on that part of their life, under 120 words, rewritten whenever its lines change.
<!-- /personal -->
<!-- enterprise -->
- Each facet opens with `## Sketch`: two to four sentences on that part of the organisation, under 120 words, rewritten whenever its lines change.
<!-- /enterprise -->
<!-- personal -->
- `Portrait/Overview.md` (frontmatter `kind: hub`, `tier: warm`, `overview: true`) holds the whole picture under its own `## Sketch`, under 160 words: the person across their facets, the threads that run through them.
<!-- /personal -->
<!-- enterprise -->
- `Portrait/Overview.md` (frontmatter `kind: hub`, `tier: warm`, `overview: true`) holds the whole picture under its own `## Sketch`, under 160 words: the organisation across its facets, the priorities that run through them.
<!-- /enterprise -->
  Rewrite it whenever a pass changes any facet. The compiled portrait leads with it, so every session reads the picture before the lines.
- Never put a line, a date or a source in a sketch; the lines carry those.

## A line
<!-- personal -->
`- <text> · <said|seen|guess> · <high|medium|low> · <latest evidence, YYYY-MM-DD> · <source> ^<id>`, about {human_name}, not about an event: a trait, a want, a pattern, a tension ("Wants peers among builders, not only teammates"), never "registered for a meetup on November 18".
<!-- /personal -->
<!-- enterprise -->
`- <text> · <said|seen|guess> · <high|medium|low> · <latest evidence, YYYY-MM-DD> · <source> ^<id>`, about {human_name}, not about an event: a priority, a pattern, a tradeoff ("Prioritises reliable releases over new features"), never "shipped version 2 on November 18".
<!-- /enterprise -->
What happened is its evidence, in `because:`.
<!-- personal -->
A habit that says nothing about what they value, want or are like (books tables) is evidence for a line that does: fold it into that line's `because:` (meals as where friendship and work meet) and write no line of its own.
<!-- /personal -->
<!-- enterprise -->
A routine action that says nothing about what the organisation values (holds weekly demos) is evidence for a line that does: fold it into that line's `because:` (keeps product decisions grounded in customer feedback) and write no line of its own.
<!-- /enterprise -->
<!-- personal -->
Lines sit under one of six headings: `After`, `Cares about`, `Taste`, `How they decide` (an organisation's may say `How it decides`), `Energy`, `Not`.
<!-- /personal -->
<!-- enterprise -->
Lines sit under one of six headings: `After`, `Cares about`, `Taste`, `How it decides`, `Energy`, `Not`.
<!-- /enterprise -->
No ` · ` inside the text.
<!-- personal -->
The source is a link to the note it rests on (`[[<note>]]`) or, for a line drawn from evidence, `evidence:<source>` in plain text (`evidence:imessage`).
<!-- /personal -->
<!-- enterprise -->
The source is a link to the note it rests on (`[[<note>]]`) or, for a line drawn from evidence, `evidence:<source>` in plain text (`evidence:calendar`).
<!-- /enterprise -->
Indented under it:
- `  - horizon: now|year|longer` and `  - for: <where you can help get there>` on every After line, and `  - goal: G-n` on the After line that reflects an active goal;
- `  - test: <a cheap way to check, asked once at a natural moment>` on every guess;
<!-- personal -->
- `  - because: <what was said or seen, with its specifics>` on every line from mail, calendar, messages or evidence (registered for the November 18 SF builders meetup; texted Jo most days since 2019), only what happened;
<!-- /personal -->
<!-- enterprise -->
- `  - because: <what was said or seen, with its specifics>` on every line from mail, calendar, messages or evidence (reviewed the launch plan on November 18; held weekly customer calls since 2019), only what happened;
<!-- /enterprise -->
- `  - against: <counterevidence>` where what is said and what is done differ; it never leaves the vault.

**Bases.** `said`: {human_name} said it about themselves, in their framing. `seen`: a pattern seen at least twice, on separate occasions, stated as behaviour (what they do, decide, ask for, return to, push back on); something done again and again is `seen`, not a guess, even when you cannot see why. `guess`: an inference from one signal, with its test.
<!-- personal -->
Health, mood, relationships, money and other sensitive subjects are only ever `said`, in their own words.
<!-- /personal -->
Someone else's words never become {human_name}'s statement. Be honest, not flattering.
<!-- personal -->
A line names the pattern and its `because:` carries the specifics (texted since 2019, five sessions since October, four bookings this month), never a hedge or caveat in its words or its `because:` ("possibly", "not proof of"): how sure you are is its basis and confidence, and what would settle it is its test.
<!-- /personal -->
<!-- enterprise -->
A line names the pattern and its `because:` carries the specifics (weekly reviews since 2019, five demos since October, four releases this month), never a hedge or caveat in its words or its `because:` ("possibly", "not proof of"): how sure you are is its basis and confidence, and what would settle it is its test.
<!-- /enterprise -->

**One claim, one line.** A line that says the same thing as a new one gets the newer date and, with more evidence, a higher confidence; never a duplicate. A line contradicted by new evidence is revised, never appended to, and its old version goes to `History.md` in the folder that holds `{human_portrait}` (never inside `Portrait/`, where every note is a facet), dated, with its id. An id belongs to one claim: rewording it, or changing its basis or confidence, keeps the id, but a different claim, or one that belongs in another section, is a new line written without an id, and the line it grew from stays where it is or goes to History by the rules below (a guess under After that the evidence now shows {human_name} doing has been reached: it goes to History as reached, and what they now do is a new line). A claim retired or corrected (in History, or as a `Not` line) is never proposed again: read both before writing.

## Where lines live
Once the `Portrait/` folder beside `{human_portrait}` holds notes (`brain portrait facets`), each line lives in the facet it is about, and you write there: `{human_portrait}` is compiled from the facets each night and on `brain portrait compile`, and `brain validate` names a hand edit to it.
<!-- personal -->
A person's facets start as Work, People, Places, Food & drink, Music, Reading & ideas, Play, Home & routine, Health and Money; an organisation's as Direction, Customers, Product, Engineering, Operations and Culture.
<!-- /personal -->
<!-- enterprise -->
An organisation's facets start as Direction, Customers, Product, Engineering, Operations and Culture.
<!-- /enterprise -->
<!-- personal -->
Sport, exercise, games and hobbies are Play; Health is the body's care, medical and mental, and mood.
<!-- /personal -->
A line that holds across areas goes to the facet its evidence came from. An area becomes its own facet once three lines share it.
<!-- personal -->
Start a facet, or give a line a better one, with `brain portrait move <id> <Facet>`, which writes the facet's frontmatter (`facet`, `rank`, `sensitivity`, `subjects`); Health and Money are `sensitivity: said-only` and take only `said` lines.
<!-- /personal -->
<!-- enterprise -->
Start a facet, or give a line a better one, with `brain portrait move <id> <Facet>`, which writes the facet's frontmatter (`facet`, `rank`, `sensitivity`, `subjects`).
<!-- /enterprise -->
<!-- personal -->
When a facet is about something narrower than the area its evidence arrives under (Climbing within Play), give it `hints:`, the words its rows carry (`hints: [climb, boulder]`), and those rows come to it instead.
<!-- /personal -->
<!-- enterprise -->
When a facet is about something narrower than the area its evidence arrives under (Reliability within Engineering), give it `hints:`, the words its rows carry (`hints: [reliability, uptime]`), and those rows come to it instead.
<!-- /enterprise -->

Before there are facets, sort the portrait once: `brain portrait stamp`, then move every line to the one facet it is about; the portrait compiles itself when the last has moved. Before there is any portrait, create `{human_portrait}` with `kind: orientation`, `pinned: true`, `tier: hot` and the six headings.

## Caps and ids
At most eight lines a section, ranked. A facet holds at most forty lines and 1,500 words; a portrait written by hand at most 700 words, links and fields included; the compiled portrait keeps to that itself. Keep every line's `^id`; write a new line without one, then `brain portrait stamp`. When {human_name} answers a line in their app (right, wrong or not this), the engine applies it with `brain portrait mark`; never undo it.

## Promote and retire
A guess whose offers came back `used` or `liked` twice in `brain reception summary` becomes `seen`; one {human_name} confirmed becomes `said`. A guess whose offer was declined, or that they corrected, moves to History; when they corrected it, their words become a `Not` line. A guess with no new evidence for thirty days moves to History (`brain validate` names it); one sourced to a Seeds note counts its thirty days from that note's date, so a first portrait read from a year of mail keeps its older guesses for {human_name} to answer, as does an After line whose goal was reached, superseded, set aside, refused or converted.

## From evidence
What the sources found waits in evidence, outside the vault. `brain portrait due --json` lists the facets with rows newer than their last pass, the most telling evidence first: what {human_name} said, did and wrote outweighs a pile of notices (an entry marked new is an area no facet covers yet). Take them in that order. For each one taken, read `brain portrait evidence <facet> --json`, the rows routed to it since its last pass, and add, revise, promote or retire lines in that facet only. Every pattern the rows show becomes a line or strengthens one; only rows that show nothing about them (a one-off notice) may be passed over.
<!-- personal -->
A pattern across rows is `seen` (texts the same two friends most days; four restaurant bookings a month); one row is a `guess` with a test.
<!-- /personal -->
<!-- enterprise -->
A pattern across rows is `seen` (reviews customer feedback each week; four releases a month); one row is a `guess` with a test.
<!-- /enterprise -->
<!-- personal -->
A row whose `whose` is `other:<who>` is someone else's: a link a friend sent says what they share, not what {human_name} likes, unless {human_name} shared it on or answered it.
<!-- /personal -->
<!-- enterprise -->
A row whose `whose` is `other:<who>` is someone else's: a link a colleague sent says what they share, not what {human_name} endorses, unless a member endorsed it on behalf of the organisation.
<!-- /enterprise -->
<!-- personal -->
Other people appear by name, with their relationship to {human_name} and how they keep in touch (texts Jo most days; a Thursday dinner group of four), never with what they confided or anything private about them.
<!-- /personal -->
<!-- enterprise -->
Colleagues appear by name, with their role in {human_name} and their part in its work (owns release reviews; leads the weekly customer call), never with personal confidences.
<!-- /enterprise -->
For an entry marked new, `brain portrait open <facet>` makes its note first. Write the lines and rewrite the facet's sketch, and only then `brain portrait judged <facet> --through <newest row's date>`; it refuses a facet left without lines when its rows say something, unless you give `--empty "<why nothing in them makes a line>"`.
<!-- personal -->
Health and Money never come from evidence.
<!-- /personal -->
