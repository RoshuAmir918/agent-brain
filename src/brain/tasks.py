"""The open-task register: parsing, per-task state, tiering, archive moves.

Mutation helpers are lock-free: callers must hold vault_lock across the entire
read/modify/write operation. The CLI, ideas, needs-you and nightly own that lock.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import activation as act, fm, works
from .config import DAILY_CHUNKS, Config
from .vault import TASK_ID_RE, Vault

TASK_HEAD_RE = re.compile(r"^### (T-\d{3}) — (.+?)\s*$", re.M)
STATE_RE = re.compile(r"^<!-- brain: (.*?) -->\s*$", re.M)
DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
MAX_WORDS = 150
TASK_STATES = ("ready", "in_progress", "blocked", "waiting_for_owner", "yours", "set_aside", "completed", "cancelled")
# yours: the owner's own work, paced by them; the agent supports it and never asks about it on a short clock
STATE_SYNONYMS = {"done": "completed", "complete": "completed", "canceled": "cancelled",
                  "parked": "set_aside", "deferred": "set_aside", "deprioritized": "set_aside", "owner": "yours", "owners": "yours"}
QUIET_DAYS = 21          # an open task untouched this long is reviewed by the evening routine
OWNER_QUIET_DAYS = 14    # sooner for one waiting on the owner, or the owner's own: it is in their hands


def normal_state(value: str) -> str:
    """`In progress`, `waiting-for-owner` and `done` mean what they say; anything else stays as written."""
    key = "_".join(value.strip().lower().replace("-", " ").split())
    return STATE_SYNONYMS.get(key, key)


@dataclass
class Task:
    tid: str
    title: str
    body: str                      # everything after the heading line, excluding the brain comment
    state: dict = field(default_factory=dict)

    @property
    def task_state(self) -> str | None:
        if "state" in self.state:
            return normal_state(self.state["state"])
        status = self.state.get("status")
        return status if status in ("completed", "cancelled") else None

    @property
    def hub(self) -> str | None:
        match = re.search(r"^[ \t]*-[ \t]+\*\*Hub:\*\*[ \t]*\[\[([^\]\n]+)\]\]", self.body, re.M)
        if not match:
            return None
        return match[1].split("|", 1)[0].split("#", 1)[0].strip() or None

    @property
    def delivers(self) -> str | None:
        """What the owner will see when it is done, for a task whose result is an action or an answer."""
        match = re.search(r"^[ \t]*-[ \t]+\*\*Delivers:\*\*[ \t]*(\S.*?)[ \t]*$", self.body, re.M)
        return match[1] if match else None

    @property
    def work(self) -> str | None:
        match = re.search(r"^[ \t]*-[ \t]+\*\*Work:\*\*[ \t]*([a-z0-9-]+)[ \t]*$", self.body, re.M)
        return match[1] if match else None

    @property
    def resume(self) -> str | None:
        """A local `YYYY-MM-DD[ HH:MM]`, `computer` (until woken), or None."""
        match = re.search(r"^[ \t]*-[ \t]+\*\*Resume:\*\*[ \t]*(computer|\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2})?)[ \t]*$", self.body, re.M)
        return match[1] if match else None

    @property
    def handoff(self) -> tuple[str, str] | None:
        """(date, where to report) when the owner handed this task off: work it until done or blocked."""
        match = HANDOFF.search(self.body)
        return (match[1], (match[3] or "").strip()) if match else None

    @property
    def owner(self) -> str | None:
        """Who the task is for: the person who handed it off (`· for <platform>:<id>`), in an organisation's intellect.
        A personal intellect's tasks are all its owner's and carry none."""
        match = HANDOFF.search(self.body)
        return match[2] if match and match[2] else None

    @property
    def uses(self) -> list[str]:
        return [x for x in self.state.get("uses", "").split(",") if x]

    @property
    def uses_inferred(self) -> list[str]:
        return [x for x in self.state.get("inferred", "").split(",") if x]

    def add_use(self, day: str, inferred: bool = False) -> None:
        key = "inferred" if inferred else "uses"
        cur = [x for x in self.state.get(key, "").split(",") if x]
        if day not in cur:
            cur.insert(0, day)
        self.state[key] = ",".join(sorted(set(cur), reverse=True)[:30])

    def word_count(self) -> int:
        return len(self.body.split())

    def first_status(self) -> str:
        for ln in self.body.splitlines():
            s = ln.strip()
            if s.startswith("- **"):
                s = re.sub(r"\*\*", "", s[2:])
                return s[:220].rstrip() + ("…" if len(s) > 220 else "")
        return ""

    def last_evidence_date(self) -> str:
        ds = DATE_RE.findall(self.body)
        return max(ds) if ds else ""

    def last_touched(self) -> str:
        """The latest day credited to this task: a use, an inferred use, or any date its title or entry names, so a
        deadline still ahead keeps it out of review. A malformed date is skipped, never fatal."""
        days = []
        for value in (*self.uses, *self.uses_inferred, *DATE_RE.findall(self.title), *DATE_RE.findall(self.body)):
            try:
                days.append(date.fromisoformat(value.strip()).isoformat())
            except ValueError:
                pass
        return max(days, default="")

    def _comment(self) -> str:
        parts = (f"{key}={value}" for key, value in self.state.items()
                 if key != "status" or "state" not in self.state)
        return "<!-- brain: " + "; ".join(parts) + " -->"

    def render(self) -> str:
        return f"### {self.tid} — {self.title}\n{self._comment()}\n{self.body.rstrip()}\n"

    def render_line(self, config: Config) -> str:
        tail = self.first_status()
        ev = self.last_evidence_date()
        return f"### {self.tid} — {self.title}\n{self._comment()}\n- **Warm:** {tail} · last evidence {ev or 'unknown'} · full entry in [[{config.link(config.agent('Tasks Archive/' + self.tid))}|{self.tid} archive]]\n"


def parse_state(s: str) -> dict:
    out = {}
    for part in s.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def parse_register(text: str) -> tuple[list[tuple[str, int, int]], list[Task]]:
    """Return section spans [(tid, start, end)] and parsed tasks."""
    heads = list(TASK_HEAD_RE.finditer(text))
    spans, tasks = [], []
    for i, h in enumerate(heads):
        start = h.start()
        # An entry ends at the next task or section; a `### notes` subheading inside it is part of the entry.
        nxt = re.compile(r"^(?:## |### T-\d{3} — )", re.M).search(text, h.end())
        end = nxt.start() if nxt else len(text)
        chunk = text[h.end():end]
        m = STATE_RE.search(chunk)
        state = parse_state(m.group(1)) if m else {}
        body = STATE_RE.sub("", chunk, count=1).lstrip("\n")
        spans.append((h.group(1), start, end))
        tasks.append(Task(h.group(1), h.group(2), body, state))
    return spans, tasks


HANDOFF = re.compile(r"^[ \t]*-[ \t]+\*\*Handed off:\*\*[ \t]*(\d{4}-\d{2}-\d{2})(?:[ \t]*·[ \t]*for[ \t]+(\S+))?"
                     r"(?:[ \t]*·[ \t]*report in[ \t]*(.+?))?[ \t]*$", re.M)
PERSON = re.compile(r"^[a-z][a-z0-9_-]*:[A-Za-z0-9_.@-]+$")   # a person as the runtime names them: <platform>:<id>
HANDOFF_LINE = re.compile(r"^[ \t]*-[ \t]+\*\*Handed off:\*\*[^\n]*\n?", re.M)
PACE_LINE = re.compile(r"^[ \t]*-[ \t]+\*\*Pace:\*\*[^\n]*\n?", re.M)
WORK_LINE = re.compile(r"^[ \t]*-[ \t]+\*\*Work:\*\*[^\n]*\n?", re.M)
RESTING = ("waiting_for_owner", "yours", "set_aside", "blocked")
FIELDS = ("status", "next", "verified", "evidence", "blocker", "ask", "why", "delivers", "hub", "work", "resume", "morning")
LABELS = {"evidence": "Latest evidence"}   # the line a field writes, when it is not the field's own name
RESUME = re.compile(r"^(?:computer|\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2})?)$")


def single_line(name: str, value: str) -> str:
    if any(char in value for char in "\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029"):
        raise ValueError(f"{name} must be single-line (no newlines)")
    return value.strip()


def note_name(config: Config, value: str) -> str:
    value = single_line("hub", value)
    if not value or any(char in value for char in "[]#|"):
        raise ValueError("hub must name an existing note, without wikilink brackets")
    resolved = Vault(config.vault_path).resolve(value)
    if resolved is None or not resolved.endswith(".md"):
        raise ValueError(f"hub note does not exist: {value}")
    return resolved.removesuffix(".md")


def _field_value(config: Config, name: str, value: str) -> str:
    value = single_line(name, value)
    if name == "resume" and not RESUME.fullmatch(value):
        raise ValueError("resume must be computer, YYYY-MM-DD or YYYY-MM-DD HH:MM, in the owner's local time")
    if name == "ask":
        from .needs import vague_ask
        if (reason := vague_ask(value)):
            raise ValueError(f"ask refused: {reason}")
    if name == "work" and not works.SLUG.fullmatch(value):
        raise ValueError("work slug must match [a-z0-9-]+")
    limit = {"ask": 16, "why": 40}.get(name)
    if limit and len(value.split()) > limit:
        raise ValueError(f"{name} must be at most {limit} words")
    return f"[[{note_name(config, value)}]]" if name == "hub" else value


def _replace_field(body: str, name: str, value: str | None) -> str:
    """Replace a whole field block (its line and the indented lines continuing it, and any duplicates), leaving
    everything else in the entry alone: notes, dated sections and other fields."""
    label = LABELS.get(name, name.title())
    aliases = {name, "next action"} if name == "next" else {name, label.casefold()}
    lines = body.splitlines(keepends=True)
    out, placed, skipping, blanks = [], False, False, []
    line_for = f"- **{label}:** {value}\n" if value is not None else ""
    for line in lines:
        head = re.match(r"^[ \t]*-[ \t]+\*\*([^*\n]+):\*\*", line)
        if head and head[1].strip().casefold() in aliases and not line[:1].isspace():
            skipping, blanks = True, []
            if not placed:
                out.append(line_for)
                placed = True
            continue
        if skipping and not line.strip():
            blanks.append(line)   # a blank inside the field is dropped only if an indented line follows
            continue
        if skipping and line[:1] in (" ", "\t"):
            blanks = []
            continue   # a continuation of the replaced field
        skipping = False
        out.extend(blanks)
        blanks = []
        out.append(line)
    out.extend(blanks)
    body = "".join(out)
    if not placed and line_for:
        body = body.rstrip("\n") + "\n" + line_for
    return body


def set_fields(config: Config, tid: str, today: date, *, state: str | None = None,
               values: dict[str, str] | None = None, clear: list[str] | tuple[str, ...] = (),
               reviewed: str | None = None, report: str | None = None, title: str | None = None) -> Task:
    """Update an open task's full entry; the caller holds the vault lock. `reviewed` appends a dated Reviewed line
    (the evening's decisions accumulate; they are never replaced). Clearing `handoff` takes the task back from its
    worker: it stays open, worked only when someone asks."""
    values = values or {}
    if set(values) - set(FIELDS) or set(clear) - set(FIELDS) - {"handoff"}:
        raise ValueError("unknown task field")
    if set(values) & set(clear):
        raise ValueError("a field cannot be set and cleared together")
    if state is not None:
        state = normal_state(single_line("state", state))
        if state in ("completed", "cancelled"):
            raise ValueError("use task done or drop with evidence to close a task")
        if state not in TASK_STATES:
            raise ValueError(f"unknown task state: {state}")
    if reviewed is not None:
        reviewed = single_line("reviewed", reviewed)
    if report is not None:
        report = single_line("report", report)
    if title is not None:
        title = single_line("title", title)
        if not title:
            raise ValueError("title must not be empty")
    if state is None and not values and not clear and not reviewed and report is None and title is None:
        raise ValueError("set needs a field, --state, --title or --clear")
    values = {name: _field_value(config, name, value) for name, value in values.items()}
    registered, archived = load_tasks(config)
    task = next((t for t in registered + archived if t.tid == tid), None)
    if task is None or not is_open(task):
        raise ValueError(f"{tid} is not an open task (unknown or closed); a closed one comes back with task {tid} reopen")
    if state == "waiting_for_owner" and "ask" not in values and ("ask" in clear or not _owner_ask(task.body)):
        raise ValueError("waiting_for_owner needs the question: --ask \"…\" --why \"…\"")
    before = current_words(task, config)
    if state is not None:
        task.state["state"] = state
        task.state.pop("status", None)
    if title is not None:
        task.title = title
    if "handoff" in clear:
        if not task.handoff:
            raise ValueError(f"{tid} was not handed off")
        task.body = PACE_LINE.sub("", HANDOFF_LINE.sub("", task.body))
        clear = [name for name in clear if name != "handoff"]
    for name in dict.fromkeys([*values, *clear]):
        if name == "morning" and name in values:
            # A note carried to the next briefing; several may wait, and the morning removes them all with --clear.
            task.body = task.body.rstrip("\n") + f"\n- **morning:** {values[name]}\n"
        else:
            task.body = _replace_field(task.body, name, values.get(name))
    if reviewed:
        task.body = task.body.rstrip("\n") + f"\n- **Reviewed:** {today.isoformat()} — {reviewed}\n"
    if report is not None:
        if not task.handoff:
            raise ValueError(f"{tid} was not handed off; there is no report line to change")
        line = handoff_line(task.handoff[0], task.owner, report)
        task.body = HANDOFF_LINE.sub(line + "\n", task.body, count=1)
    after = current_words(task, config)
    # A field written long stays long: past the limit only a person can cut the entry down, so it is refused here,
    # where the writer still has what it meant. A write that does not grow the entry is always taken.
    if values and after > MAX_WORDS and after > before:
        raise ValueError(f"{tid} would hold {after} words of current fields; the limit is {MAX_WORDS}. Say it shorter: "
                         "a sentence or two per field, the detail in a note linked with --evidence")
    save_task(config, task, today)
    return task


def _owner_ask(body: str) -> str | None:
    match = re.search(r"^[ \t]*-[ \t]+\*\*Ask:\*\*[ \t]*(\S.*?)[ \t]*$", body, re.M)
    return match[1] if match else None


def next_id(config: Config) -> str:
    """Next id after every task that exists: the register's headings and the archive's, open or closed. Only real task
    headings count; a note that merely mentions an id (an example, a test fixture, "T-999") never uses one up."""
    heading = re.compile(r"^#{1,3} T-(\d{3})\b", re.M)
    used = []
    register = config.vault_path / config.agent("Tasks.md")
    sources = [register] if register.exists() else []
    archive = config.vault_path / config.agent("Tasks Archive")
    if archive.is_dir():
        sources += sorted(archive.glob("*.md"))
        used += [int(m[1]) for path in archive.glob("T-*.md") if (m := re.match(r"T-(\d{3})\.md$", path.name))]
    for path in sources:
        used += [int(number) for number in heading.findall(path.read_text(encoding="utf-8", errors="replace"))]
    number = max(used, default=0) + 1
    if number > 999:
        raise ValueError("no unused T-nnn task id remains")
    return f"T-{number:03d}"


def append_task(config: Config, task: Task) -> None:
    """Append a full entry to the register; the caller holds the vault lock."""
    register = config.vault_path / config.agent("Tasks.md")
    data, body = fm.read(register) if register.exists() else ({"kind": "orientation"}, "# Tasks\n\n## Active work\n")
    register.parent.mkdir(parents=True, exist_ok=True)
    standing = re.search(r"^## Standing", body, re.M)
    if standing:
        # A new task is active work: it goes before the standing-state section, not under it.
        head, tail = body[:standing.start()], body[standing.start():]
        fm.write(register, data, head.rstrip("\n") + "\n\n" + task.render() + "\n\n" + tail)
    else:
        fm.write(register, data, body.rstrip("\n") + "\n\n" + task.render() + "\n")


def new_task(config: Config, title: str, source: str, *, state: str = "ready",
             values: dict[str, str] | None = None, work: str | None = None) -> Task:
    """Create a task without handing it off; the caller holds the vault lock."""
    title, source = single_line("title", title), single_line("source", source)
    if not title or not source:
        raise ValueError("new needs a nonempty title and --source")
    state = normal_state(single_line("state", state))
    if state not in ("ready", "in_progress"):
        raise ValueError("new task state must be ready or in_progress")
    if work is not None and not works.SLUG.fullmatch(work):
        raise ValueError("work slug must match [a-z0-9-]+")
    values = values or {}
    if set(values) - {"status", "next", "delivers", "hub"}:
        raise ValueError("unsupported new task field")
    body = "".join(f"- **{name.title()}:** {_field_value(config, name, value)}\n" for name, value in values.items())
    resolved = Vault(config.vault_path).resolve(source)
    source = f"[[{resolved.removesuffix('.md')}]]" if resolved else f'"{source}"'
    body += f"- **Source:** {source}\n"
    if work is not None:
        body += f"- **Work:** {work}\n"
    task = Task(next_id(config), title, body, {"state": state})
    if (words := current_words(task, config)) > MAX_WORDS:
        raise ValueError(f"the new task would hold {words} words; the limit is {MAX_WORDS}. Say it shorter: "
                         "a sentence or two per field")
    task.add_use(config.today().isoformat())   # made today is used today: a new task starts in the register
    append_task(config, task)
    return task


def pace(task: Task) -> str:
    """No Pace line means regular chunks while the task is due."""
    return _pace(task.body)


def _pace(body: str) -> str:
    return "daily" if re.search(r"^[ \t]*-[ \t]+\*\*Pace:\*\*[ \t]*daily[ \t]*$", body, re.M) else "regular"


def set_pace(config: Config, tid: str, pace: str, today: date) -> Task:
    """Change the pace where the full entry lives, just like a handoff."""
    if pace not in ("regular", "daily"):
        raise ValueError("pace must be regular or daily")
    registered, archived = load_tasks(config)
    task = next((t for t in registered + archived if t.tid == tid), None)
    if task is None:
        path = archive_path(config, tid)
        task = read_archived_task(config, path) if path.exists() else None
    if task is None or not task.handoff:
        raise ValueError(f"{tid} is not a handed-off task")
    task.body = PACE_LINE.sub("", task.body)
    if pace == "daily":
        match = HANDOFF_LINE.search(task.body)
        at = match.end()
        task.body = task.body[:at].rstrip("\n") + "\n- **Pace:** daily\n" + task.body[at:]
    save_task(config, task, today)
    return task


def handoff_line(day: str, owner: str | None, report_to: str) -> str:
    return f"- **Handed off:** {day}" + (f" · for {owner}" if owner else "") + (f" · report in {report_to}" if report_to else "")


def with_handoff(body: str, today: date, report_to: str, owner: str | None = None) -> str:
    """The task's entry with one Handed off line, placed after its first field line."""
    line = handoff_line(today.isoformat(), owner, report_to)
    body = PACE_LINE.sub("", HANDOFF_LINE.sub("", body))
    lines = body.splitlines()
    at = next((i + 1 for i, text in enumerate(lines) if text.lstrip().startswith("- **")), 0)
    lines.insert(at, line)
    return "\n".join(lines).rstrip("\n") + "\n"


def hand_off(config: Config, tid: str, today: date, report_to: str = "", work: str | None = None,
             owner: str | None = None) -> Task:
    """Mark a task handed off by the owner and in progress, in the register entry or the archive body, whichever holds
    the full entry; returns the task as written. A fresh handoff restarts the date, budget and pace: to attach a work to
    a task already handed off, `brain task T-nnn set --work <slug>`."""
    if work is not None and not works.SLUG.fullmatch(work):
        raise ValueError("work slug must match [a-z0-9-]+")
    if owner is not None and not PERSON.fullmatch(owner):
        raise ValueError("--for names a person as <platform>:<id>, e.g. slack:U0123ABC")
    registered, archived = load_tasks(config)
    task = next((t for t in registered + archived if t.tid == tid), None)
    if task is None or not is_open(task):
        raise ValueError(f"{tid} is not an open task")
    # A fresh handoff names who it is for now; one that names nobody keeps the task's owner.
    task.body = with_handoff(task.body, today, report_to, owner or task.owner)
    if work is not None:
        task.body = WORK_LINE.sub("", task.body).rstrip("\n") + f"\n- **Work:** {work}\n"
    if task.task_state in (None, "ready", *RESTING):
        task.state["state"] = "in_progress"
        task.state.pop("status", None)
    save_task(config, task, today)
    return task


def set_aside(config: Config, tid: str, today: date, why: str) -> Task:
    """Park an open task, with a dated Reviewed line saying why; it is never asked about until it comes up again."""
    registered, archived = load_tasks(config)
    task = next((t for t in registered + archived if t.tid == tid), None)
    if task is None or not is_open(task):
        raise ValueError(f"{tid} is not an open task")
    task.state["state"] = "set_aside"
    task.state.pop("status", None)
    task.body = task.body.rstrip("\n") + f"\n- **Reviewed:** {today.isoformat()} — set aside: {why}\n"
    save_task(config, task, today)
    return task


def complete(config: Config, tid: str, today: date, evidence: str) -> Task:
    """Close an open task as completed, with its evidence on a dated line."""
    registered, archived = load_tasks(config)
    task = next((t for t in registered + archived if t.tid == tid), None)
    if task is None or not is_open(task):
        raise ValueError(f"{tid} is not an open task")
    from . import continuation
    continuation.clear_close_out(config, tid)
    task.state["state"] = "completed"
    task.state.pop("status", None)
    task.body = task.body.rstrip("\n") + f"\n- **Completed:** {today.isoformat()} — {evidence}\n"
    save_task(config, task, today)
    return task


def close(config: Config, tid: str, today: date, done: bool, why: str) -> Task:
    """The owner closing their own task from the app: `done` completes it, otherwise it is cancelled. Either way a dated
    line says so; nothing else is decided for them."""
    if done:
        return complete(config, tid, today, why)
    registered, archived = load_tasks(config)
    task = next((t for t in registered + archived if t.tid == tid), None)
    if task is None or not is_open(task):
        raise ValueError(f"{tid} is not an open task")
    from . import continuation
    continuation.clear_close_out(config, tid)
    task.state["state"] = "cancelled"
    task.state.pop("status", None)
    task.body = task.body.rstrip("\n") + f"\n- **Dropped:** {today.isoformat()} — {why}\n"
    save_task(config, task, today)
    return task


def reopen(config: Config, tid: str, today: date, why: str) -> Task:
    """Bring a completed or cancelled task back as ready, wherever its entry is, with a dated line saying why; its
    earlier evidence stays. The night puts it back in the register."""
    registered, _ = load_tasks(config)
    task = next((t for t in registered if t.tid == tid), None)
    if task is None and archive_path(config, tid).exists():
        task = read_archived_task(config, archive_path(config, tid))
    if task is None:
        raise ValueError(f"{tid} is not a task")
    if is_open(task):
        raise ValueError(f"{tid} is already open")
    task.state["state"] = "ready"
    task.state.pop("status", None)
    task.add_use(today.isoformat())   # active again today, so the night brings it back to the register
    task.body = task.body.rstrip("\n") + f"\n- **Reopened:** {today.isoformat()} — {why}\n"
    save_task(config, task, today)
    return task


def save_task(config: Config, task: Task, today: date) -> None:
    """Write a changed task where its full entry lives: the register, or the archive when the register holds a warm line."""
    tid = task.tid
    reg = config.vault_path / config.agent("Tasks.md")
    data, body = fm.read(reg)
    spans, parsed = parse_register(body)
    full = next(((s, e) for (t, s, e), entry in zip(spans, parsed) if t == tid and not is_register_summary(entry.body)), None)
    if full:
        s, e = full
        fm.write(reg, data, body[:s] + task.render() + ("\n" if body[e:] and not body[e:].startswith("\n") else "") + body[e:])
        if not is_open(task) and archive_path(config, tid).exists():
            # An older archive copy still marked open would bring the task back once the register stops listing it.
            write_archive(config, task, task.state.get("tier", "warm"), today, whole=True)
    else:
        write_archive(config, task, task.state.get("tier", "warm"), today, whole=True)
        # The register's warm line carries the state too; left alone, a closed task would still read as open.
        line = next(((s, e) for (t, s, e), entry in zip(spans, parsed) if t == tid), None)
        if line:
            s, e = line
            fm.write(reg, data, body[:s] + task.render_line(config) + ("\n" if body[e:] and not body[e:].startswith("\n") else "") + body[e:])


def due(tasks: list[Task], pace: str | None = "regular", worked: dict[str, str] | None = None, *,
        chunks: dict[str, int] | None = None, daily_chunks: int = DAILY_CHUNKS, now: str | None = None,
        budgets: dict[str, int] | None = None, caps: dict[str, int] | None = None) -> list[Task]:
    """Handed-off tasks a worker should work now: open and not resting on the owner. With `worked` (each
    task's last chunk), the one worked least recently comes first and a task never worked before all of them, so one
    long task cannot hold the slot; otherwise, oldest handoff first. `chunks` is
    today's local-day count per task; regular tasks at their budget wait. `pace=None` combines both paces,
    with daily tasks due from noon local until their first counted chunk. Explicit paces keep the legacy lists."""
    worked = worked or {}
    chunks = chunks or {}
    return sorted((t for t in tasks if is_open(t) and t.handoff and t.delivers
                   # Computer rests end only on an explicit wake, regardless of time or pace.
                   and t.resume != "computer"
                   and (t.task_state not in RESTING or (t.task_state == "blocked" and t.resume and now
                                                       and (t.resume if " " in t.resume else t.resume + " 00:00") <= now))
                   and (pace is None or _pace(t.body) == pace)
                   and (_pace(t.body) == "daily" or chunks.get(t.tid, 0) < (budgets or {}).get(t.tid, daily_chunks))
                   and (pace is not None or _pace(t.body) != "daily"
                        or (now and now[11:] >= "12:00" and chunks.get(t.tid, 0) == 0))
                   and (t.tid not in (caps or {}) or chunks.get(t.tid, 0) < caps[t.tid])
                   # A task resting until a time (`now` is the owner's local "YYYY-MM-DD HH:MM") waits for it.
                   and not (now and t.resume and (t.resume if " " in t.resume else t.resume + " 00:00") > now)),
                  key=lambda t: (worked.get(t.tid, ""), t.handoff[0], t.tid))


def local_now(config: Config) -> str:
    """The owner's local time as `YYYY-MM-DD HH:MM`, the form a Resume line uses."""
    moment = config.now()
    return (moment if moment.tzinfo is None else moment.astimezone(config.zone)).strftime("%Y-%m-%d %H:%M")


def task_activation(t: Task, today: date) -> float:
    return act.activation(act.ages_from_dates(t.uses, today), act.ages_from_dates(t.uses_inferred, today))


def is_open(t: Task) -> bool:
    if "state" in t.state:
        return t.task_state not in ("completed", "cancelled")
    return t.state.get("status", "open") == "open"   # legacy: only an explicit open, or none, is open


def quiet(tasks: list[Task], today: date, worked: dict[str, str] | None = None) -> list[tuple[Task, str]]:
    """Open tasks that have gone untouched long enough to review, oldest first, with the day each was last touched.
    A set-aside task is already decided; the review never sees it again until its state changes. `worked` (each
    task's last continue chunk) counts as a touch: a handoff its worker moved today is not quiet."""
    out = []
    for t in tasks:
        if not is_open(t) or t.task_state == "set_aside":
            continue
        touched = max(t.last_touched(), (worked or {}).get(t.tid, "")[:10])
        days = OWNER_QUIET_DAYS if t.task_state in ("waiting_for_owner", "yours") else QUIET_DAYS
        if not touched or (today - date.fromisoformat(touched)).days >= days:
            out.append((t, touched))
    return sorted(out, key=lambda pair: pair[1])


def seed_uses_from_body(t: Task, today: date, config: Config) -> None:
    """Credit dated evidence fields, never arbitrary dates or deadlines.

    Ambiguous historical prose is left for source-backed attribution, not
    guessed from a date occurring somewhere in the task.
    """
    evidence = re.compile(
        r"^\s*-\s+\*\*(?:Latest evidence|Verified progress|Completion evidence|Evidence):\*\*\s+"
        r"(?:\[\[(?:" + re.escape(config.vault) + r"/)?Conversations/)?(20\d{2}-\d{2}-\d{2})\b", re.I)
    for line in t.body.splitlines():
        match = evidence.match(line)
        if match:
            try:
                day = date.fromisoformat(match[1])
            except ValueError:
                continue
            if day <= today:
                t.add_use(day.isoformat())


def archive_path(config: Config, tid: str) -> Path:
    return config.vault_path / config.agent("Tasks Archive") / f"{tid}.md"


def _chunk_label(chunk: list[str]) -> str | None:
    match = re.match(r"^\s*-\s+\*\*([^*]+?)(?:\s*:\s*\*\*|\*\*\s*:)", chunk[0])
    return match[1].strip().casefold() if match else None


def _direct_pointer(t: Task, config: Config, chunk: list[str]) -> bool:
    """A link to something other than the task's own archive."""
    text = "\n".join(chunk)
    links = re.findall(r"\[\[([^\]]+)\]\]", text)
    links += re.findall(r"\[[^\]]+\]\(([^)]+)\)", text)
    links += re.findall(r"https?://\S+", text)
    for link in links:
        target = link.split("|", 1)[0].split("#", 1)[0].removeprefix(config.vault + "/").removesuffix(".md")
        if target not in (config.agent(f"Tasks Archive/{t.tid}"), f"Tasks Archive/{t.tid}"):
            return True
    return False


def _partition(t: Task, config: Config) -> tuple[list[list[str]], list[list[str]], str]:
    """The entry's fields as chunks of lines: those a hot entry must keep whole (its current fields, the newest Reviewed,
    and the evidence that is current) and those it may leave to the archive; and the archive pointer line."""
    pointer = f"- **Earlier evidence:** [[{config.link(config.agent('Tasks Archive/' + t.tid))}|{t.tid} archive]]"
    chunks = []
    for line in t.body.splitlines():
        field = re.match(r"^\s*-\s+\*\*([^*]+?)(?:\s*:\s*\*\*|\*\*\s*:)", line)
        if field and line[:1].isspace():
            raise ValueError(f"{t.tid}: parent judgment required: ambiguous indented task field")
        if chunks and not field:
            chunks[-1].append(line)
        else:
            chunks.append([line])
    label = _chunk_label
    def direct_pointer(chunk):
        return _direct_pointer(t, config, chunk)

    # Only an explicitly labeled direct latest pointer permits older Evidence
    # to be omitted. Otherwise retain every Evidence block in full, or fail.
    latest = any(label(chunk) == "latest evidence" and direct_pointer(chunk) for chunk in chunks)
    newest_review = max((i for i, chunk in enumerate(chunks) if label(chunk) == "reviewed"), default=-1)
    required, optional = [], []
    for i, chunk in enumerate(chunks):
        if "\n".join(chunk).strip() == pointer:
            continue
        name = label(chunk)
        current = (name != "earlier evidence" and (name != "evidence" or not latest)
                   and (name != "reviewed" or i == newest_review))
        (required if current else optional).append(chunk)
    return required, optional, pointer


def current_words(t: Task, config: Config) -> int:
    """Words a hot entry cannot shed to the archive: what `bounded_hot_body` must fit in MAX_WORDS."""
    try:
        required, _, pointer = _partition(t, config)
    except ValueError:
        return t.word_count()
    return len(pointer.split()) + sum(len("\n".join(chunk).split()) for chunk in required)


def bounded_hot_body(t: Task, config: Config) -> str:
    """Keep whole current fields and direct evidence; ambiguity needs judgment."""
    if t.word_count() <= MAX_WORDS:
        return t.body
    required, optional, pointer = _partition(t, config)
    label = _chunk_label
    def direct_pointer(chunk):
        return _direct_pointer(t, config, chunk)
    if not any(label(chunk) not in (None, "evidence", "earlier evidence") for chunk in required):
        raise ValueError(f"{t.tid}: parent judgment required: no labeled current fields; cannot create a useful task summary")
    if not any(label(chunk) in ("latest evidence", "evidence") and direct_pointer(chunk) for chunk in required):
        raise ValueError(f"{t.tid}: parent judgment required: latest direct evidence pointer is not identified; no evidence dropped")
    budget = MAX_WORDS - len(pointer.split())
    words = sum(len("\n".join(chunk).split()) for chunk in required)
    if words > budget:
        raise ValueError(f"{t.tid}: parent judgment required: current fields plus archive pointer exceed the {MAX_WORDS}-word limit; no fields truncated")
    kept = list(required)
    for chunk in optional:
        count = len("\n".join(chunk).split())
        if words + count <= budget:
            kept.append(chunk)
            words += count
    return "\n".join(line for chunk in kept for line in chunk).rstrip() + "\n" + pointer + "\n"


def write_archive(config: Config, t: Task, reason: str, today: date, whole: bool = False) -> None:
    """`whole` means the body is the task's complete entry, edited in place (save_task): git keeps the earlier text,
    so it is not repeated as an Earlier archive block. A move from the register may carry only a bounded excerpt,
    so there the prior full body is kept."""
    p = archive_path(config, t.tid)
    p.parent.mkdir(parents=True, exist_ok=True)
    comment = t._comment()
    existing = p.read_text(encoding="utf-8") if p.exists() else ""
    generated = "by the nightly script.\n\n" in existing
    moved = re.search(r"\bMoved (20\d{2}-\d{2}-\d{2})\b",
                      existing.split("by the nightly script.\n\n", 1)[0]) if generated else None
    provenance = (f"Moved {moved[1] if moved else today.isoformat()}" if moved or not existing
                  else "Historical move date unrecorded; preserved")
    header = f"# {t.tid} — {t.title}\n{comment}\n\nFull register entry, kept here while the task is {reason}. State is still open unless the register says otherwise. {provenance} by the nightly script.\n\n"
    earlier = ""
    body = t.body
    if existing:
        old_body = read_archive_body(config, t.tid) or ""
        separator = "\n\n## Earlier archive\n\n"
        if not generated:
            # Owner-preserved historical notes are not generated register
            # views. Retain their complete original text, including stops.
            earlier = separator + existing
        elif separator in existing:
            earlier = separator + existing.split(separator, 1)[1]
        if is_register_summary(body) and old_body:
            # A derived register view must never replace the recoverable body.
            body = old_body
        elif old_body:
            pointer = f"- **Earlier evidence:** [[{config.link(config.agent('Tasks Archive/' + t.tid))}|{t.tid} archive]]"
            unchanged = False
            if pointer in body:
                try:
                    unchanged = bounded_hot_body(Task(t.tid, t.title, old_body, t.state), config).strip() == body.strip()
                except ValueError:
                    pass  # An unrepresentable old body still remains evidence.
            if unchanged:
                # An unchanged budget excerpt still refers to the full body.
                body = old_body
            elif old_body.strip() not in body and not whole:
                # Changed excerpt fields remain current; retain the prior full
                # entry as evidence instead of silently choosing its old state.
                earlier = separator + old_body.rstrip() + earlier
    p.write_text(header + body.rstrip() + "\n" + earlier, encoding="utf-8")


def read_archived_task(config: Config, path: Path) -> Task | None:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"# (T-\d{3}) — (.+)", text)
    if not m or "State is still open" not in text:
        return None
    sm = STATE_RE.search(text)
    state = parse_state(sm.group(1)) if sm else {}
    body = read_archive_body(config, m.group(1)) or ""
    return Task(m.group(1), m.group(2).strip(), body, state)


def archived_open_tasks(config: Config) -> list[Task]:
    d = config.vault_path / config.agent("Tasks Archive")
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("T-*.md")):
        t = read_archived_task(config, p)
        if t and is_open(t):
            out.append(t)
    return out


def read_archive_body(config: Config, tid: str) -> str | None:
    p = archive_path(config, tid)
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8")
    marker = "by the nightly script.\n\n"
    if marker not in text:
        return None
    bodies = text.split(marker, 1)[1].split("\n\n## Earlier archive\n\n")
    # Recover archives already affected by an earlier summary overwrite.
    body = next((body for body in bodies if not is_register_summary(body)), bodies[0])
    # An archive written by hand may repeat the task heading and brain comment; the register adds its own.
    return re.sub(r"\A\s*#+ T-\d{3} — [^\n]*\n(?:<!-- brain:[^\n]*-->\n)?\s*", "", body)


def is_register_summary(body: str) -> bool:
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    return len(lines) == 1 and lines[0].startswith("- **Warm:**")


def load_tasks(config: Config, report: list[str] | None = None) -> tuple[list[Task], list[Task]]:
    """One Task per ID: register tasks, then archived open tasks the register does not list.

    A generated warm line in the register stands for its archive body; a full
    entry in the register is canonical over the archive.
    """
    _, parsed = parse_register(fm.read(config.vault_path / config.agent("Tasks.md"))[1])
    seen, tasks = set(), []
    for t in parsed:
        if t.tid in seen:
            if report is not None:
                report.append(f"task: {t.tid} listed twice in the register; later entry dropped")
            continue
        seen.add(t.tid)
        if is_register_summary(t.body):
            t.body = read_archive_body(config, t.tid) or t.body
        tasks.append(t)
    archived = [t for t in archived_open_tasks(config) if t.tid not in seen]
    return tasks, archived


COMPLETED_LINE = re.compile(r"^[ \t]*-[ \t]+\*\*Completed:\*\*[ \t]*(\d{4}-\d{2}-\d{2})(?:[ \t]*[—-][ \t]*(.*))?$", re.M)


def completed(config: Config, today: date, days: int = 7) -> list[dict]:
    """Tasks completed in the last `days` local days, newest first, from the register and every archive file: what a
    follow-on check reads (is the goal a finished task served still open? is there an extension worth offering?)."""
    sources = []
    register = config.vault_path / config.agent("Tasks.md")
    if register.exists():
        sources.append(fm.read(register)[1])
    archive = config.vault_path / config.agent("Tasks Archive")
    if archive.is_dir():
        for path in sorted(archive.glob("*.md")):
            sources.append(re.sub(r"^#{1,2} (?=T-\d{3} — )", "### ", fm.read(path)[1], flags=re.M))
    seen, out = set(), []
    for text in sources:
        for task in parse_register(text)[1]:
            if task.tid in seen or task.task_state != "completed":
                continue
            match = COMPLETED_LINE.search(task.body)
            if not match:
                continue
            try:
                when = date.fromisoformat(match[1])
            except ValueError:
                continue
            if (today - when).days > days or when > today:
                continue
            seen.add(task.tid)
            source = re.search(r"^[ \t]*-[ \t]+\*\*Source:\*\*[ \t]*(.+?)[ \t]*$", task.body, re.M)
            out.append({"id": task.tid, "title": task.title, "completed": match[1], "evidence": (match[2] or "").strip(),
                        "hub": task.hub, "work": task.work, "delivers": task.delivers,
                        "source": source[1] if source else None})
    return sorted(out, key=lambda row: (row["completed"], row["id"]), reverse=True)
