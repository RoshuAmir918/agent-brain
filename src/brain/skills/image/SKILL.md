---
name: {prefix}image
description: Judge the portrait from evidence and reception
version: 1.0.0
metadata:
  runtime:
    tags: [memory, night, portrait]
    category: brain
    context: []
---
# Image

The nightly portrait pass, for a person or an organisation. Load `{prefix}portrait` with it; that skill holds the rules for every line, sketch, cap and id. The chain extracts evidence before this pass and stamps and compiles after it.

## Procedure
1. **First sort.** Only when `brain portrait facets` is empty and the hand-written `{human_portrait}` has lines: `brain portrait stamp`, then `brain portrait move <id> <Facet>` for every line, keeping its id. The portrait compiles itself when the last line moves. Then write each facet's sketch and the overview by `{prefix}portrait`: that picture is what the owner sees first. This has its own budget, on top of the three evidence facets below.
2. **Facets from evidence.** Take the first three facets in `brain portrait due --json` (`image.PASSES`, the same budget for both editions) and judge each by `{prefix}portrait`'s From evidence. For a person these include the `conversation` rows Sleep 1 writes; for an organisation, its sources' rows, including review corrections and plans.
3. **Promote and retire.** Read `brain reception summary` and the owner's corrections. Promote or retire guesses by the portrait rules. For a person, read `brain goals list --json`: each active goal has an After line with its `goal: G-n`, horizon and where to help, and ended goals leave After for History. Infer or end no goal here. Rewrite changed facets' sketches and the overview.
4. **Weekly, the first run after Sunday.** Check the last weekly image entry in the Sleep Log first. Re-rank every facet's sections from the week's evidence and reception, set facet ranks by what is central now, retire stale guesses to History, fold into the line it supports any line that says nothing about who they are, fold a facet with no new evidence for a year into a neighbour keeping its lines and ids, and keep the caps. Rewrite the overview. Record which top three After lines got neither an explore run nor an offer this week and the week's hit rate, using `brain portrait coverage` and `brain reception summary`.
5. **Confirms.** For a new or changed `seen` or `guess` line outside After, add a confirm in CURRENT's Needs you: "I've come to think you … — right?", one guess per confirm, never a list. Count portrait confirms in Needs you and History over the last seven days; add none past two. The answer is evidence on the line.
6. **Record.** One image entry in `{agent_dir}/Sleep Log/<month>.md`, under 100 words: facets judged, guesses promoted or retired, confirms, and the weekly line when due. `brain commit -m "image(<date>)" --push <the files you changed>`.

## Boundaries
Writes only portrait facets, their History and overview, portrait confirms in Needs you and the Sleep Log. Read evidence in place; no digest or mailbox walk, no source sync, no messages. Never compile context here.
