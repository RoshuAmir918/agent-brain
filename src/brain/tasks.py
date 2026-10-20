"""The open-task register: parsing, per-task state, tiering, archive moves."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import activation as act, fm
from .config import Config

TASK_HEAD_RE = re.compile(r"^### (T-\d{3}) — (.+?)\s*$", re.M)
STATE_RE = re.compile(r"^<!-- brain: (.*?) -->\s*$", re.M)
DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
MAX_WORDS = 150


@dataclass
class Task:
    tid: str
    title: str
    body: str                      # everything after the heading line, excluding the brain comment
    state: dict = field(default_factory=dict)

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

    def render(self) -> str:
        comment = "<!-- brain: " + "; ".join(f"{k}={v}" for k, v in self.state.items()) + " -->"
        return f"### {self.tid} — {self.title}\n{comment}\n{self.body.rstrip()}\n"

    def render_line(self, config: Config) -> str:
        tail = self.first_status()
        ev = self.last_evidence_date()
        comment = "<!-- brain: " + "; ".join(f"{k}={v}" for k, v in self.state.items()) + " -->"
        return f"### {self.tid} — {self.title}\n{comment}\n- **Warm:** {tail} · last evidence {ev or 'unknown'} · full entry in [[{config.link(config.agent('Tasks Archive/' + self.tid))}|{self.tid} archive]]\n"


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
        # section ends at the next ### or ## heading
        nxt = re.compile(r"^#{2,3} ", re.M).search(text, h.end())
        end = nxt.start() if nxt else len(text)
        chunk = text[h.end():end]
        m = STATE_RE.search(chunk)
        state = parse_state(m.group(1)) if m else {}
        body = STATE_RE.sub("", chunk, count=1).lstrip("\n")
        spans.append((h.group(1), start, end))
        tasks.append(Task(h.group(1), h.group(2), body, state))
    return spans, tasks


def task_activation(t: Task, today: date) -> float:
    return act.activation(act.ages_from_dates(t.uses, today), act.ages_from_dates(t.uses_inferred, today))


def is_open(t: Task) -> bool:
    s = t.state.get("status", "open")
    return s == "open"


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


def bounded_hot_body(t: Task, config: Config) -> str:
    """Keep whole current fields and direct evidence; ambiguity needs judgment."""
    if t.word_count() <= MAX_WORDS:
        return t.body
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
    def label(chunk):
        match = re.match(r"^\s*-\s+\*\*([^*]+?)(?:\s*:\s*\*\*|\*\*\s*:)", chunk[0])
        return match[1].strip().casefold() if match else None

    def direct_pointer(chunk):
        text = "\n".join(chunk)
        links = re.findall(r"\[\[([^\]]+)\]\]", text)
        links += re.findall(r"\[[^\]]+\]\(([^)]+)\)", text)
        links += re.findall(r"https?://\S+", text)
        for link in links:
            target = link.split("|", 1)[0].split("#", 1)[0].removeprefix(config.vault + "/").removesuffix(".md")
            if target not in (config.agent(f"Tasks Archive/{t.tid}"), f"Tasks Archive/{t.tid}"):
                return True
        return False

    # Only an explicitly labeled direct latest pointer permits older Evidence
    # to be omitted. Otherwise retain every Evidence block in full, or fail.
    latest = any(label(chunk) == "latest evidence" and direct_pointer(chunk) for chunk in chunks)
    required, optional = [], []
    for chunk in chunks:
        if "\n".join(chunk).strip() == pointer:
            continue
        name = label(chunk)
        current = name != "earlier evidence" and (name != "evidence" or not latest)
        (required if current else optional).append(chunk)
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


def write_archive(config: Config, t: Task, reason: str, today: date) -> None:
    p = archive_path(config, t.tid)
    p.parent.mkdir(parents=True, exist_ok=True)
    comment = "<!-- brain: " + "; ".join(f"{k}={v}" for k, v in t.state.items()) + " -->"
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
            elif old_body.strip() not in body:
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
    task = Task(m.group(1), m.group(2).strip(), body, state)
    return task if is_open(task) else None


def archived_open_tasks(config: Config) -> list[Task]:
    d = config.vault_path / config.agent("Tasks Archive")
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("T-*.md")):
        t = read_archived_task(config, p)
        if t:
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
    return next((body for body in bodies if not is_register_summary(body)), bodies[0])


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
