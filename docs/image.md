# The image

*Draft, 2026-11-07. A rework of how the portrait is built, so that it can grow into a full image of a person or an organisation: modular, built up source by source, expandable without rewriting what exists. It keeps everything `docs/portrait.md` settled about a line (its basis, id, tests, the owner's answers, reception) and changes where lines live and how they get there. Read with `docs/portrait.md`, `docs/sharing.md` (the tellable note) and Demiurge's `docs/goals.md`.*

## 1. Why

The portrait as built is one note: six fixed sections, eight lines each, 700 words. That is the right size for what goes into every session, and the wrong shape for an image. Everything known about a person's music, their food, their work, their weekend game and each friend competes for the same 48 lines, so depth has nowhere to go and is thrown away.

The way sources reach it does not scale either. Every source (mail and calendar, a repository's reviews, a tracker's projects, public research, connected inboxes) is a branch in one module (`seed.py`) and a section of one skill, each with its own file format, and the kinds of source are a fixed list in code. Adding iMessage, Drive or a music service means editing all of them, and nothing a source finds is kept between the raw input and the finished line.

The image needs three things the portrait lacks: **depth** (as much as is known, by area of life), **modularity** (a new source or a new area is an addition, never an edit to the others) and **a built-up record** (what each source found is kept, so the image can be rebuilt, audited and extended).

## 2. Three layers

```
sources ──extract──▶ evidence ──judge──▶ facets ──compile──▶ portrait
(modules,            (one format,        (the image:          (the summary in
 one per source)      outside the vault)   one note per area)   every session)
```

Each layer has one owner and one contract. A source never writes a facet; a facet never reads raw input; the portrait is compiled, never edited.

## 3. Sources

A source is a module that turns one kind of raw input into evidence. It has an id (`mail`, `calendar`, `imessage`, `reviews`, `plans`, `research`, `drive`, `music`), an edition (`personal`, `organisation` or both), where it runs (`owner-mac` for what only the owner's machine holds, such as Messages; `runtime` for synced channel files; `agent` for research the agent does with its own tools), and its window.

**The contract.** `brain source <id> extract [--since DATE]` reads the raw input and writes evidence (section 4); it is deterministic wherever it can be, and keeps no raw text. A source that needs judgment to extract (public research) is a skill whose only output is evidence in the same format. Either way, the rest of the pipeline sees only evidence.

**The registry.** `src/brain/sources/` holds one Python module per source, and each declares itself (a module of sources the agent reads with its own tools, such as an organisation's site, press and CRM, declares several as `SOURCES`, with no `extract`):

```python
SOURCE = Source(id="imessage", editions=("personal",), runs="owner-mac", window_days=None,
                facets=("people", "places", "taste", "play"), sensitive=True)
def extract(ctx, since) -> list[Evidence]: ...
```

Sources that read what the runtime already holds (`runs="runtime"`: `mail`, `calendar`) are extracted by the night itself; a preview of the night keeps what they find in its disposable copy. Each declaration also says where coverage files it (`where`: the owner's own accounts, an organisation's internal sources, or public ones, with a `rank` among them), the kind of connection it reads through (`needs`: google, github, tracker, crm, chat, docs), and for a runtime source the globs of its synced files within a channel folder (`files`), so the sync's layout is declared by the source that reads it. `brain portrait coverage` lists the sources declared for the vault's edition, and a Seeds note's `source:` must name one. `brain source list` prints the registry with each source's last extraction and how much evidence it holds. Adding a source is one module and its tests: coverage, the seed's reading list and the owner's view of what has been read pick it up from the registry, with no list to edit.

**Rules every source keeps.** No raw message, email or document body is written, only derived facts with a pointer back. Codes, passwords, tracking and session parameters are dropped at extraction. Each item says whose it is (`own`, or `other:<who>`), from the source's structure where possible (sent mail and the owner's own messages are `own`; what someone else wrote is theirs), so the sharing rule holds by construction downstream. A source that reads the owner's private life (`sensitive=True`) runs only when the owner starts it, and its evidence is never indexed.

## 4. Evidence

This is the engine's one evidence contract: the portrait's sources and the knowledge model's sources (consults, acts, seeders; `docs/knowledge.md`) both write it.

Evidence is kept outside the vault, in `$AGENT_HOME/evidence/<source>/YYYY-MM.md`, like channel files: it can be large, it is never indexed into Demiurge, and it can be thrown away and rebuilt by extracting again. One line per item:

```md
- 2026-10-14 · went · Dinner at a neighbourhood bistro, booked for two · own · places, food · mail:3f9a
- 2026-10-20 · shared · Link to a bouldering gym's beginner night · own · play · imessage:link/8c1d
- 2026-10-22 · talks · Daily with one friend, mostly in a group of four · own · people · imessage:person/2b
- 2026-11-03 · said · "I want to read less news" · own · reading · conversation:2026-11-03
- 2026-11-04 · wrote · Shared a sublet listing with the owner · other:a friend · places · imessage:link/91e0
```

The fields are date, a verb, the fact in one plain sentence, whose it is, one or more hints, and a pointer the source can resolve back to its input. Verbs are an open set: each source declares the verbs it emits (the personal sources use `said`, `did`, `went`, `bought`, `booked`, `shared`, `talks`, `plans`, `corrected`, `reviewed`, `decided`). A hint names a portrait facet or a knowledge area (`docs/knowledge.md`), so the image and routing read the same evidence filtered the same way. A source that needs a structured value beyond the fact (stakes, say) adds optional trailing `key=value` fields to the row; there is never a second format. Counts and rankings a source computes (closeness, how often a site is shared, effort by project) are evidence too, as `talks` or `did` lines with the number in the fact.

`brain evidence [--source S] [--hint H] [--since DATE] [--json]` reads it back, newest first, so the judging step reads a few hundred lines for one area, not a source's whole output.

## 5. Facets: the image

The image is a folder beside the owner's profile, `Portrait/`, one note per facet (an area of a life or of an organisation). A facet note holds lines in the portrait's grammar, unchanged (`- text · said|seen|guess · confidence · date · [[source]] ^id` with `horizon:`, `for:`, `test:`, `against:`, `goal:`), under the same six headings, using only the ones it needs:

```md
---
facet: Music
rank: 4
sensitivity: open
subjects: [opinions, experiences]
audience: owner
---
## Taste
- Late-night electronic over anything with lyrics while working · seen · high · 2026-11-02 · [[Evidence/music]] ^a1f0
## Energy
- Goes to a show about once a month, always with the same two friends · seen · medium · 2026-10-28 · [[Evidence/calendar]] ^a1f1
## Not
- Doesn't want playlists made for them · said · high · 2026-10-30 · [[2026-10-30 conversation]] ^a1f2
```

**Starting facets.** A person: Work, People, Places, Food & drink, Music, Reading & ideas, Play (games, sports, hobbies), Home & routine, Health and Money (both `sensitivity: said-only`: a line enters only as something the owner said). An organisation: Direction, Customers, Product, Engineering, Operations, Culture. These are a starting set, not a schema.

**Facets are open.** The judging step creates a facet when at least three lines it would write share an area no facet covers ("Poker", "Climbing", "The house in the country"); `brain portrait facets` lists them with their line counts and last evidence. A facet with no new evidence for a year is folded into a neighbour by the weekly pass, its lines and ids kept.

**What each facet declares.** `hints` (the words its evidence carries beyond its name, so a narrow facet takes its rows from a broad one), `rank` (how central the area is to the person, which the compiled portrait uses), `sensitivity` (`open` or `said-only`), `subjects` (the sharing subjects in `docs/sharing.md` its own facts may be filed under, so the tellable note and the image agree on what is whose), and `audience: owner` for a person's, team for an organisation's. Ids are unique across the whole folder; `brain portrait stamp` and `mark` work on any facet; a line can move between facets keeping its id.

**Caps per facet, not per person.** Forty lines and 1,500 words a facet, eight a section within it. The image as a whole has no cap; what enters every session is the compiled portrait, below.

**The judging step** is the seed skill, split by facet instead of by source: for each facet with new evidence since its last pass, read `brain evidence --hint F --since <last>`, then add, revise, promote or retire lines in that facet only. A run touches as many facets as have new evidence, and one facet's pass never edits another. Which facet a row goes to is the facets' to say: a facet may claim `hints:` in its frontmatter (Climbing claims `climb`, `boulder`), and a row naming one, in its hints or its fact, goes to that facet instead of the broader one its source hinted (Play); otherwise a row goes to the facet its hint names, existing or to be created. `brain portrait evidence <facet>` gives a pass exactly its rows. `brain portrait due` lists the facets with evidence newer than each one's `judged` date (and the areas a source names that no facet covers yet), ordered by the weight of that evidence, not its count (what the owner said or decided counts most, then what they did, wrote and booked, then links, and a routine notice least; someone else's row counts half); the pass writes a line for every pattern the rows show, then runs `brain portrait judged <facet> --through <date>`, which refuses a facet left without lines when its rows say something about the owner (a reason given with `--empty` lets a genuinely empty pass through); `brain portrait open <facet>` makes a new facet's note. Health, Money and any `said-only` facet never take evidence. A line drawn from evidence names its source as `evidence:<source>`, plain text, since evidence is outside the vault. What a facet has read is recorded by row, beside the evidence (`$AGENT_HOME/records/portrait/read/<facet>.txt`), so a row that arrives late, dated before the facet's last pass (Messages brought in after the mail was judged), is still due; `judged` stays a date for people to read. A facet judged before this record existed keeps what its date counted as read. The People review keeps doing the daily part (conversations, receptions, confirms), now writing into the facet a line belongs to.

**A picture, not a log.** A facet is two layers. Its `## Sketch` is prose, two to four sentences under 120 words: that part of the owner's life as a friend would describe it, how its lines fit together and what is uncertain, rewritten when its lines change. Its lines are the claims the sketch rests on, each about the person (a trait, a want, a pattern, a tension), with what happened as `because:` beneath it. `Portrait/Overview.md` (`overview: true`, never a facet) holds the whole picture across facets, under 160 words. Lines alone read as an audit log of events; the sketches are what make it an image, and the lines are what the owner corrects.

## 6. The portrait, compiled

`Portrait.md` becomes generated, like CURRENT's active block: the night (and `brain portrait compile`) writes it from the facets, leading with the overview's sketch. For each of the six sections it takes each facet's best lines first (line rank within its facet, then facet rank, then confidence, so the summary is broad before it is deep) to the existing caps (eight a section, 700 words), and writes each as a reference to its source line. It refuses while the hand-written portrait still holds a line no facet has, so sorting it into facets never drops one:

```md
## After
- Run a sub-3:00 marathon in April · said · high · 2026-11-01 · [[Portrait/Play]] ^3f2a
```

The id is the facet line's, so an answer from the owner, a reception row or an explore account naming `portrait:3f2a` lands on the one line that exists. Nobody edits `Portrait.md`; validate names a hand edit. Everything that reads the portrait today (the session context, `--top`, `coverage`, explore, prime, the owner card's rule, the state export) keeps reading it unchanged.

## 7. Seeing and answering it

The owner's page shows the compiled portrait first, as now. Each section opens its lines grouped by facet, and each facet opens whole: the image, browsable. `ownerPortrait` in the state export carries the facets (lines and their facet), still for the owner alone; until the backend strips a new key from everyone else's view the way it strips `ownerPortrait`, the export keeps carrying only the compiled portrait, whose ids are the facet lines', so answers already land on the facet; an organisation's `portrait` carries its facets' `said` and `seen` lines. Right, wrong and not this work on any line. Coverage moves to facets: "Music: 3 lines, last evidence two weeks ago; Health: nothing, and no source covers it", with the sources that could fill it named from the registry.

## 8. How it grows

- **A new source** is one module under `src/brain/sources/` with its tests, declaring the verbs and hints it emits. Nothing else changes. iMessage is the first built this way: its metadata and links exporter already produces closeness and links, which become `talks` and `shared` evidence.
- **A new facet** appears by itself when evidence clusters, or by the owner asking ("keep a page on my climbing"), and is one note.
- **A new kind of fact** a facet needs (a field beyond the line grammar) is a later decision; the grammar stays the same across facets so every tool reads every facet.

## 9. Neighbours

- **Goals** stay `brain goals`'s register and the only reason for unasked work. A facet's After line names its goal (`goal: G-n`); inferred goals (Demiurge `docs/goals.md`) come from evidence the same way and are proposed through goals, not written as portrait lines.
- **The tellable note** stays the only thing told to anyone. A facet line that is the owner's own and fits one of the facet's `subjects` is filed there by the same judging pass, as the seed does now; later, `Tellable.md` can be compiled from those lines the way `Portrait.md` is.
- **People.** The People facet holds the owner's relationships as the owner lives them (who they are close to, how they keep in touch, who is coming up). A person's own profile stays thin: relationship, closeness, what is coming up for them, never what they confided. Evidence about them stays in evidence.
- **Reception and coverage** attach to line ids and source ids, so they span the whole image.

## 10. Migration and order

Built in this order, each a release that stands alone:

1. **Facets.** `Portrait/` and the facet note format; the engine reads, validates, stamps and marks lines across facets; `brain portrait compile` writes `Portrait.md` from them. Migration: each vault's existing portrait lines are sorted into starting facets by the agent, ids kept, and `Portrait.md` is compiled from the result, so nothing a person sees changes on day one.
2. **Evidence and the source registry.** The evidence format, `brain evidence`, `brain source list|extract`, and the first sources moved onto it: `imessage` (new), then `mail` and `calendar` from the personal seed. The seed skill judges by facet from evidence.
3. **The organisation's sources** moved onto evidence: `reviews` (each person's correction, hinted Engineering) and `plans` (each open project and where effort goes, hinted Direction) are extracted nightly from the files the seed writes; the public and internal kinds the agent reads itself are declarations; `seed.py` keeps only the personal seed. An organisation's facets are judged from evidence nightly in Sleep 2, as a person's are in the People review.
4. **The owner's view of the image** in Demiurge: facets browsable from the compiled portrait, coverage by facet.

Every step is shared work: the consults and seed-on-connect sessions use `seed.py` today, and each step is claimed in the work register before it starts.

## 11. Open

- **Where evidence lives for an owner whose Mac is not the runtime's machine.** `owner-mac` sources extract on the owner's machine; their evidence has to reach `$AGENT_HOME`. The paired-computer path that already runs commands on the owner's computer is the likely carrier.
- **How much judgment a facet pass gets.** Reading a few hundred evidence lines per facet is cheap; a person with twenty active facets is twenty passes. Probably: only facets with new evidence, at most a handful a night, the rest the next night.
- **Facet rank.** Set by the judging pass from how much evidence and how many After lines a facet holds, or by the owner pinning what matters to them. Probably both, the owner's pin winning.
- **Rebuilding.** Because evidence is kept and extraction is repeatable, an image could be rebuilt from scratch after a format change. Whether the owner's answers (rights, wrongs, nots) are replayed onto a rebuild, and how, needs deciding before the first rebuild.
