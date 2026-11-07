"""The person's goals. One writer for blocks, evidence archives and capture state.

Mutation helpers are lock-free; their CLI caller holds the vault lock.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import date

from . import fm, identity
from .config import Config
from .tasks import STATE_RE, parse_state, single_line

ID = re.compile(r"G-\d+")
HEAD = re.compile(r"^### (G-\d+) — (.+?)[ \t]*$", re.M)
STATES = ("active", "reached", "set_aside")
KINDS = ("reach", "become")
WORDS = re.compile(r'^- \*\*In their words:\*\* "(.*)" \((\d{4}-\d{2}-\d{2}|unknown)\)$', re.M)
EVIDENCE = re.compile(r"^- \*\*Evidence:\*\* (\d{4}-\d{2}-\d{2}) · (.+)$", re.M)
ACTIVE_DESCRIPTION = "What the owner has chosen to cultivate: called a goal in their words, or returned to on a second day."
TEMPLATE = "# Goals\n\n## Aspirations\n\n## Active goals\n\n## Adopted practices\n\n## Set aside\n"
QUESTION = "What are you working toward right now? Anything: a race, an exam, a habit, a skill. I'll keep it in mind, and it's how I decide what to do without being asked."


def day(value: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("use a date: YYYY-MM-DD")
    return date.fromisoformat(value).isoformat()


def line(name: str, value: str, limit: int | None = None) -> str:
    value = single_line(name, value)
    if name in ("title", "words") and any(marker in value for marker in ("<!--", "-->")):
        raise ValueError(f"{name} cannot contain HTML comment delimiters (<!-- or -->); use plain text such as an arrow (→) instead")
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
                "state": self.state.get("state"), "by": self.state.get("by"),
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
        if goal.state.get("by") and goal.state.get("kind") != "reach":
            out.append(prefix + "a finish date belongs to a reach goal")
        if not goal.words or len(WORDS.findall(goal.body)) != 1:
            out.append(prefix + 'needs one dated **In their words:** line; use `brain goals`')
        try:
            if goal.said_at:
                day(goal.said_at)
            if goal.state.get("by"):
                day(goal.state["by"])
            for evidence in goal.evidence:
                day(evidence["at"])
        except ValueError:
            out.append(prefix + "invalid date; use YYYY-MM-DD")
        if len(goal.evidence) > 10 or len(re.findall(r"\*\*Evidence:\*\*", goal.body)) != len(goal.evidence):
            out.append(prefix + "malformed or unbounded evidence; use `brain goals G-n evidence`")
        if goal.state.get("digest") != goal.digest():
            out.append(prefix + "hand-edited goal block; restore the last brain-written block from git, then use `brain goals`. "
                       "For legacy text use `brain goals migrate --dry-run`.")
    return out


def active(config: Config) -> list[Goal]:
    return [goal for goal in load(config) if goal.state.get("state") == "active"] if config.edition == "personal" else []


def require_active(config: Config, gid: str) -> str:
    if not any(goal.gid == gid for goal in active(config)):
        choices = ", ".join(f"{goal.gid} — {goal.title}" for goal in active(config)) or "none"
        raise ValueError(f"goal {gid!r} is unknown or not active. Use an active goal from `brain goals list`: {choices}. "
                         "Reopen it with `brain goals G-n reopen --why …` if the person is pursuing it again.")
    return gid


def guard(config: Config, gid: str | None, source: str, parent=None) -> None:
    """The harness explicitly identifies routines/workers; other sessions are conversations.
    A worker's follow-up to the task it is running was asked for when that task was: it passes."""
    if gid:
        require_active(config, gid)
    if parent is not None:
        return
    routine = bool(os.environ.get("BRAIN_RUN", "").strip() in ("routine", "worker") or
                   os.environ.get("DEMIURGE_TASK", "").strip() or
                   os.environ.get("BRAIN_SPEAKER", "").split(":", 1)[0] in ("cron", "subagent"))
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
    return json.loads(path.read_text()) if path.exists() else {"asked": None, "app": []}


def save_capture(config: Config, values: dict) -> None:
    path = _state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    fm.write(path, {}, json.dumps(values, ensure_ascii=False, indent=2) + "\n")


def asked(config: Config) -> bool:
    platform, separator, ident = os.environ.get("BRAIN_SPEAKER", "").strip().partition(":")
    if not separator or not ident or not (platform == "cron" or identity.is_owner(config, platform, (ident,))):
        raise ValueError(f"the capture question is only for {config.human_name}, the owner; run it in their session (BRAIN_SPEAKER naming the owner) or the morning briefing (cron)")
    values = capture(config)
    if values["asked"] or active(config) or config.edition != "personal":
        return False
    values["asked"] = config.today().isoformat()
    save_capture(config, values)
    return True


def acknowledge(config: Config, events: list[dict]) -> None:
    values = capture(config)
    values["app"] = [event for event in values["app"] if event not in events]
    save_capture(config, values)


def notices(config: Config) -> str:
    return "\n".join(f"{config.human_name} {event['change']} this in the app: {event['text']}"
                     for event in capture(config)["app"])


def context(config: Config, *, app_changes: bool = True) -> str:
    lines = []
    for goal in active(config):
        latest = goal.evidence[0] if goal.evidence else None
        lines.append(f"- {goal.gid} — {goal.title}" + (f" (by {goal.state['by']})" if goal.state.get("by") else ""))
        if latest:
            lines.append(f"  Latest evidence: {latest['at']} · {latest['text']}" + (f" · [[{latest['link']}]]" if latest['link'] else ""))
    if not lines:
        lines.append("No active goals. Nothing unasked should be made until there is one.")
        if not capture(config)["asked"]:
            lines.append(f"Ask {config.human_name} once, only in their own conversation or morning briefing; claim with `brain goals asked` before asking: " + QUESTION)
    if app_changes and (notice := notices(config)):
        lines.append(notice)
    return "\n".join(lines)


def _write(config: Config, data: dict, body: str, goal: Goal, *, new: bool = False, from_app: bool = False) -> Goal:
    spans, _ = parse_register(body)
    span = next(((start, end) for gid, start, end in spans if gid == goal.gid), None)
    destination = {"active": "Active goals", "reached": "Reached", "set_aside": "Set aside"}[goal.state["state"]]
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
        values = capture(config)
        values["app"].append({"change": "added" if new else "changed",
                              "text": f"{goal.gid} — {goal.title} ({goal.state['kind']}, {goal.state['state']}"
                                      + (f", by {goal.state['by']}" if goal.state.get("by") else "") + f"): {goal.words}",
                              "at": config.now().isoformat()})
        save_capture(config, values)
        # The harness may reuse .context.md between sessions. Refresh its active
        # goals now; transient notices travel in session-context, never the cache.
        from . import compile_context
        from .vault import Vault
        compile_context.write(Vault(config.vault_path))
    return goal


def new(config: Config, title: str, words: str, *, reach: bool = False, by: str | None = None,
        from_app: bool = False) -> Goal:
    if config.edition != "personal":
        raise ValueError("goals belong to a person; organisation aims belong in initiatives")
    if errors := problems(config):
        raise ValueError(errors[0])
    title, words = line("title", title, 80), line("words", words, 500)
    if by is not None and not reach:
        raise ValueError("--by needs --reach: a finish date belongs to a reach goal")
    state = {"kind": "reach" if reach else "become", "state": "active"}
    if by is not None:
        state["by"] = day(by)
    gid = f"G-{max((int(goal.gid[2:]) for goal in load(config)), default=0) + 1}"
    goal = Goal(gid, title, f'- **In their words:** "{words}" ({config.today()})\n', state)
    return _write(config, *read(config), goal, new=True, from_app=from_app)


def change(config: Config, gid: str, operation: str, *, text: str | None = None, title: str | None = None,
           words: str | None = None, by: str | None = None, clear_by: bool = False, kind: str | None = None,
           why: str | None = None, from_app: bool = False) -> Goal:
    if errors := problems(config):
        raise ValueError(errors[0])
    data, body = read(config)
    goal = next((goal for goal in load(config) if goal.gid == gid), None)
    if goal is None:
        raise ValueError(f"{gid} is not a goal; use `brain goals list`")
    today = config.today().isoformat()
    if operation == "edit":
        if title is not None:
            goal.title = line("title", title, 80)
        if words is not None:
            goal.body = WORDS.sub(lambda _: f'- **In their words:** "{line("words", words, 500)}" ({today})', goal.body)
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
        text = line("evidence", text or "")
        # Accept the dated line used in the design, or plain evidence dated today.
        match = re.match(r"^(\d{4}-\d{2}-\d{2})(?: · |\s+)(.+)$", text)
        at, text = (day(match[1]), match[2]) if match else (today, text)
        lines = [f"- **Evidence:** {at} · {text}"] + [m[0] for m in EVIDENCE.finditer(goal.body)]
        lines.sort(key=lambda value: value[len("- **Evidence:** "):][:10], reverse=True)
        if len(lines) > 10:
            archive = config.vault_path / config.agent(f"Goals Archive/{gid}.md")
            archive.parent.mkdir(parents=True, exist_ok=True)
            old = archive.read_text() if archive.exists() else f"# {gid} — {goal.title}\n\nEarlier evidence.\n\n"
            fm.write(archive, {}, old.rstrip() + "\n" + "\n".join(lines[10:]) + "\n")
            pointer = f"- **Earlier evidence:** [[{config.link(config.agent(f'Goals Archive/{gid}'))}|{gid} archive]]\n"
            if "**Earlier evidence:**" not in goal.body:
                goal.body += pointer
        goal.body = "\n".join(value for value in goal.body.splitlines() if value.strip() and not EVIDENCE.fullmatch(value))
        goal.body += "\n" + "\n".join(lines[:10]) + "\n"
        goal.state.pop("nudged", None)
    elif operation == "nudge":
        touched = max([value for value in (goal.said_at, goal.state.get("updated")) if value] + [ev["at"] for ev in goal.evidence], default=today)
        if goal.state["state"] != "active" or goal.state.get("nudged") or (config.today() - date.fromisoformat(touched)).days < 60:
            raise ValueError("nudge needs an active goal with no evidence for 60 days, not already nudged")
        goal.state["nudged"] = today
    elif operation in ("reached", "set-aside", "reopen"):
        why = line("why", why or "", 200)
        goal.state["state"] = {"reached": "reached", "set-aside": "set_aside", "reopen": "active"}[operation]
        goal.state.pop("nudged", None)
        goal.body += f"- **{operation.title()}:** {today} — {why}\n"
    else:
        raise ValueError("unknown goal operation")
    return _write(config, data, body, goal, from_app=from_app)


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


def migrate(config: Config, *, apply: bool = False) -> list[str]:
    """Archive legacy content for the intellect to judge; never create goals."""
    from .needs import _section
    if config.edition != "personal":
        return ["organisation vault: no personal goals to migrate"]
    path = config.vault_path / config.human_goals
    original = path.read_bytes().decode("utf-8") if path.exists() else TEMPLATE
    _, body = fm.split(original)
    span = _section(body, "Active goals")
    if span is None:
        return ["no Active goals section; nothing to migrate"]
    for raw in body[span[0]:span[1]].splitlines():
        value = raw.strip()
        if not value or value == ACTIVE_DESCRIPTION:
            continue
        value = re.sub(r"^(?:#{3,6} |[-*] )", "", value)
        value = re.sub(r"^\*\*[^*]+:\*\*\s*", "", value)
        if re.fullmatch(r"\[[^\[\]\n]*\]", value) or re.match(r"None\b", value, re.I):
            continue
        break
    else:
        return ["no legacy content in Active goals; nothing to migrate"]
    archive_rel = config.agent("Goals Archive/Migration.md")
    if not apply:
        return [f"legacy content in Active goals; would archive to {archive_rel}"]
    archive = config.vault_path / archive_rel
    if archive.exists():
        raise ValueError("a migration archive already exists; inspect it before migrating more legacy text")
    archive.parent.mkdir(parents=True, exist_ok=True)
    fm.write(archive, {"kind": "log", "tier": "cold"}, original)
    # Preserve everything outside Active goals, including frontmatter and line endings.
    offset = len(original) - len(body)
    newline = "\r\n" if "\r\n" in original else "\n"
    cleared = original[:offset + span[0]] + "\n" + ACTIVE_DESCRIPTION + newline * 2 + original[offset + span[1]:]
    fm.write(path, {}, cleared)
    return [f"archived original to {archive_rel}; Active goals cleared"]
