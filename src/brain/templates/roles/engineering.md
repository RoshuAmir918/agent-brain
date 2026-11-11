---
kind: role
template: engineering
tier: warm
produces: pull_request
actions:
- git.push_branch
- git.open_draft_pr
- ci.run
corrections:
- event: review.changes_requested
  weight: 3
- event: review.comment
  weight: 2
- event: merge.diff_from_opened
  weight: 2
- event: pr.closed_unmerged
  weight: 3
- event: revert
  weight: 4
sources:
  corrections:
  - reviews
  team:
  - reviews
  rulings:
  - chat
  - docs
  work:
  - plans
notes:
- shape: Architecture
  holds: Layers, boundaries, where things live and why, each with the best example file.
- shape: Taste
  holds: Naming, how much to abstract, errors, tests and PR shape, each with an example and the comment that taught it.
- shape: Decisions
  holds: Rulings with their reasons, including where the team changed its mind.
- shape: Kinds
  holds: Per kind of work, the reference PRs, the pitfalls and what reviewers ask for.
lenses:
- lens: data_and_audit
  covers: data changes and migrations, audit trails, authorization and isolation, sensitive data
- lens: layering_and_scope
  covers: layering and boundaries, one concern per PR and how work is split, abstraction, reuse of existing code
- lens: rollout
  covers: deploy safety, version skew, in-flight jobs, infrastructure and grants, flags, contracts others rely on, what
    the PR must tell the operator
- lens: taste
  covers: naming, error handling, types, what the team tests, comments, PR description shape
decisions:
- type: customer_contract
  covers: what customers see or rely on, such as API shapes, file formats and notifications
- type: data_change
  covers: changing or backfilling stored data, and anything that cannot be undone by a revert
- type: authorization
  covers: who may see or do what
- type: rollout_risk
  covers: risk the team accepts when shipping, such as an unflagged change or a migration under load
measures:
  burden: review comments per PR
process:
  gate: true
  review: true
screen:
  kinds:
  - kind: correction
    covers: asks the author to change how something is done, for a reason that holds beyond this piece of work, or the
      author's reply accepting or rejecting such a request
  - kind: taste
    covers: a preference about naming, abstraction, structure, tests, comments, error handling or the shape of the work
  - kind: decision
    covers: a product, architecture, security or rollout ruling, with its reason
  - kind: local
    covers: about this piece of work only, such as a typo, a one-off bug or a question, with no lesson that carries over
  - kind: noise
    covers: status, thanks, commands, CI or deploy chatter, or a bot's boilerplate
  worth:
  - nothing beyond this piece of work
  - generic advice any reviewer anywhere would give, with nothing specific to this team
  - a convention specific to this team or codebase, naming its files, helpers, patterns, layers or rules
  - a rule the team states or enforces, a decision with its reason, or a correction that changed how the work was done
  keep:
    kinds:
    - correction
    - taste
    - decision
    worth: 2
---
# Engineering

This role writes code for the team: it opens draft pull requests on work it picked, a person reviews them, and it never
merges. What it knows about how this team builds is in the notes beside this one, learned from the team's own reviews
and rulings, every line sourced.

Edit anything here. The frontmatter says what the role makes, which corrections teach it most, where it learns from,
how its notes are shaped, which reviewers check its work and which decisions belong to people. Its notes are yours to
edit too: change a line's words and yours outrank what it learned; move a line under `## Struck`, or delete it, and it
is not learned again without new evidence.
