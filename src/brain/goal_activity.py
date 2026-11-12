"""Goal work assembled from its existing records, without another activity ledger."""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta

from . import continuation, fm, goals, ideas, needs, reception, runtime, tasks, works
from .config import Config
from .vault import Vault


def _id(kind: str, source: str) -> str:
    return kind + ":" + hashlib.sha256(source.encode()).hexdigest()[:16]


def _at(config: Config, value) -> str | None:
    if value is None:
        return None
    text = value.isoformat() if isinstance(value, (date, datetime)) else str(value)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}–\d{2}:\d{2}", text):
        text = text[:16]
    try:
        if len(text) == 10:
            return date.fromisoformat(text).isoformat()
        return config.local(datetime.fromisoformat(text)).isoformat()
    except ValueError:
        return None


def _key(source: str) -> str:
    return source.split("#", 1)[0].removesuffix(".md").casefold()


def readable(text: str, names: dict[str, str] | None = None) -> str:
    def label(value):
        value = re.split(r"[/\\]", value.split("#", 1)[0])[-1]
        return re.sub(r"\.md\b", "", value, flags=re.I)

    text = re.sub(r"\[\[([^]]+)\]\]", lambda match: label(match[1].split("|", 1)[-1]), str(text))
    text = re.sub(r"[/\\]?(?:[\w.~:-]+[/\\])+[^\s,;!?()\[\]]+", lambda match: label(match[0]), text)
    text = re.sub(r"\.md\b", "", text, flags=re.I)
    text = re.sub(r"^\s*(?:(?:[a-z][a-z_-]*|G-\d+):\s*)+", "", text, flags=re.I)
    ids = r"\b(?:G-\d+|T-\d{3,})\b"

    def named(match):
        title = (names or {}).get(match[0])
        return readable(title) if title else "the goal" if match[0].startswith("G-") else "the task"

    text = re.sub(ids, named, text)
    text = re.sub(r",\s*(?:held|filed|messaged|delivered|nothing)[.!]?\s*$", "", text, flags=re.I)
    return " ".join(text.split())


def _unasked(task: tasks.Task) -> bool:
    source = fm.field(task.body, "Source") or ""
    quoted = bool(re.search(r'["“][^"”\n]+["”]', source))
    return bool(task.goal and not quoted and not task.handoff and task.task_state != "yours"
                and task.state.get("unasked") == "true")


def receptions_for(config: Config, item: dict, rows=None, *, vault: Vault | None = None) -> list[reception.Row]:
    from .state import _source
    vault = vault or Vault(config.vault_path)
    found = []
    for row in rows if rows is not None else [row for _, row in reception.rows(config)]:
        source = _source(vault, row.source)
        if source is None:
            continue
        same_source = _key(source) == _key(item["link"])
        if item["id"].startswith("offer:"):
            matches = row.id == item["id"].removeprefix("offer:")
        elif item["kind"] == "task":
            matches = (needs.task_of(source) or source) == item["id"]
        elif item["id"].startswith("work:"):
            slug = item["link"].removeprefix("work:")
            matches = row.source in (slug, "work:" + slug, "works/" + slug)
        elif item["id"].startswith("idea:"):
            proposition = item["detail"][0] if item["detail"] else item["text"]
            matches = same_source and fm.plain(readable(proposition)).startswith(fm.plain(readable(row.what)))
        elif item["id"].startswith("draft:"):
            matches = same_source
        else:
            matches = False
        if matches:
            found.append(row)
    return sorted(found, key=lambda row: (row.at, row.id))


def _landed(outcome: str, fallback: str) -> str:
    head = outcome.partition(":")[0]
    if outcome == "declined:not-now":
        return "held for you"
    if head == "declined":
        return "declined"
    if head in ("used", "liked"):
        return "done"
    return fallback


def build(config: Config, gid: str | None = None, *, weeks: int | None = 2, vault: Vault | None = None) -> dict:
    from .state import _public, _rows, _source
    if weeks is not None and weeks < 1:
        raise ValueError("--weeks must be positive")
    if config.edition != "personal":
        return {}
    entries = goals.load(config) + goals.archived(config)
    known = {goal.gid: goal for goal in entries}
    if gid is not None and gid not in known:
        raise ValueError(f"{gid} is not a goal; use `brain goals list`")
    out = {ident: [] for ident in known if gid is None or ident == gid}
    if not out:
        return out
    vault = vault or Vault(config.vault_path)
    try:
        floor = (config.today() - timedelta(weeks=weeks) + timedelta(days=1)).isoformat() if weeks is not None else None
    except (OverflowError, ValueError):
        raise ValueError("--weeks is outside the supported date range") from None
    corrections = goals.capture(config)["corrections"]
    reception_rows = [row for path, row in reception.rows(config)
                      if _public(vault, path.relative_to(config.vault_path).as_posix())
                      and _source(vault, row.source) is not None and _at(config, row.at) is not None]
    used_rows, source_goals, listed_sources = set(), {}, {}
    for source, note in vault.notes.items():
        if vault.kind_for(source) == "hub" and _public(vault, source) and (goal := fm.hub_goal(note.body)) in out:
            source_goals.setdefault(_key(source), set()).add(goal)
    task_entries = tasks.all_tasks(config)
    task_unasked = {task.tid: _unasked(task) for task in task_entries}
    names = {**{ident: goal.title for ident, goal in known.items()}, **{task.tid: task.title for task in task_entries}}

    def attach(item, detail, at):
        item["detail"].extend(line for block in detail for line in block.splitlines() if line.strip())
        item["at"] = max(filter(None, (item["at"], _at(config, at))), default=None)

    def add(goal, ident, at, kind, text, state, link, detail, unasked, *, owner=None):
        if goal not in out:
            return
        if any(item["id"] == ident for item in out[goal]):
            return
        item = {"id": ident, "at": _at(config, at), "kind": kind, "text": readable(text, names),
                "state": state, "link": link.split("#", 1)[0], "detail": detail, "unasked": unasked}
        for row in receptions_for(config, item, reception_rows, vault=vault):
            item["detail"].append(row.render())
            item["state"] = _landed(row.outcome, state)
            item["at"] = max(filter(None, (item["at"], _at(config, row.at))))
            used_rows.add(row.id)
        if corrected := corrections.get(goal, {}).get(ident):
            item["corrected"] = {"at": corrected["at"], "why": corrected["why"]}
            item["state"] = "declined"
        item["detail"] = [line for block in item["detail"] for line in block.splitlines() if line.strip()]
        if item["at"] and item["at"][:10] > config.today().isoformat():
            return
        target = next((item for item in out[goal] if item["id"] == owner), None)
        if target is not None:
            attach(target, [text, *item["detail"]], item["at"])
            listed_sources[goal, _key(link)] = target
            return
        out[goal].append(item)
        if kind == "task" or ident.startswith("draft:"):
            listed_sources[goal, _key(link)] = item

    progress = continuation._progress(config)
    for task in task_entries:
        archive = tasks.archive_path(config, task.tid)
        registered = vault.notes.get(config.agent("Tasks.md"))
        in_register = registered and any(t.tid == task.tid for t in tasks.parse_register(registered.body)[1])
        source = config.agent("Tasks.md") if in_register else archive.relative_to(config.vault_path).as_posix()
        if task.goal not in out or not _public(vault, source) or (archive.exists() and not _public(vault, archive.relative_to(config.vault_path).as_posix())):
            continue
        results = [row for row in progress.get(task.tid, {}).get("results", []) or []
                   if isinstance(row, dict) and isinstance(row.get("text"), str)]
        if not results and (checkpoint := continuation._checkpoint(task.tid)):
            results = [checkpoint]
        result_lines = [f"{row.get('at') or ''} · {row['text']}" for row in reversed(results)]
        detail = result_lines + [line for line in task.body.splitlines() if line.strip()]
        dates = [*task.uses, *task.uses_inferred, *(row.get("at") for row in results)]
        for line in task.body.splitlines():
            if re.match(r"- \*\*(?:Completed|Dropped|Reviewed|Reopened|Status|Result|Latest evidence):", line):
                dates.extend(tasks.DATE_RE.findall(line))
        dated = [value for raw in dates if (value := _at(config, raw)) and value[:10] <= config.today().isoformat()]
        status = (tasks.closing(task)[1] if not tasks.is_open(task) else None)
        status = status or (results[-1]["text"] if results else None) or fm.field(task.body, "Result", "Status")
        status = next((line.strip() for line in (status or "").splitlines() if line.strip()), "")
        text = readable(task.title, names) + (f" — {readable(status, names)}" if status else "")
        if task.delivers:
            detail.append(f"Delivers: {task.delivers}")
        if home := runtime.configured():
            if task.work and works.SLUG.fullmatch(task.work):
                history = home / "works" / task.work / "HISTORY.md"
                if history.exists():
                    sections = re.split(r"(?=^## )", history.read_text(encoding="utf-8"), flags=re.M)
                    detail += [line for section in reversed(sections) if re.search(r"\b" + re.escape(task.tid) + r"\b", section.split("\n", 1)[0])
                               for line in section.splitlines() if line.strip()]
        state = {"completed": "done", "cancelled": "declined", "set_aside": "declined",
                 "waiting_for_owner": "held for you", "yours": "held for you"}.get(task.task_state, "in progress")
        add(task.goal, task.tid, max(dated, default=None), "task", text, state, task.tid, detail, task_unasked[task.tid])

    for source, note in sorted(vault.notes.items()):
        if not _public(vault, source) or not (note.data.get("kind") == "draft" or vault.kind_for(source) == "draft"):
            continue
        goal = note.data.get("goal") or fm.field(note.body, "Goal")
        if goal in out:
            source_goals.setdefault(_key(source), set()).add(goal)
        leading = re.match(r"\d{4}-\d{2}-\d{2}", note.stem)
        at = note.data.get("created") or (leading[0] if leading else None)
        text = note.data.get("index_line") or re.sub(r"^\d{4}-\d{2}-\d{2} ", "", note.stem)
        add(goal, _id("draft", source), at, "made", str(text), "held for you", source,
            [line for line in note.body.splitlines() if line.strip()], task_unasked.get(note.data.get("task"), True),
            owner=note.data.get("task"))

    home = runtime.configured()
    for work in works.parse(works.library() or ""):
        goal = works.goal_for(work.slug) or work.goal
        detail = [f"{work.title} · {work.form} · {work.status}"]
        if home is not None and works.SLUG.fullmatch(work.slug):
            path = home / "works" / work.slug / "HISTORY.md"
            if path.exists():
                detail += [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        state = "held for you" if work.status in ("draft", "kept") else "in progress"
        if isinstance(work.opened, date):
            state = "done"
        ident = "work:" + work.slug
        metadata = home / "works" / work.slug / "GOAL.md" if home is not None else None
        owner = fm.read(metadata)[0].get("task") if metadata and metadata.exists() else None
        linked = [task for task in task_entries if task.work == work.slug or task.tid == owner]
        add(goal, ident if len(ident) <= 80 else _id("work", work.slug), work.created, "made", work.title,
            state, work.slug, detail, any(task_unasked[task.tid] for task in linked) if linked else True,
            owner=next((task.tid for task in linked if task.goal == goal), None))

    candidates = []
    note = vault.notes.get(config.agent("Ideas.md"))
    if note and _public(vault, config.agent("Ideas.md")):
        candidates += ideas.parse_section(note.body)
    for source, note in sorted(vault.notes.items()):
        if vault.kind_for(source) == "hub" and _public(vault, source):
            candidates += needs.improvements(note.body, source, config.today())
    responses = ideas._responses(config)
    for idea in candidates:
        link = _source(vault, idea.source)
        if link is None:
            continue
        answers = [(day, answer) for day, answer, text, ident in responses
                   if ident == idea.id or not ident and fm.plain(text) == fm.plain(idea.text)]
        answer_at, answer = max(answers, default=(None, None), key=lambda value: value[0] or date.min)
        state = "done" if answer == "yes" else "declined" if answer in ("no", "stop-asking") else "held for you"
        detail = [idea.text] + ([idea.detail] if idea.detail else []) + ([f"{answer_at} · {answer}"] if answer else [])
        add(idea.goal, "idea:" + idea.id, answer_at or idea.since, "offer", idea.text, state, link, detail, True)
    for path in sorted((config.vault_path / config.agent("Answered")).glob("*.md")):
        if not _public(vault, path.relative_to(config.vault_path).as_posix()):
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.removeprefix("- ").split(" · ")
            if len(parts) > 1 and parts[1] in ("loop done", "loop dropped"):
                continue
            ident = next((part[3:] for part in parts if re.fullmatch(r"id:[0-9a-f]{12}", part)), None)
            if not ident or any(item["id"] == "idea:" + ident for items in out.values() for item in items):
                continue
            text = parts[3] if len(parts) > 3 and parts[1].startswith("dismissed ") else parts[2] if len(parts) > 2 else ""
            match = next(((day, answer) for day, answer, value, rid in reversed(responses) if rid == ident), None)
            sources = re.findall(r"\[\[([^]]+)\]\]", line)
            if not match or not sources or (link := _source(vault, sources[-1])) is None:
                continue
            goal = next((part[5:] for part in parts if re.fullmatch(r"goal:G-\d+", part)), None)
            if goal:
                add(goal, "idea:" + ident, match[0], "offer", text, "done" if match[1] == "yes" else "declined",
                    link, [text, line], True)

    for at, source, _, raw, row in _rows(vault):
        if row.routine.casefold().removeprefix(config.skill_prefix) not in ("explore", "improve"):
            continue
        if row.outcome == "nothing":
            continue
        for goal in set(goals.ID.findall(row.account + " " + row.next)):
            matches = []
            for (target_goal, target_source), item in listed_sources.items():
                if target_goal != goal:
                    continue
                references = {target_source, target_source.rsplit("/", 1)[-1]}
                if any(re.search(r"(?<![\w-])" + re.escape(reference) + r"(?![\w-])", row.account, re.I)
                       for reference in references):
                    if item not in matches:
                        matches.append(item)
            if matches:
                if (dated := _at(config, at)) and dated[:10] <= config.today().isoformat():
                    for item in matches:
                        attach(item, [raw], dated)
                continue
            state = "done" if row.outcome == "done" else "held for you" if row.outcome == "waiting" else "in progress"
            add(goal, _id("research", source + "\n" + raw), at, "research", row.account, state, source, [raw], True)
    for row in reception_rows:
        if row.id in used_rows:
            continue
        link = _source(vault, row.source)
        for goal in source_goals.get(_key(link), set()):
            add(goal, "offer:" + row.id, row.at, "offer", row.what, _landed(row.outcome, "held for you"), link, [], True)
    for items in out.values():
        items[:] = [item for item in items if not floor or item["at"] is not None and item["at"][:10] >= floor]
        for item in items:
            item["detail"] = list(dict.fromkeys(item["detail"]))
        items.sort(key=lambda item: (item["at"] or "", item["id"]), reverse=True)
    return out
