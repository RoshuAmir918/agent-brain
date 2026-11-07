"""The personal seed's reading list and hand-off: what the owner's synced mail and calendar hold, sorted by whose it
is, for the seed skill to read for the first portrait. The reading itself is the mail and calendar sources'."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from . import channel_files, tasks

PERSONAL_CAPS = {"sent": 100, "transactional": 150, "events": 100, "person": 100}

def personal(vault, today: date, account: str = "all", days: int | None = None) -> dict:
    """The owner's synced mail and calendar as a reading list for the seed: their own side and other people's, sorted
    and capped, without exposing mail bodies. The reading is the mail and calendar sources'; this is what to read."""
    from .sources import calendar, mail
    if days is not None and days < 0:
        raise SystemExit("brain portrait seed: --days must be zero or more")
    folders, owners = mail.owned(vault)
    account = account.casefold()
    if account != "all" and account not in owners:
        raise SystemExit(f"brain portrait seed: {account} is not an owner account")
    folders = [(name, folder) for name, folder in folders if account == "all" or account == name]
    backfill = [name for name, folder in folders
                if any((folder / "backfill").glob("mail/*/*.md")) or any((folder / "backfill").glob("calendar/*.md"))]
    folders = [(name, folder) for name, folder in folders if mail.files(folder) or calendar.files(folder)]
    if not folders:
        raise SystemExit("brain portrait seed: no owner account has synced mail or calendar files"
                         + (f" for {account}" if account != "all" else ""))
    floor = today - timedelta(days=days) if days is not None else None
    dates: list[str] = []

    def inside(day: date) -> bool:
        dates.append(day.isoformat())
        return floor is None or floor <= day <= today

    mails, events, problems = [], [], []
    for folder in folders:   # each account's mail, then its calendar, as the reading list has always reported them
        found, unreadable = mail.read(vault, [folder], owners)
        mails += found
        problems += unreadable
        found, unreadable = calendar.read(vault, [folder])
        events += found
        problems += unreadable
    sent, transactional, people = [], {}, []
    other = dict(forwarded=0, cc=0, group=0)
    for item in mails:
        if not inside(item.received.date()) or item.excluded:
            continue
        entry = {"kind": item.kind, "whose": item.whose, "date": item.received.isoformat(), "from": item.sender,
                 "subject": item.subject, "path": str(item.path), "account": item.account}
        if item.kind == "sent":
            sent.append(entry | {"to": item.to, "cc": item.cc})
        elif item.kind == "transactional":
            transactional.setdefault(min(item.senders), []).append(entry)
        elif item.kind == "person":
            people.append(entry)
        else:
            other[item.kind] += 1
    accepted = [(event, path, name) for event, ok, path, name in events if inside(event.start.date()) and ok]
    newest = lambda item: (datetime.fromisoformat(item["date"]), item["path"])
    notices = []
    for sender, items in transactional.items():
        items.sort(key=newest, reverse=True)
        notices.append(items[0] | {"from": sender, "count": len(items),
                                   "subjects": list(dict.fromkeys(item["subject"] for item in items))[:3]})
    lists = {"sent": sent, "transactional": notices, "events": calendar.grouped(accepted, today), "person": people}
    omitted = {}
    for kind, items in lists.items():
        items.sort(key=newest, reverse=True)
        omitted[kind] = max(0, len(items) - PERSONAL_CAPS[kind])
        lists[kind] = items[:PERSONAL_CAPS[kind]]
    return {"window": {"from": floor.isoformat() if floor else min(dates, default=today.isoformat()),
                       "to": today.isoformat() if floor else max(dates, default=today.isoformat())},
            "accounts": [name for name, _ in folders],
            "backfillAccounts": backfill,
            "own": {kind: lists[kind] for kind in ("sent", "transactional", "events")},
            "other": other | {"person": lists["person"]}, "omitted": omitted, "problems": problems}

def render_personal(data: dict) -> str:
    def line(value):
        return " ".join(str(value).split())
    out = [f"{data['window']['from']} to {data['window']['to']} · {', '.join(data['accounts'])}"]
    if data["backfillAccounts"]:
        out.append("Backfill read: " + ", ".join(data["backfillAccounts"]))
    for kind, items in data["own"].items():
        out += ["", f"own · {kind}:"]
        for item in items:
            if kind == "events":
                detail = f"{item['title']} ×{item['count']} · next {item['next'] or 'none'} · last {item['last'] or 'none'} · {item['location']}"
            elif kind == "transactional":
                detail = f"{item['from']} ×{item['count']} · " + "; ".join(item["subjects"])
            else:
                detail = f"{item['from']} → {item['to']} · {item['subject']}"
            out.append(line(f"- {item['date']} · {detail} · {item['path']}"))
    out += ["", "other (never tellable): " + ", ".join(f"{data['other'][kind]} {kind}" for kind in ("forwarded", "cc", "group"))]
    out += [line(f"- {item['date']} · {item['from']} · {item['subject']} · {item['path']}") for item in data["other"]["person"]]
    out += ["Omitted over caps: " + ", ".join(f"{count} {kind}" for kind, count in data["omitted"].items())]
    out += [f"problem: {problem}" for problem in data["problems"]]
    return "\n".join(out)

def clear_backfill(vault, account: str = "all") -> dict:
    """Remove only synced backfill mail and calendar files, keeping the completion marker."""
    account = account.casefold()
    if account != "all" and account not in channel_files.owner_accounts(vault):
        raise SystemExit(f"brain portrait seed: {account} is not an owner account")
    removed = {}
    for name, folder in channel_files.synced_accounts(vault):
        if account != "all" and account != name:
            continue
        counts = removed[name] = dict(mail=0, calendar=0)
        backfill = folder / "backfill"
        if folder.is_symlink() or backfill.is_symlink():
            continue
        for kind, glob, shape in (("mail", "*/*.md", r"\d{4}-\d{2}-\d{2}/[^/]+\.md"),
                                  ("calendar", "*.md", r"\d{4}-\d{2}-\d{2}\.md")):
            root = backfill / kind
            if root.is_symlink():
                continue
            for path in sorted(root.glob(glob)):
                if (re.fullmatch(shape, path.relative_to(root).as_posix()) and path.is_file()
                        and not path.is_symlink() and not path.parent.is_symlink()):
                    path.unlink()
                    counts[kind] += 1
            if kind == "mail":
                for day in sorted(root.glob("*")):
                    if (re.fullmatch(r"\d{4}-\d{2}-\d{2}", day.name) and not day.is_symlink()
                            and day.is_dir() and not any(day.iterdir())):
                        day.rmdir()
    return removed

SEED_SOURCE = re.compile(r'^- \*\*Source:\*\* "seed:([^"\s]+)"$', re.M)

def seeds(vault) -> list[dict]:
    """Each owner account a seed has read or is reading: the last day a Seeds note recorded reading it, and the open
    task reading it now, so the owner's app can say the first portrait is on its way."""
    config = vault.config
    read: dict[str, str] = {}
    prefix = config.agent("Seeds/")
    for rel, note in vault.notes.items():
        account, when = str(note.data.get("channel") or "").casefold(), str(note.data.get("read") or "")
        if rel.startswith(prefix) and account and re.fullmatch(r"\d{4}-\d{2}-\d{2}", when):
            read[account] = max(read.get(account, ""), when)
    reading = _reading(config)
    return [{"account": account, "read": read.get(account), "task": reading.get(account)}
            for account in sorted(read.keys() | reading.keys())]

def _reading(config) -> dict[str, str]:
    if not (config.vault_path / config.agent("Tasks.md")).exists():
        return {}
    registered, archived = tasks.load_tasks(config)
    found: dict[str, str] = {}
    for task in registered + archived:
        if tasks.is_open(task) and (match := SEED_SOURCE.search(task.body)):
            found.setdefault(match[1].casefold(), task.tid)
    return found

def hand_off_seed(vault, account: str, today: date) -> dict:
    """A newly connected owner account gets one handed-off task that runs the seed for it, unless a seed has read it or
    is reading it. The caller holds the vault lock."""
    config = vault.config
    if config.edition != "personal":
        raise ValueError("a seed task is for a person's own accounts; an organisation seeds with brain consult seed")
    account = account.strip().casefold()
    if not channel_files.EMAIL.fullmatch(account):
        raise ValueError("--hand-off needs one account address, not all")
    known = next((row for row in seeds(vault) if row["account"] == account), None)
    if known and known["task"]:
        return {"account": account, "task": known["task"], "status": "reading"}
    if known and known["read"]:
        return {"account": account, "task": None, "status": "read"}
    task = tasks.new_task(config, f"Learn {config.human_name} from {account}", f"seed:{account}", values={
        "delivers": f"the portrait and tellable facts from {account}, a Seeds note for it, and the reveal confirm",
        "next": f"until $AGENT_HOME/channels/{account}/backfill/.done exists the first sync is still running: set "
                "--resume an hour out and end the chunk. Then register the account in Channels.md if it is not there, "
                f"load the seed skill and run its personal pass for --account {account}"})
    tasks.hand_off(config, task.tid, today)
    return {"account": account, "task": task.tid, "status": "opened"}
