"""What the owner asked ChatGPT and Claude, from the exports they handed over (docs/image.md section 3): one file per
provider at `$AGENT_HOME/channels/chats/<provider>.jsonl`, a conversation a line, `{"id", "title", "at", "said"}`,
where `said` holds only the owner's own messages. The assistant's replies never arrive: they are the model's words, not
the owner's."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path

from ..evidence import Evidence
from . import Context, Nothing, Source, hints

SOURCE = Source(id="chats", editions=("personal",), runs="runtime", where="own", rank=9, files=("*.jsonl",),
                facets=("work", "reading & ideas", "health", "money", "places", "play", "food & drink", "music"),
                sensitive=True, about="what the owner asked ChatGPT and Claude, in their own words")

PROVIDERS = {"chatgpt": "ChatGPT", "claude": "Claude"}
READ = 300   # conversations the reading lists: the most recent, then the longest of the rest
SAID = 600   # characters of the owner's words shown per conversation; the file holds them all


def folder(home: Path | None = None) -> Path | None:
    from .. import runtime
    home = home or runtime.configured()
    return home / "channels" / "chats" if home else None


def provider(path: Path) -> str:
    if path.suffix != ".jsonl" or path.stem not in PROVIDERS:
        raise ValueError(f"a chats file is chatgpt.jsonl or claude.jsonl, not {path.name}")
    return path.stem


def load(path: Path) -> tuple[list[dict], list[str]]:
    """The conversations in one file, newest first, and the lines that could not be read (by number, never content)."""
    conversations, problems = [], []
    with path.open(encoding="utf-8") as lines:
        for number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                said = [text.strip() for text in item["said"] if isinstance(text, str) and text.strip()]
                if not said:
                    continue
                conversations.append({"id": str(item["id"]), "title": " ".join(str(item.get("title") or "").split()),
                                      "at": date.fromisoformat(item["at"]), "said": said})
            except (ValueError, TypeError, KeyError):
                problems.append(f"line {number} is not a conversation")
    conversations.sort(key=lambda c: c["at"], reverse=True)
    return conversations, problems


def pointer(name: str, conversation: dict) -> str:
    return f"chats:{name}/{hashlib.sha256(conversation['id'].encode()).hexdigest()[:8]}"


def reading(path: Path) -> dict:
    """What a seed reads first: how much there is and when, then the conversations worth reading, each with the start
    of what the owner said. The file is there for the rest."""
    name = provider(path)
    conversations, problems = load(path)
    size = lambda c: sum(len(text) for text in c["said"])
    recent = conversations[:READ // 2]
    longest = sorted(conversations[READ // 2:], key=size, reverse=True)[:READ - len(recent)]
    months = Counter(c["at"].strftime("%Y-%m") for c in conversations)
    shown = lambda c: (lambda words: words[:SAID] + (" …" if len(words) > SAID else ""))(" / ".join(c["said"]))
    return {
        "provider": name, "file": path.as_posix(), "conversations": len(conversations),
        "messages": sum(len(c["said"]) for c in conversations),
        "from": conversations[-1]["at"].isoformat() if conversations else None,
        "to": conversations[0]["at"].isoformat() if conversations else None,
        "months": dict(sorted(months.items())),
        "read": [{"at": c["at"].isoformat(), "title": c["title"], "messages": len(c["said"]), "said": shown(c),
                  "pointer": pointer(name, c)} for c in recent + longest],
        "problems": problems,
    }


def render(data: dict) -> str:
    out = [f"{PROVIDERS[data['provider']]}: {data['conversations']} conversations, {data['messages']} messages by the owner"
           + (f", {data['from']} to {data['to']}" if data["from"] else "") + f" · {data['file']}"]
    if data["months"]:
        out.append("By month: " + ", ".join(f"{month} {n}" for month, n in data["months"].items()))
    if data["read"]:
        out += ["", "Read, the most recent then the longest:"]
        out += [f"- {c['at']} · {c['title'] or 'untitled'} · {c['messages']} · {c['pointer']}\n  {c['said']}" for c in data["read"]]
    out += [f"problem: {problem}" for problem in data["problems"]]
    return "\n".join(out)


def rows(path: Path) -> list[Evidence]:
    """A conversation a row, dated the day it began: what the owner turned to a model about, in its title's words."""
    name = provider(path)
    conversations, _ = load(path)
    return [Evidence(c["at"], "asked", f"{PROVIDERS[name]}: {c['title'] or c['said'][0][:120]}", "own",
                     hints(f"{c['title']} {c['said'][0][:500]}"), pointer(name, c), {"messages": str(len(c["said"]))})
            for c in conversations]


def extract(ctx: Context) -> list[Evidence]:
    root = ctx.input or folder()
    files = sorted(root.glob("*.jsonl")) if root and root.is_dir() else []
    if not files:
        raise Nothing("no chat export handed over")
    found = [row for path in files if path.stem in PROVIDERS for row in rows(path)]
    return [row for row in found if not ctx.since or row.date >= ctx.since]


def clear(path: Path) -> bool:
    """The export is for the seed: once its Seeds note records what it read, the owner's words leave the workspace."""
    provider(path)
    if not path.is_file():
        return False
    path.unlink()
    return True
