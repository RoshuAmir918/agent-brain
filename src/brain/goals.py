"""The person's goals. One writer for blocks, evidence archives and refused readings.

Mutation helpers are lock-free; their CLI caller holds the vault lock.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import evidence as rows, fm, identity
from .config import Config
from .tasks import STATE_RE, Task, parse_state, single_line
from .vault import Vault, WIKILINK_RE

ID = re.compile(r"G-\d+")
HEAD = re.compile(r"^### (G-\d+) — (.+?)[ \t]*$", re.M)
STATES = ("active", "reached", "set_aside", "superseded")
KINDS = ("reach", "become")
ORIGINS = ("said", "inferred")
WEIGHTS = ("more", "less", "normal", "pause")
ITEM_ID = re.compile(r"[A-Za-z0-9._:-]{1,80}")
ACCOUNT = re.compile(r"^- \*\*Account \((\d{4}-W\d{2})\):\*\* (.+)$", re.M)
WORDS = re.compile(r'^- \*\*In their words:\*\* "(.*)" \((\d{4}-\d{2}-\d{2}|unknown)\)$', re.M)
EVIDENCE = re.compile(r"^- \*\*Evidence:\*\* (\d{4}-\d{2}-\d{2}) · (.+)$", re.M)
ENDING = re.compile(r"^- \*\*(?:Reached|Superseded|Set-Aside):\*\* (\d{4}-\d{2}-\d{2}) — ", re.M)
TEMPLATE = "# Goals\n\n## Aspirations\n\n## Active goals\n\n## Adopted practices\n\n## Set aside\n"


def day(value: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("use a date: YYYY-MM-DD")
    return date.fromisoformat(value).isoformat()


def line(name: str, value: str, limit: int | None = None) -> str:
    original = value
    value = single_line(name, value)
    if name in ("title", "words", "why") and any(marker in value for marker in ("<!--", "-->")):
        raise ValueError(f"{name} cannot contain HTML comment delimiters (<!-- or -->); use plain text such as an arrow (→) instead")
    if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in original) or (name == "why" and value.startswith("-")):
        raise ValueError(f"{name} cannot contain control characters or start with '-' when it is a reason")
    if not value or (limit and len(value) > limit):
        raise ValueError(f"{name} must be nonempty" + (f" and at most {limit} characters" if limit else ""))
    return value


@dataclass
class Goal:
    gid: str
    title: str
    body: str
    state: dict = field(default_factory=dict)

    @property
    def origin(self) -> str:
        return self.state.get("origin", "said")

    @property
    def words(self) -> str:
        match = WORDS.search(self.body)
        return match[1] if match else ""

    @property
    def said_at(self) -> str | None:
        match = WORDS.search(self.body)
        return match[2] if match and match[2] != "unknown" else None

    @property
    def evidence(self) -> list[dict]:
        out = []
        for match in EVIDENCE.finditer(self.body):
            text = match[2]
            link = re.search(r"(?: · )?\[\[([^]\n]+)\]\]$", text)
            out.append({"at": match[1], "text": text[:link.start()].rstrip() if link else text,
                        "link": link[1].split("|", 1)[0] if link else None})
        return out

    def _content(self, digest: str | None = None) -> str:
        state = {key: value for key, value in self.state.items() if key != "digest"}
        if digest is not None:
            state["digest"] = digest
        comment = "; ".join(f"{key}={value}" for key, value in state.items())
        return f"### {self.gid} — {self.title}\n<!-- brain: {comment} -->\n{self.body.rstrip()}\n"

    def digest(self) -> str:
        return hashlib.sha256(self._content().encode()).hexdigest()[:16]

    def render(self) -> str:
        return self._content(digest=self.digest())

    def public(self) -> dict:
        return {"id": self.gid, "title": self.title, "kind": self.state.get("kind"),
                "origin": self.origin, "weight": self.state.get("weight", "normal"),
                "state": self.state.get("state"), "by": self.state.get("by"),
                "supersededBy": self.state.get("superseded_by"),
                "words": self.words, "saidAt": self.said_at, "evidence": self.evidence[:5],
                "links": {name: [] for name in ("tasks", "works", "drafts", "ideas", "hubs")}}


def _chunk_end(text: str, start: int, end: int | None = None) -> int:
    """A blank before prose ends a block; section prose never belongs to its digest."""
    end = len(text) if end is None else end
    offset, blank = start, None
    for raw in text[start:end].splitlines(keepends=True):
        if re.match(r"^#{1,6} ", raw) or (raw.strip() and blank is not None and not raw.startswith("- ")):
            return blank if blank is not None else offset
        if not raw.strip():
            if blank is None:
                blank = offset
        else:
            blank = None
        offset += len(raw)
    return blank if blank is not None else end


def parse_register(text: str) -> tuple[list[tuple[str, int, int]], list[Goal]]:
    spans, goals = [], []
    for head in HEAD.finditer(text):
        end = _chunk_end(text, head.end() + (text[head.end():head.end()+1] == "\n"))
        chunk = text[head.end():end]
        comment = STATE_RE.search(chunk)
        goals.append(Goal(head[1], head[2], STATE_RE.sub("", chunk, count=1).strip() + "\n",
                          parse_state(comment[1]) if comment else {}))
        spans.append((head[1], head.start(), end))
    return spans, goals


def read(config: Config) -> tuple[dict, str]:
    path = config.vault_path / config.human_goals
    return fm.read(path) if path.exists() else ({"kind": "orientation", "tier": "hot"}, TEMPLATE)


def load(config: Config) -> list[Goal]:
    return parse_register(read(config)[1])[1]


def archived(config: Config) -> list[Goal]:
    folder = config.vault_path / config.agent("Goals Archive")
    return [goal for path in sorted(folder.glob("G-*.md")) for goal in parse_register(fm.read(path)[1])[1]]


def problems(config: Config) -> list[str]:
    body = read(config)[1]
    spans, entries = parse_register(body)
    out, seen = [], set()
    malformed = len(re.findall(r"^#{1,6} G-", body, re.M)) - len(spans)
    if malformed:
        out.append(f"{config.human_goals}: malformed goal heading; use `brain goals`, not hand edits")
    for goal in entries:
        prefix = f"{goal.gid}: "
        if goal.gid in seen:
            out.append(prefix + "duplicate goal id")
        seen.add(goal.gid)
        if goal.state.get("kind") not in KINDS:
            out.append(prefix + "kind must be reach or become")
        if goal.state.get("state") not in STATES:
            out.append(prefix + f"unknown goal state '{goal.state.get('state')}'")
        if goal.state.get("state") == "superseded" and (not ID.fullmatch(goal.state.get("superseded_by", ""))
                                                       or goal.state["superseded_by"] == goal.gid):
            out.append(prefix + "superseded needs a different goal in superseded_by")
        if goal.origin not in ORIGINS:
            out.append(prefix + "origin must be said or inferred")
        if goal.state.get("weight", "normal") not in WEIGHTS:
            out.append(prefix + "weight must be more, less, normal or pause")
        if goal.origin == "inferred" and not goal.evidence:
            out.append(prefix + "an inferred goal needs evidence")
        if goal.state.get("by") and goal.state.get("kind") != "reach":
            out.append(prefix + "a finish date belongs to a reach goal")
        if not goal.words or len(WORDS.findall(goal.body)) != 1:
            out.append(prefix + 'needs one dated **In their words:** line; use `brain goals`')
        try:
            if goal.said_at:
                day(goal.said_at)
            if goal.state.get("by"):
                day(goal.state["by"])
            for key in ("missed_asked", "updated", "owner_updated", "stale"):
                if goal.state.get(key):
                    day(goal.state[key])
            if goal.state.get("inferred"):
                datetime.fromisoformat(goal.state["inferred"])
            for evidence in goal.evidence:
                day(evidence["at"])
        except ValueError:
            out.append(prefix + "invalid date; use YYYY-MM-DD")
        if len(goal.evidence) > 10 or len(re.findall(r"\*\*Evidence:\*\*", goal.body)) != len(goal.evidence):
            out.append(prefix + "malformed or unbounded evidence; use `brain goals G-n evidence`")
        if goal.state.get("digest") != goal.digest():
            out.append(prefix + "hand-edited goal block; restore the last brain-written block from git, then use `brain goals`.")
    return out


def active(config: Config) -> list[Goal]:
    return [goal for goal in load(config) if goal.state.get("state") == "active"]


def due(config: Config) -> list[Goal]:
    return [goal for goal in active(config) if due_reasons(config, goal)]


def due_reasons(config: Config, goal: Goal) -> list[str]:
    today = config.today()
    reasons = []
    if goal.state.get("stale"):
        reasons.append(f"No grounded evidence for 30 days; flagged stale {goal.state['stale']}")
    if (goal.state.get("kind") == "reach" and goal.state.get("by", today.isoformat()) < today.isoformat()
            and (not goal.state.get("missed_asked")
                 or (today - date.fromisoformat(goal.state["missed_asked"])).days >= 14)):
        reasons.append(f"Past finish date {goal.state['by']}")
    return reasons


def require_active(config: Config, gid: str) -> str:
    if not any(goal.gid == gid for goal in active(config)):
        choices = ", ".join(f"{goal.gid} — {goal.title}" for goal in active(config)) or "none"
        raise ValueError(f"goal {gid!r} is unknown or not active. Use an active goal from `brain goals list`: {choices}. "
                         "Reopen it with `brain goals G-n reopen --why …` if the person is pursuing it again.")
    return gid


def is_routine() -> bool:
    return bool(os.environ.get("BRAIN_RUN", "").strip() in ("routine", "worker") or
                os.environ.get("DEMIURGE_TASK", "").strip() or
                os.environ.get("BRAIN_SPEAKER", "").split(":", 1)[0] in ("cron", "subagent"))


def guard(config: Config, gid: str | None, source: str, parent=None) -> None:
    """The harness explicitly identifies routines/workers; other sessions are conversations.
    A worker's follow-up to the task it is running was asked for when that task was: it passes."""
    if gid:
        require_active(config, gid)
    routine = is_routine()
    if routine and gid and not re.search(r'["“][^"”\n]+["”]', source) and not (parent and parent.handoff):
        goal = next(goal for goal in active(config) if goal.gid == gid)
        if goal.state.get("weight") == "pause":
            raise ValueError(f"{gid} is paused; no unasked work until the person changes its weight")
    if parent is not None:
        return
    if config.edition != "personal" or not routine or gid or re.search(r'["“][^"”\n]+["”]', source):
        return
    names = ", ".join(f"{goal.gid} — {goal.title}" for goal in active(config))
    reason = (f"{config.human_name}'s active goals: {names}. Name one with --goal G-n."
              if names else f"{config.human_name} has no active goals. Nothing unasked should be made until they have one.")
    raise ValueError("No goal, no unasked work. " + reason + " Asked work may use --source quoting the owner's actual words.")


def _state_path(config: Config):
    return config.vault_path / config.runtime_dir / "goals.json"


def capture(config: Config) -> dict:
    path = _state_path(config)
    return {"asked": None, "app": [], "refused": [], "corrections": {}, **(json.loads(path.read_text()) if path.exists() else {})}


def save_capture(config: Config, values: dict) -> None:
    path = _state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    fm.write_text(path, json.dumps(values, ensure_ascii=False, indent=2) + "\n")


def _owner(config: Config, from_app: bool = False) -> bool:
    platform, separator, ident = os.environ.get("BRAIN_SPEAKER", "").strip().partition(":")
    return from_app or bool(separator and ident and identity.is_owner(config, platform, (ident,)))


def _require_owner(config: Config, from_app: bool) -> None:
    if not _owner(config, from_app):
        raise ValueError("the intellect infers (--inferred) or sets aside; the person keeps, refuses or demotes. "
                         "Only the person changes weights or corrects work. "
                         "Use --from-app or an owner session (BRAIN_SPEAKER naming the owner).")


def _matching(title: str, words: str, entries, evidence: list[dict] | None = None) -> bool:
    def key(value):
        return " ".join(re.findall(r"\w+", value.casefold()))
    def tokens(value):
        return set(key(value).split()) - {"a", "an", "the", "to", "of", "for", "in", "on", "and", "my", "more"}
    def links(items):
        targets = [item["link"] for item in items if item.get("link")]
        targets += [m[1] for item in items for m in WIKILINK_RE.finditer(item["text"])]
        return {target.split("#", 1)[0].removesuffix(".md").casefold()
                for target in targets}
    for entry in entries:
        if key(title) == key(entry["title"]) or key(words) == key(entry["words"]):
            return True
        if evidence is not None:
            ours, theirs = tokens(title), tokens(entry["title"])
            if ours and theirs and (ours <= theirs or theirs <= ours):
                return True
            if links(evidence) & links(entry.get("evidence", [])):
                return True
    return False


def acknowledge(config: Config, events: list[dict]) -> None:
    values = capture(config)
    values["app"] = [event for event in values["app"] if event not in events]
    save_capture(config, values)


def notices(config: Config) -> str:
    return "\n".join(f"{config.human_name} {event['change']} this in the app: {event['text']}"
                     for event in capture(config)["app"])


def context(config: Config, *, app_changes: bool = True) -> str:
    lines = []
    corrections = capture(config)["corrections"]
    for goal in active(config):
        latest = goal.evidence[0] if goal.evidence else None
        lines.append(f"- {goal.gid} — {goal.title} ({goal.origin}) (weight: {goal.state.get('weight', 'normal')})" + (f" (by {goal.state['by']})" if goal.state.get("by") else ""))
        if latest:
            lines.append(f"  Latest evidence: {latest['at']} · {latest['text']}" + (f" · [[{latest['link']}]]" if latest['link'] else ""))
        for correction in corrections.get(goal.gid, {}).values():
            lines.append(f"  Not this ({correction['kind']}): {correction['text']} — {correction['why'] or 'Do not repeat this kind of work'}")
    if config.edition == "enterprise":
        lines.insert(0, f"{config.human_name}'s goals: where its leadership says it is heading. Unasked work that serves one names it "
                        "with --goal G-n; a goal grants no outward permission." if lines else
                     f"No company goals yet. The seed files the goals {config.human_name}'s leadership states, for the operator to "
                     "keep; until then unasked work follows initiatives and assigned work.")
    elif not lines:
        lines.append("No active goals. The next people pass may infer from repeated, dated evidence; make nothing unasked until then, and never ask whether something is a goal.")
    for entry in capture(config)["refused"]:
        lines.append(f"Refused goal; never re-propose: {entry['title']} — {entry['words']}")
    if app_changes and (notice := notices(config)):
        lines.append(notice)
    return "\n".join(lines)


def week(today: date) -> str:
    year, number, _ = today.isocalendar()
    return f"{year}-W{number:02}"


def account(goal: Goal, today: date) -> dict:
    saved = {match[1]: match[2] for match in ACCOUNT.finditer(goal.body)}
    return {name: {"week": week(at), "text": saved.get(week(at))}
            for name, at in (("thisWeek", today), ("lastWeek", today - timedelta(weeks=1)))}


def activity(config: Config, gid: str | None = None, *, weeks: int | None = 2, vault: Vault | None = None) -> dict:
    from . import goal_activity
    return goal_activity.build(config, gid, weeks=weeks, vault=vault)


def steer(config: Config, gid: str, operation: str, text: str, *, why: str | None = None,
          from_app: bool = False, account_week: str | None = None) -> Goal:
    if operation in ("weight", "correct"):
        _require_owner(config, from_app)
    if account_week is not None and operation != "account":
        raise ValueError("--week goes with account")
    if operation == "account":
        people = (os.environ.get("BRAIN_RUN", "").strip() == "routine" and
                  (os.environ.get("BRAIN_SKILL", "").strip() in ("people", config.skill_prefix + "people") or
                   os.environ.get("BRAIN_SPEAKER", "").strip() in ("cron:people", "cron:" + config.skill_prefix + "people")))
        if not (_owner(config) or people):
            raise ValueError("only the owner or the people routine may write a goal account")
    if errors := problems(config):
        raise ValueError(errors[0])
    data, body = read(config)
    goal = next((entry for entry in load(config) if entry.gid == gid), None)
    if goal is None:
        raise ValueError(f"{gid} is not a goal; use `brain goals list`")
    if operation == "account":
        text = line("account", text, 2000)
        if any(marker in text for marker in ("<!--", "-->")):
            raise ValueError("account cannot contain HTML comment delimiters")
        if re.search(r"\b(?:G-\d+|T-\d{3,})\b|\[\[|\.md|[/\\]", text, re.I):
            raise ValueError("account must be plain text without goal/task ids, wikilinks, .md or paths")
        now = config.local()
        current = account_week if account_week is not None else week(now.date() - timedelta(days=now.hour < 12))
        try:
            if not re.fullmatch(r"\d{4}-W\d{2}", current):
                raise ValueError
            date.fromisocalendar(int(current[:4]), int(current[6:]), 1)
        except ValueError:
            raise ValueError("--week must be a valid ISO week: YYYY-Www") from None
        saved = {match[1]: match[2] for match in ACCOUNT.finditer(goal.body)}
        saved[current] = text
        latest = max(saved)
        cutoff = week(date.fromisocalendar(int(latest[:4]), int(latest[6:]), 1) - timedelta(weeks=7))
        older = [f"- **Account ({at}):** {value}" for at, value in sorted(saved.items()) if at < cutoff]
        if older:
            path = config.vault_path / config.agent(f"Goals Archive/{gid}.md")
            previous = fm.read(path)[1] if path.exists() else f"# {gid} — {goal.title}\n"
            path.parent.mkdir(parents=True, exist_ok=True)
            fm.write(path, {"kind": "log", "tier": "cold"}, previous.rstrip() + "\n\n" + "\n".join(older) + "\n")
        goal.body = ACCOUNT.sub("", goal.body).rstrip() + "\n"
        goal.body += "\n".join(f"- **Account ({at}):** {value}" for at, value in sorted(saved.items(), reverse=True) if at >= cutoff) + "\n"
        return _write(config, data, body, goal)
    if operation == "weight":
        if text not in WEIGHTS:
            raise ValueError("weight must be more, less, normal or pause")
        goal.state["weight"] = text
        result = _write(config, data, body, goal)
        detail = f"weight: {text}"
    elif operation == "correct":
        if not ITEM_ID.fullmatch(text):
            raise ValueError("item id must match [A-Za-z0-9._:-]{1,80}")
        reason = line("why", why, 200) if why is not None else ""
        item = next((item for item in activity(config, gid, weeks=None)[gid] if item["id"] == text), None)
        if item is None:
            raise ValueError(f"{text} is not an activity item for {gid}")
        from . import reception, tasks
        if item["kind"] == "task":
            task = tasks.find(config, text, archived_closed=True)
            if task is not None and tasks.is_open(task) and task.task_state != "set_aside":
                tasks.set_aside(config, task.tid, config.today(), reason or "Not this")
        from .goal_activity import receptions_for
        for row in receptions_for(config, item):
            reception.set_outcome(config, row.id, "declined:" + (reason or "not-this"))
        values = capture(config)
        values["corrections"].setdefault(gid, {})[text] = {
            "at": config.now().isoformat(), "why": reason, "kind": item["kind"], "text": item["text"]}
        save_capture(config, values)
        result, detail = goal, f"Not this: {item['text']}" + (f" — {reason}" if reason else "")
    else:
        raise ValueError("unknown goal operation")
    if from_app:
        _app_change(config, goal, "changed", detail)
    else:
        _refresh_context(config)
    return result


def _refresh_context(config: Config) -> None:
    from . import compile_context
    compile_context.write(Vault(config.vault_path))


def _write(config: Config, data: dict, body: str, goal: Goal, *, new: bool = False, from_app: bool = False) -> Goal:
    spans, _ = parse_register(body)
    span = next(((start, end) for gid, start, end in spans if gid == goal.gid), None)
    destination = {"active": "Active goals", "reached": "Reached", "set_aside": "Set aside",
                   "superseded": "Superseded"}[goal.state["state"]]
    if span:
        sections = list(re.finditer(r"^## (.+?)\s*$", body[:span[0]], re.M))
        if not sections or sections[-1][1] != destination:
            body = body[:span[0]] + body[span[1]:]
            span = None
    if span:
        start, end = span
        body = body[:start] + goal.render() + body[end:]
    else:
        section = re.search(r"^## " + re.escape(destination) + r"[ \t]*$", body, re.M)
        if section is None:
            separator = "" if body.endswith("\n\n") else "\n" if body.endswith("\n") else "\n\n"
            body += separator + f"## {destination}\n"
            section = re.search(r"^## " + re.escape(destination) + r"[ \t]*$", body, re.M)
        following = re.search(r"^## ", body[section.end():], re.M)
        end = section.end() + following.start() if following else len(body)
        blocks = [stop for _, start, stop in parse_register(body)[0] if section.end() <= start < end]
        pos = blocks[-1] if blocks else end
        before, after = body[:pos], body[pos:]
        separator = "" if before.endswith("\n\n") else "\n" if before.endswith("\n") else "\n\n"
        body = before + separator + goal.render() + ("" if after.startswith("\n") else "\n") + after
    path = config.vault_path / config.human_goals
    path.parent.mkdir(parents=True, exist_ok=True)
    fm.write(path, data, body)
    if from_app:
        _app_change(config, goal, "added" if new else "changed")
    return goal


def _app_change(config: Config, goal: Goal, change: str, detail: str = "") -> None:
    values = capture(config)
    values["app"].append({"change": change,
                          "text": f"{goal.gid} — {goal.title} ({goal.state['kind']}, {goal.state['state']}"
                                  + (f", by {goal.state['by']}" if goal.state.get("by") else "") + f"): {goal.words}"
                                  + (f"; {detail}" if detail else ""),
                          "at": config.now().isoformat()})
    save_capture(config, values)
    # The harness may reuse .context.md between sessions. Refresh its active
    # goals now; transient notices travel in session-context, never the cache.
    from . import compile_context
    from .vault import Vault
    compile_context.write(Vault(config.vault_path))


def _said(config: Config, said: str | None) -> str:
    """When the person said it: a stated past date, never one in the future; today when not given."""
    if said is None:
        return config.today().isoformat()
    when = day(said)
    if date.fromisoformat(when) > config.today():
        raise ValueError("--said is when they said it, so it can't be in the future")
    return when


def new(config: Config, title: str, words: str, *, reach: bool = False, by: str | None = None,
        said: str | None = None, inferred: bool = False, evidence: list[str] | None = None,
        from_app: bool = False) -> Goal:
    if not inferred:
        _require_owner(config, from_app)
    if errors := problems(config):
        raise ValueError(errors[0])
    title, words = line("title", title, 80), line("words", words, 500)
    evidence_lines = [_evidence(config, value, inferred=inferred) for value in evidence or []]
    entries = load(config) + archived(config)
    now = config.now()
    if inferred:
        if not evidence_lines:
            raise ValueError("--inferred needs at least one inline --evidence '<date> · <what> · [[note]]'")
        if _matching(title, words, capture(config)["refused"], Goal("", title, "\n".join(evidence_lines)).evidence):
            raise ValueError("this reading was refused; never re-propose it")
        if _matching(title, words, [goal.public() for goal in entries]):
            raise ValueError("this goal already exists; add evidence to it instead")
        if sum(goal.origin == "inferred" for goal in active(config)) >= 5:
            raise ValueError("at most 5 active inferred goals; add evidence to an existing goal instead")
        # A company's goals are read from what its leadership states, often several at once; pacing is for guesses about a person.
        if config.edition == "personal" and sum(now - timedelta(hours=24) < config.local(datetime.fromisoformat(goal.state["inferred"])) <= now
               for goal in entries if goal.state.get("inferred")) >= 2:
            raise ValueError("at most 2 new inferred goals per 24 hours, counting readings made earlier today")
    if by is not None and not reach:
        raise ValueError("--by needs --reach: a finish date belongs to a reach goal")
    state = {"kind": "reach" if reach else "become", "state": "active", "origin": "inferred" if inferred else "said"}
    if inferred:
        state["inferred"] = now.isoformat()
    if by is not None:
        state["by"] = day(by)
    gid = f"G-{max((int(goal.gid[2:]) for goal in entries), default=0) + 1}"
    goal = Goal(gid, title, f'- **In their words:** "{words}" ({_said(config, said)})\n', state)
    for value in evidence_lines:
        _add_evidence(config, goal, value)
    return _write(config, *read(config), goal, new=True, from_app=from_app)


ROW = re.compile(r"\bevidence:([a-z][a-z0-9-]*)\b")
ROW_DAYS = 3   # a goal's evidence line may summarize a source's own rows from the few days before it


def _grounded(config: Config, at: str, text: str, vault: Vault | None = None) -> bool:
    """Evidence an inferred goal may rest on: a real note outside the intellect's own folders, or a synced source's
    own rows (`evidence:<source>`, as portrait lines cite them) from that day or the days just before."""
    if _has_evidence_note(config, text, vault):
        return True
    day_ = date.fromisoformat(at)
    return any(row.whose == "own" and 0 <= (day_ - row.date).days <= ROW_DAYS
               for match in ROW.finditer(text)
               for row in rows.read(match[1], since=day_ - timedelta(days=ROW_DAYS)))


def _has_evidence_note(config: Config, text: str, vault: Vault | None = None) -> bool:
    vault = vault or Vault(config.vault_path)
    agent = (config.vault_path / config.agent_dir).resolve()
    for match in WIKILINK_RE.finditer(text):
        rel = vault.resolve(match[1])
        if rel:
            path = (config.vault_path / rel).resolve()
            note = vault.notes.get(rel)
            if (path.suffix == ".md" and path.is_file() and not path.is_relative_to(agent)
                    and vault.kind_for(rel) != "draft" and not (note and note.data.get("kind") == "draft")):
                return True
    return False


def _evidence(config: Config, text: str, *, inferred: bool = False) -> str:
    text = line("evidence", text)
    match = re.match(r"^(\d{4}-\d{2}-\d{2})(?: · |\s+)(.+)$", text)
    if inferred and not match:
        raise ValueError("inferred evidence needs a date: YYYY-MM-DD · <what> · [[note]]")
    at, text = (day(match[1]), line("evidence", match[2])) if match else (config.today().isoformat(), text)
    if at > config.today().isoformat():
        raise ValueError("evidence cannot be in the future")
    if inferred and not _grounded(config, at, text):
        raise ValueError("inferred evidence needs a [[link]] to an existing note outside the agent directory and prepared "
                         "drafts, or evidence:<source> naming a synced source with the person's own rows from that day "
                         "or the three before (`brain evidence --source <source> --since <date>`)")
    return f"- **Evidence:** {at} · {text}"


def _add_evidence(config: Config, goal: Goal, text: str) -> None:
    lines = list(dict.fromkeys([text] + [m[0] for m in EVIDENCE.finditer(goal.body)]))
    lines.sort(key=lambda value: value[len("- **Evidence:** "):][:10], reverse=True)
    if len(lines) > 10:
        archive = config.vault_path / config.agent(f"Goals Archive/{goal.gid}.md")
        archive.parent.mkdir(parents=True, exist_ok=True)
        old = archive.read_text() if archive.exists() else f"# {goal.gid} — {goal.title}\n\nEarlier evidence.\n\n"
        fm.write(archive, {}, old.rstrip() + "\n" + "\n".join(lines[10:]) + "\n")
        if "**Earlier evidence:**" not in goal.body:
            goal.body += f"- **Earlier evidence:** [[{config.link(config.agent(f'Goals Archive/{goal.gid}'))}|{goal.gid} archive]]\n"
    goal.body = "\n".join(value for value in goal.body.splitlines() if value.strip() and not EVIDENCE.fullmatch(value))
    goal.body += "\n" + "\n".join(lines[:10]) + "\n"
    goal.state.pop("nudged", None)
    match = EVIDENCE.fullmatch(text)
    if (goal.state.get("stale") and (config.today() - date.fromisoformat(match[1])).days < 30
            and _grounded(config, match[1], match[2])):
        goal.state.pop("stale", None)


def change(config: Config, gid: str, operation: str, *, text: str | None = None, title: str | None = None,
           words: str | None = None, by: str | None = None, clear_by: bool = False, kind: str | None = None,
           why: str | None = None, said: str | None = None, evidence: list[str] | None = None,
           from_app: bool = False) -> Goal | Task:
    if operation in ("keep", "not-a-goal", "to-task"):
        _require_owner(config, from_app)
    if errors := problems(config):
        raise ValueError(errors[0])
    data, body = read(config)
    goal = next((goal for goal in load(config) if goal.gid == gid), None)
    if goal is None:
        raise ValueError(f"{gid} is not a goal; use `brain goals list`")
    today = config.today().isoformat()
    if operation == "superseded-by" and goal.state["state"] != "active":
        raise ValueError("only an active goal can be superseded; reopen it first")
    if operation in ("reached", "superseded-by", "set-aside") and not _owner(config, from_app):
        updated = goal.state.get("owner_updated")
        if updated and (config.today() - date.fromisoformat(updated)).days < 14:
            raise ValueError("the person reopened or edited this goal within 14 days; leave it active")
        if sum(any(match[1] == today for match in ENDING.finditer(entry.body))
               for entry in load(config) + archived(config)) >= 3:
            raise ValueError("three goals already have an ending dated today; leave further endings for another night")
    if operation in ("edit", "reopen") and _owner(config, from_app):
        goal.state["origin"] = "said"
        goal.state.pop("stale", None)
        goal.state["owner_updated"] = today
    if operation == "edit":
        if title is not None:
            goal.title = line("title", title, 80)
        if words is not None or said is not None:
            current = WORDS.search(goal.body)
            text = line("words", words, 500) if words is not None else (current[1] if current else "")
            when = _said(config, said) if said is not None or words is not None else current[2]
            goal.body = WORDS.sub(lambda _: f'- **In their words:** "{text}" ({when})', goal.body)
        if kind:
            if kind not in KINDS:
                raise ValueError("kind must be reach or become")
            goal.state["kind"] = kind
        if by is not None:
            goal.state["by"] = day(by)
            if kind is None:
                goal.state["kind"] = "reach"
        if clear_by:
            goal.state.pop("by", None)
        if goal.state.get("by") and goal.state["kind"] != "reach":
            raise ValueError("a finish date needs kind reach; use --kind reach or --clear-by")
        goal.state.pop("nudged", None)
        goal.state["updated"] = today
    elif operation == "evidence":
        _add_evidence(config, goal, _evidence(config, text or "", inferred=goal.origin == "inferred"))
    elif operation == "keep":
        goal.state["origin"] = "said"
        goal.state.pop("stale", None)
        goal.state["updated"] = today
        goal.state["owner_updated"] = today
        goal.state.pop("nudged", None)
        goal.body += f"- **Kept:** {today} — you kept it\n"
    elif operation in ("not-a-goal", "to-task"):
        reason = line("why", why, 200) if why is not None else "Not a goal"
        task = _to_task(config, goal) if operation == "to-task" else None
        if task:
            reason = f"became {task.tid}"
        else:
            _detach_tasks(config, goal)
        values = capture(config)
        values["refused"].append({"id": gid, "title": goal.title, "words": goal.words, "evidence": goal.evidence,
                                  "why": reason, "at": today})
        save_capture(config, values)
        _archive(config, data, body, goal, reason)
        if from_app:
            _app_change(config, goal, "changed", reason)
        return task or goal
    elif operation == "nudge":
        touched = max([value for value in (goal.said_at, goal.state.get("updated")) if value] + [ev["at"] for ev in goal.evidence], default=today)
        if goal.state["state"] != "active" or goal.state.get("nudged") or (config.today() - date.fromisoformat(touched)).days < 60:
            raise ValueError("nudge needs an active goal with no evidence for 60 days, not already nudged")
        goal.state["nudged"] = today
    elif operation == "asked-missed":
        if goal.state["state"] != "active" or goal.state["kind"] != "reach" or goal.state.get("by", today) >= today:
            raise ValueError("asked-missed needs an active reach goal past its finish date")
        goal.state["missed_asked"] = today
    elif operation == "superseded-by":
        if text == gid:
            raise ValueError("a goal must be superseded by a different active goal")
        require_active(config, text or "")
        target = next(entry for entry in load(config) if entry.gid == text)
        reason = line("why", why, 200) if why is not None else f"Replaced by {target.gid}"
        evidence_lines = _ending_evidence(config, goal, evidence, from_app)
        for value in evidence_lines:
            _add_evidence(config, goal, value)
        for match in reversed(list(EVIDENCE.finditer(goal.body))):
            _add_evidence(config, target, match[0])
        _relink(config, gid, target.gid)
        goal.state["state"] = "superseded"
        goal.state.pop("stale", None)
        goal.state["superseded_by"] = target.gid
        goal.state.pop("nudged", None)
        goal.body += f"- **Became:** [[{config.link(config.human_goals)}#{target.gid} — {target.title}]] {target.gid} — {target.title}\n"
        goal.body += f"- **Superseded:** {today} — {reason}\n"
        target.body += f"- **Earlier as:** {gid} — {goal.title}\n"
        _write(config, data, body, target)
        data, body = read(config)
    elif operation in ("reached", "set-aside", "reopen"):
        why = line("why", why or ("Reached" if operation == "reached" else ""), 200)
        if operation == "reopen" and goal.state["state"] != "active" and goal.origin == "inferred" and sum(g.origin == "inferred" for g in active(config)) >= 5:
            raise ValueError("at most 5 active inferred goals; set aside another first")
        if operation == "reached":
            for value in _ending_evidence(config, goal, evidence, from_app):
                _add_evidence(config, goal, value)
        if operation == "reopen" and _owner(config, from_app):
            goal.state["updated"] = today
        goal.state.pop("superseded_by", None)
        goal.state["state"] = {"reached": "reached", "set-aside": "set_aside", "reopen": "active"}[operation]
        if operation != "reopen":
            goal.state.pop("stale", None)
        goal.state.pop("nudged", None)
        goal.body += f"- **{operation.title()}:** {today} — {why}\n"
    else:
        raise ValueError("unknown goal operation")
    return _write(config, data, body, goal, from_app=from_app)


def _ending_evidence(config: Config, goal: Goal, evidence: list[str] | None, from_app: bool) -> list[str]:
    intellect = not _owner(config, from_app)
    if intellect and not evidence:
        raise ValueError("an intellect ending a goal needs --evidence '<date> · <what> · [[note]] or evidence:<source>'")
    lines = [_evidence(config, value, inferred=intellect) for value in evidence or []]
    if intellect:
        existing = {match[0] for match in EVIDENCE.finditer(goal.body)}
        latest = max((ev["at"] for ev in goal.evidence),
                     default=goal.said_at or goal.state.get("inferred", "")[:10])
        for value in lines:
            if value in existing:
                raise ValueError("an intellect ending a goal needs new evidence, not a line already on the goal")
            if EVIDENCE.fullmatch(value)[1] < latest:
                raise ValueError("ending evidence cannot predate the goal's latest evidence or said/inferred date")
    return lines


def _relink(config: Config, gid: str, target: str) -> None:
    from . import runtime, tasks, works
    slugs = set()
    for task in tasks.all_tasks(config):
        if task.goal == gid and tasks.is_open(task):
            tasks.set_fields(config, task.tid, config.today(), values={"goal": target})
            if task.work:
                slugs.add(task.work)
    vault = Vault(config.vault_path)
    for rel, note in vault.notes.items():
        if note.data.get("kind") == "draft" or vault.kind_for(rel) == "draft":
            if (note.data.get("goal") or fm.field(note.body, "Goal")) != gid:
                continue
            body = tasks._replace_field(note.body, "goal", target) if fm.field(note.body, "Goal") else note.body
            fm.write(config.vault_path / rel, {**note.data, "goal": target}, body)
        elif vault.kind_for(rel) == "hub" and fm.hub_goal(note.body) == gid:
            section = re.search(r"^#{2,6} ", note.body, re.M)
            end = section.start() if section else len(note.body)
            body = tasks._replace_field(note.body[:end], "goal", target) + note.body[end:]
            fm.write(config.vault_path / rel, note.data, body)
            if note.data.get("work"):
                slugs.add(note.data["work"])
    library = works.parse(works.library() or "")
    work_goals = {work.slug: work.goal for work in library}
    slugs.update(work.slug for work in library if work.status in ("active", "draft", "kept"))
    home = runtime.configured()
    for slug in sorted(slugs):
        if (home is not None and works.SLUG.fullmatch(slug) and (home / "works" / slug).is_dir()
                and (works.goal_for(slug) or work_goals.get(slug)) == gid):
            works.set_goal(config, slug, target)


def _archive(config: Config, data: dict, body: str, goal: Goal, reason: str) -> None:
    goal.state["state"] = "set_aside"
    goal.body += f"- **Archived:** {config.today().isoformat()} — {reason}\n"
    path = config.vault_path / config.agent(f"Goals Archive/{goal.gid}.md")
    previous = fm.read(path)[1] if path.exists() else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    fm.write(path, {"kind": "log", "tier": "cold"}, previous.rstrip() + "\n\n" + goal.render())
    _, start, end = next(span for span in parse_register(body)[0] if span[0] == goal.gid)
    fm.write(config.vault_path / config.human_goals, data, body[:start] + body[end:])


def _to_task(config: Config, goal: Goal) -> Task:
    from . import runtime, tasks, works
    from .vault import Vault
    linked = [task for task in tasks.all_tasks(config) if task.goal == goal.gid]
    slugs = {task.work for task in linked if task.work}
    drafts = []
    vault = Vault(config.vault_path)
    for rel, note in vault.notes.items():
        if note.data.get("kind") == "draft" or vault.kind_for(rel) == "draft":
            if (note.data.get("goal") or fm.field(note.body, "Goal")) == goal.gid:
                drafts.append((rel, note))
        elif fm.hub_goal(note.body) == goal.gid and note.data.get("work"):
            slugs.add(note.data["work"])
    slugs.update(work.slug for work in works.parse(works.library() or "")
                 if (works.goal_for(work.slug) or work.goal) == goal.gid)
    values = {}
    if goal.state["kind"] == "reach":
        values["delivers"] = goal.words + (f" by {goal.state['by']}" if goal.state.get("by") else "")
    task = tasks.new_task(config, goal.title, f'"{goal.words}"', values=values,
                          work=next(iter(sorted(slugs)), None), inherit_goal=False)
    _detach_tasks(config, goal)
    for rel, note in drafts:
        fm.write(config.vault_path / rel, {**note.data, "task": task.tid}, note.body)
    home = runtime.configured()
    for slug in slugs:
        if home is None or not works.SLUG.fullmatch(slug):
            continue
        path = home / "works" / slug / "GOAL.md"
        if path.parent.is_dir():
            data, body = fm.read(path) if path.exists() else ({}, "")
            fm.write(path, {**data, "goal": goal.gid, "task": task.tid}, body)
    return task


def _detach_tasks(config: Config, goal: Goal) -> None:
    from . import tasks
    for task in tasks.all_tasks(config):
        if task.goal == goal.gid and tasks.is_open(task):
            tasks.set_fields(config, task.tid, config.today(), clear=("goal",))


def decay(config: Config, today: date, report: list[str] | None = None, *, vault: Vault | None = None) -> list[str]:
    """Flag inferred goals for people to judge; only grounded evidence renews them."""
    changed = []
    report = report if report is not None else []
    if errors := problems(config):
        report.append("goal: decay skipped: " + "; ".join(errors))
        return changed
    vault = vault or Vault(config.vault_path)
    for goal in active(config):
        if goal.origin != "inferred":
            continue
        try:
            latest = max((ev["at"] for ev in goal.evidence if _grounded(
                config, ev["at"], ev["text"] + (f" [[{ev['link']}]]" if ev["link"] else ""), vault)),
                default=goal.said_at or today.isoformat())
            stale = (today - date.fromisoformat(latest)).days >= 30
            if stale != bool(goal.state.get("stale")):
                if stale:
                    goal.state["stale"] = today.isoformat()
                else:
                    goal.state.pop("stale", None)
                _write(config, *read(config), goal)
                changed.append(goal.gid)
                if not stale:
                    report.append(f"goal: {goal.gid} stale mark cleared by grounded evidence")
            if stale:
                report.append(f"goal: {goal.gid} stale: no grounded evidence for 30 days; people to judge")
        except Exception as error:
            report.append(f"goal: {goal.gid} decay skipped: {error}")
    return changed


def link_note(config: Config, gid: str, note: str) -> None:
    """Hubs and prepared drafts keep their own formats; validate the edge before writing it."""
    from .tasks import note_name, _replace_field
    from .vault import Vault
    require_active(config, gid)
    rel = note_name(config, note) + ".md"
    path = config.vault_path / rel
    data, body = fm.read(path)
    if data.get("kind") == "draft" or Vault(config.vault_path).kind_for(rel) == "draft":
        data["goal"] = gid
    elif Vault(config.vault_path).kind_for(rel) == "hub":
        section = re.search(r"^## ", body, re.M)
        end = section.start() if section else len(body)
        body = _replace_field(body[:end], "goal", gid).rstrip() + "\n\n" + body[end:]
    else:
        raise ValueError("link needs a hub or a prepared draft (kind: draft)")
    fm.write(path, data, body)

