"""Process audit: how the routines behave, as numbers. Never note contents, and no titles or paths of knowledge notes."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from importlib.resources import files
from pathlib import Path

from . import digest, install, session_tools
from .config import Config
from .health import STAGE_RE, git
from .vault import Vault

DATED = re.compile(r"^\d{4}-\d{2}-\d{2}")
MONTHLY = re.compile(r"^\d{4}-\d{2}$")
LONG_EPISODE = 1500


def words(text: str) -> int:
    return len(text.split())


def mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 1) if values else 0.0


def notes_by_kind(vault: Vault) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for rel, note in vault.notes.items():
        if rel.startswith(("Conversations/Sessions/", "Conversations/Digests/")):
            continue   # exports, not notes
        entry = out.setdefault(vault.kind_for(rel), {"count": 0, "words": 0})
        entry["count"] += 1
        entry["words"] += words(note.body)
    return dict(sorted(out.items()))


def folders(vault: Vault, config: Config) -> list[dict]:
    """Top-level folders under the agent's directory: a folder of dated files is the file-per-run smell."""
    root = vault.root / config.agent_dir
    out = []
    for folder in sorted(path for path in root.iterdir() if path.is_dir()) if root.is_dir() else []:
        prefix = config.agent(folder.name) + "/"
        notes = [(rel, note) for rel, note in vault.notes.items() if rel.startswith(prefix)]
        out.append({"folder": folder.name, "files": len(notes),
                    "dated_files": sum(1 for rel, _ in notes if DATED.match(Path(rel).stem)),
                    "words": sum(words(note.body) for _, note in notes)})
    return out


def ledgers(vault: Vault) -> list[dict]:
    """Monthly ledgers: rows and words per row say whether a routine writes receipts or disclaimers."""
    out = []
    for rel, note in sorted(vault.notes.items()):
        if vault.kind_for(rel) != "log" or not MONTHLY.match(Path(rel).stem):
            continue
        rows = [line for line in note.body.splitlines() if line.strip() and not line.startswith(("#", "---", "Format:"))]
        out.append({"ledger": rel, "rows": len(rows), "mean_words_per_row": mean([words(row) for row in rows])})
    return out


def episodes(vault: Vault, today: date, days: int) -> dict:
    since = (today - timedelta(days=days)).isoformat()
    recent = [note for rel, note in vault.notes.items()
              if vault.kind_for(rel) == "episode" and DATED.match(Path(rel).stem) and Path(rel).stem[:10] >= since]
    sizes = [words(note.body) for note in recent]
    return {"count": len(recent), "mean_words": mean(sizes), "over_1500_words": sum(1 for size in sizes if size > LONG_EPISODE),
            "logs_under_conversations": sum(1 for rel in vault.notes if rel.startswith("Conversations/") and vault.kind_for(rel) == "log"
                                            and not rel.startswith(("Conversations/Sessions/", "Conversations/Digests/")))}


def context(vault: Vault, config: Config) -> dict:
    hot = vault.notes.get("Hot Set.md")
    warm = vault.notes.get("Warm Index.md")
    compiled = config.repo / ".hermes.md"
    return {"hot_set_words": words(hot.body) if hot else 0,
            "warm_index_entries": sum(1 for line in warm.body.splitlines() if line.startswith("- ")) if warm else 0,
            "compiled_context_words": words(compiled.read_text(encoding="utf-8", errors="replace")) if compiled.exists() else 0}


def skills(config: Config) -> dict:
    """Which day routines the agent has changed from the package defaults, by name only."""
    drifted, own, day = [], [], 0
    rendered = {}
    for routine in files("brain").joinpath("skills").iterdir():
        template = routine.joinpath("SKILL.md")
        if template.is_file():
            text = template.read_text(encoding="utf-8")
            for token, value in config.template_values().items():
                text = text.replace("{" + token + "}", value)
            rendered[routine.name] = text
    if config.skills_path.is_dir():
        for folder in sorted(config.skills_path.iterdir()):
            skill = folder / "SKILL.md"
            if not skill.is_file():
                continue
            name = folder.name[len(config.skill_prefix):] if folder.name.startswith(config.skill_prefix) else folder.name
            if name not in rendered:
                own.append(name)
            elif name not in install.CORE:
                day += 1
                if skill.read_text(encoding="utf-8") != rendered[name]:
                    drifted.append(name)
    return {"day_routines": day, "changed_from_default": drifted, "own_routines": own}


def routines(directory: Path, today: date, days: int) -> list[dict]:
    """Each cron job's runs in the window, from the session store's own counters: the cost of a routine, not its words."""
    since = datetime.combine(today - timedelta(days=days), datetime.min.time()).timestamp()
    jobs: dict[str, list[dict]] = {}
    for path in digest.jsonl_sources(directory) if directory.is_dir() else []:
        for _, record in digest.json_records(path):
            containers = digest.session_maps(record)
            if session_tools.session_kind(containers, digest.message_list(containers)) != "routine":
                continue
            started = record.get("started_at")
            if not isinstance(started, (int, float)) or started < since:
                continue
            title = str(record.get("title") or "")
            job = title.split(" · ")[0].strip() if " · " in title else re.sub(r"_\d{8}_\d{6}$", "", digest.record_id(record))
            jobs.setdefault(job or "unnamed", []).append(record)
    out = []
    for job, runs in sorted(jobs.items()):
        def numbers(key):
            return [float(run[key]) for run in runs if isinstance(run.get(key), (int, float))]
        minutes = [(run["ended_at"] - run["started_at"]) / 60 for run in runs
                   if isinstance(run.get("ended_at"), (int, float)) and isinstance(run.get("started_at"), (int, float))]
        reasons: dict[str, int] = {}
        for run in runs:
            reasons[str(run.get("end_reason") or "unknown")] = reasons.get(str(run.get("end_reason") or "unknown"), 0) + 1
        out.append({"job": job, "runs": len(runs),
                    "last": date.fromtimestamp(max(numbers("started_at"))).isoformat(),
                    "mean_messages": mean(numbers("message_count") or [float(len(digest.message_list(digest.session_maps(run)))) for run in runs]),
                    "mean_tool_calls": mean(numbers("tool_call_count")),
                    "mean_input_tokens": mean(numbers("input_tokens")), "mean_output_tokens": mean(numbers("output_tokens")),
                    "mean_minutes": mean(minutes), "end_reasons": reasons})
    return out


def commits(repo: Path, days: int) -> dict[str, int]:
    out: dict[str, int] = {}
    for subject in (git(repo, "log", f"--since={days} days ago", "--format=%s") or "").splitlines():
        match = STAGE_RE.match(subject)
        key = match.group(1) if match else (re.match(r"^([\w-]+)[(:]", subject).group(1) if re.match(r"^([\w-]+)[(:]", subject) else "other")
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def report(config: Config, today: date | None = None, days: int = 14, sessions_dir: Path | None = None) -> dict:
    today = today or config.today()
    vault = Vault(config.vault_path)
    directory = sessions_dir if sessions_dir is not None else digest.default_sessions_dir(config)
    return {"agent": config.agent_name, "date": today.isoformat(), "days": days,
            "notes": notes_by_kind(vault), "folders": folders(vault, config), "ledgers": ledgers(vault),
            "episodes": episodes(vault, today, days), "context": context(vault, config), "skills": skills(config),
            "routines": routines(directory, today, days), "commits": commits(config.repo, days)}


def render(data: dict) -> str:
    lines = [f"# Audit: {data['agent']}, {data['date']}, last {data['days']} days", "",
             "Notes by kind: " + ", ".join(f"{kind} {entry['count']} ({entry['words']} words)" for kind, entry in data["notes"].items()),
             "Folders: " + ", ".join(f"{f['folder']} {f['files']} files, {f['dated_files']} dated, {f['words']} words" for f in data["folders"]),
             "Ledgers: " + ", ".join(f"{l['ledger']} {l['rows']} rows at {l['mean_words_per_row']} words" for l in data["ledgers"])]
    ep = data["episodes"]
    lines.append(f"Episodes: {ep['count']} in window, mean {ep['mean_words']} words, {ep['over_1500_words']} over {LONG_EPISODE}, "
                 f"{ep['logs_under_conversations']} logs filed under Conversations")
    cx = data["context"]
    lines.append(f"Context: hot set {cx['hot_set_words']} words, warm index {cx['warm_index_entries']} entries, compiled context {cx['compiled_context_words']} words")
    sk = data["skills"]
    lines.append(f"Skills: {sk['day_routines']} day routines, changed from default: {', '.join(sk['changed_from_default']) or 'none'}; "
                 f"own: {', '.join(sk['own_routines']) or 'none'}")
    lines.append("Routines:")
    for job in data["routines"]:
        reasons = ", ".join(f"{reason} {count}" for reason, count in job["end_reasons"].items())
        lines.append(f"- {job['job']}: {job['runs']} runs, last {job['last']}, {job['mean_messages']} messages, {job['mean_tool_calls']} tool calls, "
                     f"{job['mean_input_tokens']:.0f} in / {job['mean_output_tokens']:.0f} out tokens, {job['mean_minutes']} min; ends: {reasons}")
    if not data["routines"]:
        lines.append("- none recorded")
    lines.append("Commits: " + ", ".join(f"{key} {count}" for key, count in data["commits"].items()))
    return "\n".join(lines) + "\n"


def to_json(data: dict) -> str:
    return json.dumps(data, indent=2)
