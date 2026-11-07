"""The reception ledger: one row per thing offered to the owner, and how it landed, so the agent learns what to offer
and the portrait's guesses are tested. Callers hold the vault lock; nothing here takes it."""
from __future__ import annotations

import re
import secrets
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from . import fm
from .config import Config

KINDS = ("message", "brief", "idea", "draft", "gesture", "letter", "work", "ask")
# delivered: the runtime saw it reach a surface that reports opens, so silence there is a signal
OUTCOMES = ("pending", "delivered", "opened", "used", "liked", "declined", "ignored", "unknown")
SETTLE_DAYS = 7
HEADER = "---\nkind: log\ntier: cold\n---\n# Reception\n\nOne row per thing offered, and how it landed: time, id, kind, what, outcome, source, the portrait line it served.\n\n"
ROW = re.compile(r"^- (?P<at>\d{4}-\d{2}-\d{2} \d{2}:\d{2}) · (?P<id>[0-9a-f]{6}) · (?P<kind>[a-z]+) · (?P<what>.+?) · "
                 r"(?P<outcome>[a-z]+(?::[a-z-]+)?) · \[\[(?P<source>[^\]]*)\]\](?: · portrait:(?P<portrait>[0-9a-f]{4}))?\s*$")


@dataclass
class Row:
    at: str
    id: str
    kind: str
    what: str
    outcome: str
    source: str
    portrait: str = ""

    def render(self) -> str:
        return (f"- {self.at} · {self.id} · {self.kind} · {self.what} · {self.outcome} · [[{self.source}]]"
                + (f" · portrait:{self.portrait}" if self.portrait else ""))


def folder(config: Config) -> Path:
    return config.vault_path / config.agent("Reception")


def _plain(text: str) -> str:
    return " ".join(text.replace("·", "-").split())


def valid_outcome(outcome: str) -> bool:
    head, _, reason = outcome.partition(":")
    return head in OUTCOMES and (head == "declined" or not reason) and (bool(reason) == (":" in outcome))


def rows(config: Config, since: date | None = None) -> list[tuple[Path, Row]]:
    found = []
    for path in sorted(folder(config).glob("*.md")) if folder(config).is_dir() else []:
        if since and path.stem < since.strftime("%Y-%m"):
            continue
        for line in fm.read(path)[1].splitlines():
            if match := ROW.match(line):
                row = Row(**{key: value or "" for key, value in match.groupdict().items()})
                if not since or row.at[:10] >= since.isoformat():
                    found.append((path, row))
    return found


def record(config: Config, kind: str, what: str, source: str, portrait: str = "", at: datetime | None = None) -> str:
    """A `pending` row for something just offered. Returns its id."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    if portrait and not re.fullmatch(r"[0-9a-f]{4}", portrait.lstrip("^")):
        raise ValueError("portrait is a portrait line's four-hex id")
    when = config.local(at)
    taken = {row.id for _, row in rows(config)}
    ident = secrets.token_hex(3)
    while ident in taken:
        ident = secrets.token_hex(3)
    row = Row(when.strftime("%Y-%m-%d %H:%M"), ident, kind, _plain(what)[:200], "pending",
              source.strip().removeprefix("[[").removesuffix("]]"), portrait.lstrip("^"))
    path = folder(config) / f"{when:%Y-%m}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else HEADER
    path.write_text(text.rstrip("\n") + "\n" + row.render() + "\n", encoding="utf-8")
    return ident


def _rewrite(path: Path, old: Row, new: Row) -> None:
    data, body = fm.read(path)
    fm.write(path, data, body.replace(old.render(), new.render(), 1))


def set_outcome(config: Config, ident: str, outcome: str) -> Row:
    if not valid_outcome(outcome):
        raise ValueError(f"outcome must be one of {', '.join(OUTCOMES)}, or declined:<reason>")
    for path, row in rows(config):
        if row.id == ident:
            new = Row(**{**row.__dict__, "outcome": outcome})
            _rewrite(path, row, new)
            return new
    raise ValueError(f"no reception row {ident}")


def answered(config: Config, kind: str, what: str, outcome: str) -> Row | None:
    """The owner answered an idea or an ask: settle its newest open row with the same text, if it has one."""
    needle = _plain(what)[:200].casefold()
    for path, row in reversed(rows(config)):
        if row.kind == kind and row.what.casefold() == needle and row.outcome in ("pending", "delivered", "opened"):
            new = Row(**{**row.__dict__, "outcome": outcome})
            _rewrite(path, row, new)
            return new
    return None


def settle(config: Config, today: date) -> list[Row]:
    """Rows with nothing heard after seven days: `ignored` where the surface would have shown an open, `unknown`
    where it could not."""
    floor = (today - timedelta(days=SETTLE_DAYS)).isoformat()
    settled = []
    for path, row in rows(config):
        if row.outcome in ("pending", "delivered") and row.at[:10] <= floor:
            new = Row(**{**row.__dict__, "outcome": "ignored" if row.outcome == "delivered" else "unknown"})
            _rewrite(path, row, new)
            settled.append(new)
    return settled


def summary(config: Config, today: date, days: int = 30) -> dict:
    """Offered, used, declined, ignored and unknown, by kind and by portrait line. Hit rate is used and liked over
    everything that has landed (pending and delivered are still in the air)."""
    found = [row for _, row in rows(config, today - timedelta(days=days))]

    def tally(group: list[Row]) -> dict:
        heads = Counter(row.outcome.partition(":")[0] for row in group)
        landed = sum(n for head, n in heads.items() if head not in ("pending", "delivered"))
        return {"offered": len(group), **{head: heads.get(head, 0) for head in OUTCOMES},
                "hitRate": round((heads["used"] + heads["liked"]) / landed, 2) if landed else None}

    by = lambda key: {value: tally([row for row in found if getattr(row, key) == value])
                      for value in sorted({getattr(row, key) for row in found if getattr(row, key)})}
    return {"days": days, "all": tally(found), "byKind": by("kind"), "byPortrait": by("portrait")}


def burden(reviews: Path, author: str, today: date, weeks: int = 8) -> dict:
    """Correction burden on one author's merged pull requests, week by week: how many corrections people made per PR,
    and the share merged with none. The author is the intellect's account; bots' comments are not corrections."""
    from . import seed
    loaded, problems = seed.load(reviews, today, weeks * 7)
    mine = [review for review in loaded if review.author.casefold() == author.casefold()]
    start = today - timedelta(days=today.weekday()) - timedelta(weeks=weeks - 1)
    out = []
    for week in range(weeks):
        monday = start + timedelta(weeks=week)
        prs = [r for r in mine if monday.isoformat() <= r.merged <= (monday + timedelta(days=6)).isoformat()]
        corrections = [sum(not seed.BOT.search(by) for _, by, _, _ in r.comments) for r in prs]
        out.append({"week": monday.isoformat(), "prs": len(prs), "corrections": sum(corrections),
                    "perPr": round(sum(corrections) / len(prs), 2) if prs else None,
                    "clean": round(sum(n == 0 for n in corrections) / len(prs), 2) if prs else None})
    return {"author": author, "weeks": out, "problems": problems}


def render_summary(data: dict) -> str:
    line = lambda name, t: (f"- {name}: {t['offered']} offered, {t['used']} used, {t['liked']} liked, {t['declined']} declined, "
                            f"{t['ignored']} ignored, {t['unknown']} unknown, {t['pending'] + t['delivered']} waiting"
                            + (f"; hit rate {t['hitRate']:.0%}" if t["hitRate"] is not None else ""))
    out = [f"Last {data['days']} days", line("all", data["all"]), "", "By kind:"]
    out += [line(kind, t) for kind, t in data["byKind"].items()] or ["- nothing offered"]
    if data["byPortrait"]:
        out += ["", "By portrait line:"] + [line(f"^{ident}", t) for ident, t in data["byPortrait"].items()]
    return "\n".join(out)


def render_burden(data: dict) -> str:
    out = [f"Correction burden on {data['author']}'s merged PRs, by week (corrections by people, not bots):"]
    out += [f"- {w['week']}: " + (f"{w['prs']} PRs, {w['corrections']} corrections, {w['perPr']} per PR, {w['clean']:.0%} with none"
                                  if w["prs"] else "no PRs") for w in data["weeks"]]
    out += [f"problem: {problem}" for problem in data["problems"]]
    return "\n".join(out)
