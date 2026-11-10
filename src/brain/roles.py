"""Roles (docs/roles.md): a kind of job the agent does for an organisation, set by a template in its own folder,
`Roles/<name>/Role.md`, beside the notes that hold what it has learned. People edit both by hand. The notes are the
source of truth: the index under `.brain/roles/` is rebuilt from them and their git history, and a line a person struck
stays struck."""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import date
from importlib.resources import files
from pathlib import Path

from . import fm
from .config import Config

FOLDER = "Roles"
ROLE_NOTE = "Role.md"
NAME = re.compile(r"[A-Za-z][A-Za-z0-9 -]{0,39}")
DOTTED = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*")
# The evidence classes of docs/roles.md section 7, strongest first. A template names which sources feed each.
EVIDENCE = ("corrections", "team", "outcomes", "rulings", "work", "machine", "consults")
BUILTIN_LENS = "solves_the_ask"   # every role's first reviewer: does the work solve what was asked, at its scope
KEYS = ("kind", "template", "tier", "produces", "actions", "corrections", "sources", "notes", "lenses", "decisions",
        "measures")
STRUCK = "Struck"   # the section a person moves a line into to strike it; deleting it in a commit strikes it too
LESSON = re.compile(r"^- (?P<text>.+?)(?: \^(?P<id>[0-9a-f]{4}))?[ \t]*$")
SOURCES = re.compile(r"\(([^()]*(?:\([^()]*\)[^()]*)*)\)(?: \*\((?:candidate|rule[^)]*)\)\*)?$")
STATUS = re.compile(r"\*\((candidate|rule)(?:: ([^)]*))?\)\*")


@dataclass
class Role:
    name: str
    template: str
    produces: str
    actions: list[str]
    corrections: list[dict]
    sources: dict[str, list[str]]
    notes: list[dict]
    lenses: list[dict]
    decisions: list[dict]
    measures: dict[str, str]

    def shapes(self) -> list[str]:
        return [entry["shape"] for entry in self.notes]


@dataclass
class Lesson:
    id: str
    note: str
    section: str
    text: str
    sources: str
    status: str   # candidate, active or rule; a struck line is in the index's struck list instead
    rule_for: str = ""
    by: str = ""   # who last wrote the line, from git blame
    human: bool = False   # a person wrote it: it outranks learned lines and consolidation leaves its words alone
    at: str = ""


@dataclass
class Struck:
    id: str
    text: str
    by: str
    at: str
    how: str   # moved (into the Struck section) or deleted (in a person's commit)
    commit: str = ""


@dataclass
class Index:
    role: str
    head: str
    lessons: list[Lesson] = field(default_factory=list)
    struck: list[Struck] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def folder(config: Config, name: str) -> Path:
    return config.vault_path / FOLDER / name


def names(config: Config) -> list[str]:
    root = config.vault_path / FOLDER
    return sorted(path.parent.name for path in root.glob(f"*/{ROLE_NOTE}")) if root.is_dir() else []


def shipped() -> list[str]:
    return sorted(path.name.removesuffix(".md") for path in files("brain").joinpath("templates", "roles").iterdir()
                  if path.name.endswith(".md"))


def shipped_text(template: str) -> str:
    path = files("brain").joinpath("templates", "roles", f"{template}.md")
    if not path.is_file():
        raise ValueError(f"no shipped role template {template!r}; the package has {', '.join(shipped())}")
    return path.read_text(encoding="utf-8")


def _list(data: dict, key: str, problems: list[str], where: str) -> list:
    value = data.get(key, [])
    if not isinstance(value, list):
        problems.append(f"{where}: {key} must be a list")
        return []
    return value


def _entries(data: dict, key: str, ident: str, problems: list[str], where: str) -> list[dict]:
    """A list of mappings each with an id field and a `covers` (or `holds`) line saying what it is for."""
    out, seen = [], set()
    for entry in _list(data, key, problems, where):
        if not isinstance(entry, dict) or not isinstance(entry.get(ident), str) or not entry[ident]:
            problems.append(f"{where}: each {key} entry needs a {ident}")
            continue
        if entry[ident] in seen:
            problems.append(f"{where}: {key} {entry[ident]!r} is listed twice")
        seen.add(entry[ident])
        out.append({k: str(v) for k, v in entry.items()})
    return out


def parse(text: str, name: str = "", known_sources: set[str] | None = None) -> tuple[Role | None, list[str]]:
    """A role template, and what is wrong with it. Never raises on a person's edit: problems are said, not thrown."""
    where = f"{FOLDER}/{name}/{ROLE_NOTE}" if name else ROLE_NOTE
    try:
        data, _ = fm.split(text)
    except fm.Malformed as error:
        return None, [f"{where}: frontmatter is not valid YAML ({error})"]
    problems: list[str] = []
    if data.get("kind") != "role":
        problems.append(f"{where}: kind must be role")
    problems.extend(f"{where}: unknown key {key!r}; keys are {', '.join(KEYS)}" for key in data if key not in KEYS)
    for key in ("template", "produces"):
        if not isinstance(data.get(key), str) or not data.get(key):
            problems.append(f"{where}: {key} must be a nonempty string")
    actions = [str(action) for action in _list(data, "actions", problems, where)]
    problems.extend(f"{where}: action {action!r} must be a dotted id such as git.open_draft_pr"
                    for action in actions if not DOTTED.fullmatch(action))
    corrections = []
    for entry in _list(data, "corrections", problems, where):
        event = entry.get("event") if isinstance(entry, dict) else None
        weight = entry.get("weight") if isinstance(entry, dict) else None
        if not isinstance(event, str) or not DOTTED.fullmatch(event):
            problems.append(f"{where}: each correction needs an event, a dotted id such as review.comment")
        elif isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight <= 0:
            problems.append(f"{where}: correction {event}: weight must be a positive number")
        else:
            corrections.append({"event": event, "weight": weight})
    sources: dict[str, list[str]] = {}
    raw = data.get("sources", {})
    if not isinstance(raw, dict):
        problems.append(f"{where}: sources must map evidence classes to source ids")
        raw = {}
    for group, ids in raw.items():
        if group not in EVIDENCE:
            problems.append(f"{where}: sources: unknown class {group!r}; classes are {', '.join(EVIDENCE)}")
            continue
        if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
            problems.append(f"{where}: sources.{group} must be a list of source ids")
            continue
        unknown = [item for item in ids if known_sources is not None and item not in known_sources]
        problems.extend(f"{where}: sources.{group}: no source {item!r}; known: {', '.join(sorted(known_sources))}"
                        for item in unknown)
        sources[group] = ids
    notes = _entries(data, "notes", "shape", problems, where)
    problems.extend(f"{where}: note shape {entry['shape']!r} must be a name such as Taste"
                    for entry in notes if not NAME.fullmatch(entry["shape"]) or entry["shape"] in (STRUCK, "Role"))
    lenses = _entries(data, "lenses", "lens", problems, where)
    problems.extend(f"{where}: lens {BUILTIN_LENS} is every role's own; leave it out of the template"
                    for entry in lenses if entry["lens"] == BUILTIN_LENS)
    decisions = _entries(data, "decisions", "type", problems, where)
    measures = data.get("measures", {})
    if not isinstance(measures, dict):
        problems.append(f"{where}: measures must map a name to what it counts")
        measures = {}
    if problems:
        return None, problems
    return Role(name, data["template"], data["produces"], actions, corrections, sources, notes,
                [{"lens": BUILTIN_LENS, "covers": "does the work solve what was asked, at the scope asked; "
                  "cites the ask, always a blocker, and flags growth beyond it"}, *lenses],
                decisions, {str(k): str(v) for k, v in measures.items()}), []


def _known_sources() -> set[str]:
    from .sources import registry
    return set(registry())


def load(config: Config, name: str) -> Role:
    path = folder(config, name) / ROLE_NOTE
    if not path.is_file():
        raise ValueError(f"no role {name!r}; roles: {', '.join(names(config)) or 'none'}")
    role, problems = parse(path.read_text(encoding="utf-8"), name, _known_sources())
    if role is None:
        raise ValueError("; ".join(problems))
    return role


def lessons(body: str, note: str) -> tuple[list[Lesson], list[Lesson], list[str]]:
    """A shape note's lines: the live ones and those under Struck. Any top-level bullet under a `##` heading is a line;
    its sources are its last parenthesised group, a `*(candidate)*` or `*(rule: …)*` marker sets its status."""
    live, struck, problems, section = [], [], [], ""
    for number, raw in enumerate(body.splitlines(), 1):
        if raw.startswith("## "):
            section = raw[3:].strip()
            continue
        match = LESSON.match(raw)
        if not section or not match:
            continue
        text = match["text"]
        status = STATUS.search(text)
        sources = SOURCES.search(text)
        lesson = Lesson(match["id"] or "", note, section, text, sources[1] if sources else "",
                        status[1] if status else "active", status[2] or "" if status else "")
        if not lesson.sources and section != STRUCK:
            problems.append(f"{note}:{number}: a line with no sources; end it with (where it was learned)")
        (struck if section == STRUCK else live).append(lesson)
    return live, struck, problems


def line_id(text: str, taken: set[str]) -> str:
    """A short id from the line's words, salted past any already taken, so the same line gets the same id each rebuild."""
    salt = 0
    while True:
        ident = hashlib.sha256(f"{fm.plain(text)}#{salt}".encode()).hexdigest()[:4]
        if ident not in taken:
            return ident
        salt += 1


def _ids_in_history(config: Config, name: str) -> set[str]:
    """Every id the role's notes have ever carried: a new line never reuses one, so an old strike can't attach to it."""
    rel = folder(config, name).relative_to(config.repo).as_posix()
    return set(re.findall(r" \^([0-9a-f]{4})\b", _git(config, "log", "--all", "--full-history", "-m", "-p", "--format=", "--", rel)))


def assign_ids(config: Config, name: str) -> list[str]:
    """Give each line without an id one, in place, in notes with nothing uncommitted: the commit that follows then holds
    ids alone, and blame can pass over it. Ids are how a line is followed through history, so they live in the notes."""
    changed = []
    paths = sorted(p for p in folder(config, name).glob("*.md") if p.name != ROLE_NOTE)
    taken = _ids_in_history(config, name)
    for path in paths:
        taken.update(re.findall(r" \^([0-9a-f]{4})[ \t]*$", path.read_text(encoding="utf-8"), re.M))
    for path in paths:
        if _git(config, "status", "--porcelain", "--", str(path)).strip():
            continue   # a pending edit, perhaps a person's: tagged once it is committed
        data, body = fm.read(path)
        out, section, dirty = [], "", False
        for raw in body.splitlines(keepends=True):
            line = raw.rstrip("\r\n")
            if line.startswith("## "):
                section = line[3:].strip()
            match = LESSON.match(line)
            if section and match and not match["id"]:
                ident = line_id(match["text"], taken)
                taken.add(ident)
                raw = line.rstrip() + f" ^{ident}" + raw[len(line):]
                dirty = True
            out.append(raw)
        if dirty:
            fm.write(path, data, "".join(out))
            changed.append(path.relative_to(config.vault_path).as_posix())
    return changed


def agent_email(config: Config) -> str:
    """Whose commits are the agent's own. Every other author is a person, and a person's edit outranks a learned line."""
    if config.agent_email:
        return config.agent_email
    try:
        return subprocess.check_output(["git", "-C", str(config.repo), "config", "user.email"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _git(config: Config, *args: str) -> str:
    try:
        return subprocess.check_output(["git", "-C", str(config.repo), *args], text=True, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return ""


IDS_COMMIT = "roles: assign line ids"


def _blame(config: Config, path: Path) -> dict[int, tuple[str, str, str]]:
    """Each line's last author: line number to (name, email, date). Commits that only gave lines their ids are passed
    over, so a person's line stays theirs after the agent tags it."""
    ignore = [f"--ignore-rev={sha}" for sha in _git(config, "log", "--format=%H", "--fixed-strings",
                                                    f"--grep={IDS_COMMIT}", "--", str(path)).split()]
    out, current = {}, {}
    for raw in _git(config, "blame", "--line-porcelain", *ignore, "--", str(path)).splitlines():
        if re.match(r"^[0-9a-f]{40} \d+ \d+", raw):
            current = {"line": int(raw.split()[2])}
        elif raw.startswith("author "):
            current["name"] = raw[7:]
        elif raw.startswith("author-mail "):
            current["email"] = raw[12:].strip("<>")
        elif raw.startswith("author-time "):
            current["at"] = date.fromtimestamp(int(raw[12:])).isoformat()
        elif raw.startswith("\t") and "line" in current:
            out[current["line"]] = (current.get("name", ""), current.get("email", ""), current.get("at", ""))
    return out


def _snapshots(config: Config, name: str) -> list[tuple[dict, dict[str, str]]]:
    """The role's shape notes at each commit on the main line that changed them, oldest first. First parent only, so a
    merge counts as one change by whoever made it, including a line deleted while resolving it."""
    rel = folder(config, name).relative_to(config.repo).as_posix()
    commits = []
    for line in _git(config, "log", "--first-parent", "--reverse", "--format=%H\x02%an\x02%ae\x02%as", "--", rel).splitlines():
        sha, author, email, at = line.split("\x02")
        tree = {}
        for entry in _git(config, "ls-tree", "-r", "-z", sha, "--", rel).split("\0"):
            meta, _, path = entry.partition("\t")
            if path.endswith(".md") and not path.endswith(f"/{ROLE_NOTE}"):
                tree[path] = meta.split()[2]
        commits.append(({"sha": sha, "by": author, "email": email, "at": at}, tree))
    wanted = sorted({oid for _, tree in commits for oid in tree.values()})
    blobs: dict[str, str] = {}
    if wanted:
        out = subprocess.run(["git", "-C", str(config.repo), "cat-file", "--batch"], input=("\n".join(wanted) + "\n").encode(),
                             capture_output=True, check=True).stdout
        at = 0
        for oid in wanted:
            header_end = out.index(b"\n", at)
            size = int(out[at:header_end].split()[2])
            blobs[oid] = out[header_end + 1:header_end + 1 + size].decode("utf-8", "replace")
            at = header_end + 1 + size + 1
    return [(commit, {path: blobs[oid] for path, oid in tree.items()}) for commit, tree in commits]


def _key(lesson: Lesson) -> str:
    return lesson.id or "t:" + fm.plain(lesson.text)


def _parsed(notes: dict[str, str]) -> dict[str, tuple[bool, Lesson]]:
    found = {}
    for note, text in sorted(notes.items()):
        try:
            live, struck, _ = lessons(fm.split(text)[1], note)
        except fm.Malformed:
            continue
        found.update({_key(lesson): (False, lesson) for lesson in live})
        found.update({_key(lesson): (True, lesson) for lesson in struck})
    return found


def _strikes(config: Config, name: str, agent: str, now: dict[str, str]) -> dict[str, Struck]:
    """Replay the notes' history: a line is struck by the commit that moved it under Struck, or by a person's commit
    that removed it, and stays struck whoever later removes it, until a person puts it back among the live lines. A
    line the agent's commit removed was retired by consolidation, not struck. The working tree is the last step: a line
    moved under Struck there counts at once, a deletion only once committed."""
    struck: dict[str, Struck] = {}
    prev: dict[str, tuple[bool, Lesson]] = {}
    steps = _snapshots(config, name)
    steps.append(({"sha": "", "by": "", "email": None, "at": date.today().isoformat()}, now))
    for commit, notes in steps:
        cur = _parsed(notes)
        by_id = {lesson.id: key for key, (_, lesson) in cur.items() if lesson.id}
        by_text = {fm.plain(lesson.text): key for key, (_, lesson) in cur.items()}
        person = commit["email"] is not None and commit["email"] != agent
        for key, (was_struck, lesson) in prev.items():
            if lesson.id:   # an established id is followed by id alone; its words may repeat under another id
                moved_to = by_id.get(lesson.id)
            else:   # a line without an id yet: followed by its words, perhaps now with an id
                moved_to = by_text.get(fm.plain(lesson.text))
            if moved_to is None:
                if not was_struck and person:   # a later strike is dated by its own commit
                    struck[key] = Struck(lesson.id, lesson.text, commit["by"], commit["at"], "deleted", commit["sha"][:12])
            elif moved_to != key and key in struck:   # the same line, now with an id
                struck[moved_to] = struck.pop(key)
                struck[moved_to].id = cur[moved_to][1].id
        for key, (is_struck, lesson) in cur.items():
            if is_struck and (key not in struck or not prev.get(key, (True,))[0]):
                struck[key] = Struck(lesson.id, lesson.text, commit["by"], commit["at"], "moved", commit["sha"][:12])
            elif not is_struck and person and (key not in prev or prev[key][0]):
                # A person put it back among the live lines, perhaps with an id it lacked when struck.
                struck.pop(key, None)
                struck.pop("t:" + fm.plain(lesson.text), None)
        prev = cur
    return struck


def _working_notes(config: Config, name: str) -> dict[str, str]:
    root = folder(config, name)
    return {p.relative_to(config.repo).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(root.glob("*.md")) if p.name != ROLE_NOTE}


def _fingerprint(config: Config, name: str) -> str:
    """What the index is built from: the commit and the notes as they are on disk, committed or not."""
    digest = hashlib.sha256(_git(config, "rev-parse", "HEAD").strip().encode())
    role = folder(config, name) / ROLE_NOTE
    notes = _working_notes(config, name) | {ROLE_NOTE: role.read_text(encoding="utf-8") if role.is_file() else ""}
    for path, text in sorted(notes.items()):
        digest.update(f"\0{path}\0{text}".encode())
    return digest.hexdigest()[:16]


def rebuild(config: Config, name: str) -> Index:
    """The role's index, from its notes and their history alone. Deleting it loses nothing: rebuilding gives the same.
    A pull landing mid-build would mix two states under one fingerprint, so the build is redone until none did."""
    for _ in range(5):
        before = _fingerprint(config, name)
        index = _build(config, name)
        if index.head == before == _fingerprint(config, name):
            break
    path = index_path(config, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    fm.write_text(path, json.dumps(asdict(index), indent=2, sort_keys=True) + "\n")
    return index


def _build(config: Config, name: str) -> Index:
    role = load(config, name)
    agent = agent_email(config)
    notes = _working_notes(config, name)
    index = Index(name, _fingerprint(config, name))
    for shape in role.shapes():
        path = folder(config, name) / f"{shape}.md"
        if not path.is_file():
            continue
        note = path.relative_to(config.vault_path).as_posix()
        text = path.read_text(encoding="utf-8")
        live, _, problems = lessons(fm.split(text)[1], note)
        index.problems.extend(problems)
        blame = _blame(config, path)
        numbered = {line.rstrip(): number for number, line in enumerate(text.splitlines(), 1)}
        for lesson in live:
            line = f"- {lesson.text}" + (f" ^{lesson.id}" if lesson.id else "")
            author = blame.get(numbered.get(line, 0))
            if author and author[1] != "not.committed.yet":   # an edit not yet committed has no author to rank
                lesson.by, lesson.at = author[0], author[2]
                lesson.human = bool(agent) and author[1] != agent
        index.lessons.extend(live)
    struck = _strikes(config, name, agent, notes)
    index.struck = sorted(struck.values(), key=lambda entry: (entry.at, entry.id, entry.text))
    index.problems.extend(f"{lesson.note}: ^{lesson.id or '?'} was struck by {struck[_key(lesson)].by}; "
                          "the agent wrote it again without a person bringing it back"
                          for lesson in index.lessons if _key(lesson) in struck)
    return index


def scratch(config: Config) -> Path:
    """The vault's `.brain`: engine state that is rebuilt, never committed and never read as a note. It ignores itself,
    so a person's `git add -A` can't commit it either."""
    path = config.vault_path / ".brain"
    path.mkdir(exist_ok=True)
    if not (path / ".gitignore").exists():
        (path / ".gitignore").write_text("*\n", encoding="utf-8")
    return path


def index_path(config: Config, name: str) -> Path:
    return scratch(config) / "roles" / f"{name}.json"


def read_index(config: Config, name: str) -> Index:
    """The index, rebuilt first when it is missing or the notes changed since, on disk or in history."""
    path = index_path(config, name)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return rebuild(config, name)
    if raw.get("head") != _fingerprint(config, name):
        return rebuild(config, name)
    return Index(raw["role"], raw["head"], [Lesson(**item) for item in raw["lessons"]],
                 [Struck(**item) for item in raw["struck"]], raw["problems"])


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", STATUS.sub("", SOURCES.sub("", text)).casefold()))


def refusal(index: Index, text: str, evidence: str) -> str | None:
    """Why a claim may not be learned: a person struck it (or one close to it), and no evidence since says otherwise."""
    words = _words(text)
    for entry in index.struck:
        theirs = _words(entry.text)
        if theirs and len(words & theirs) / len(words | theirs) >= 0.8 and evidence <= entry.at:
            return (f"{entry.by} struck this on {entry.at} (^{entry.id}: {fm.cut(entry.text, 16)}); "
                    "learn it again only from evidence dated after that")
    return None


def base_path(config: Config, name: str) -> Path:
    """The shipped text this role's template was last taken from, so an upgrade can be offered as a change to it."""
    return config.vault_path / config.runtime_dir / "roles" / f"{name}.base.md"


def proposal_path(config: Config, name: str) -> Path:
    return config.vault_path / config.runtime_dir / "roles" / f"{name}.proposed.md"


def add(config: Config, name: str, template: str) -> list[Path]:
    """Copy a shipped template into the vault as the role's own, never over one that is there."""
    if not NAME.fullmatch(name) or name.strip() != name:
        raise ValueError("a role name uses letters, digits, spaces or hyphens and starts with a letter")
    text = shipped_text(template)
    target = folder(config, name) / ROLE_NOTE
    if target.exists():
        raise ValueError(f"role {name!r} already exists; edit {target.relative_to(config.vault_path)} instead")
    role, problems = parse(text, name, _known_sources())
    if role is None:
        raise ValueError("; ".join(problems))
    written = []
    for path, content in ((target, text), (base_path(config, name), text)):
        path.parent.mkdir(parents=True, exist_ok=True)
        fm.write_text(path, content)
        written.append(path)
    for entry in role.notes:
        path = folder(config, name) / f"{entry['shape']}.md"
        if not path.exists():
            fm.write(path, {"tier": "warm"}, f"# {entry['shape']}\n\n{entry.get('holds', '')}\n")
            written.append(path)
    return written


def template_check(config: Config) -> list[tuple[str, Path]]:
    """For each role whose shipped template changed since it was copied, write the change as a proposal beside it.
    The role's own copy is never touched: a person accepts or declines."""
    found = []
    for name in names(config):
        base = base_path(config, name)
        current = (folder(config, name) / ROLE_NOTE).read_text(encoding="utf-8")
        try:
            template = fm.split(current)[0].get("template") or ""
            new = shipped_text(str(template))
        except (ValueError, fm.Malformed):
            continue
        old = base.read_text(encoding="utf-8") if base.is_file() else ""
        if new == old:
            continue
        if current == new:   # the person already made the same change
            fm.write_text(base, new)
            continue
        diff = "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"{template} (as copied)",
                                            f"{template} (shipped now)"))
        path = proposal_path(config, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        fm.write_text(path, f"# The {template} template changed\n\nAccept with `brain roles template accept {name}` "
                      f"(merged into your edits) or decline with `brain roles template decline {name}`.\n\n"
                      f"```diff\n{diff}```\n")
        found.append((name, path))
    return found


def template_accept(config: Config, name: str) -> Path:
    """Merge the shipped change into the role's own copy, keeping every edit a person made. A clash is refused, never
    resolved over their text."""
    target = folder(config, name) / ROLE_NOTE
    template = str(fm.split(target.read_text(encoding="utf-8"))[0].get("template") or "")
    new, base = shipped_text(template), base_path(config, name)
    old = base.read_text(encoding="utf-8") if base.is_file() else ""
    staged = scratch(config) / "roles"
    staged.mkdir(parents=True, exist_ok=True)
    (staged / "old").write_text(old, encoding="utf-8")
    (staged / "new").write_text(new, encoding="utf-8")
    merged = subprocess.run(["git", "merge-file", "-p", str(target), str(staged / "old"), str(staged / "new")],
                            capture_output=True, text=True)
    if merged.returncode != 0:
        raise ValueError(f"the shipped change clashes with edits in {target.relative_to(config.vault_path)}; "
                         f"make it by hand from {proposal_path(config, name).relative_to(config.vault_path)}, then "
                         f"`brain roles template decline {name}`")
    role, problems = parse(merged.stdout, name, _known_sources())
    if role is None:
        raise ValueError("the merged template would not be valid: " + "; ".join(problems))
    fm.write_text(target, merged.stdout)
    fm.write_text(base, new)
    proposal_path(config, name).unlink(missing_ok=True)
    return target


def template_decline(config: Config, name: str) -> None:
    """Keep the role's copy as it is and stop proposing this change."""
    target = folder(config, name) / ROLE_NOTE
    template = str(fm.split(target.read_text(encoding="utf-8"))[0].get("template") or "")
    fm.write_text(base_path(config, name), shipped_text(template))
    proposal_path(config, name).unlink(missing_ok=True)


def problems(config: Config) -> list[str]:
    known, out = _known_sources(), []
    for name in names(config):
        role, found = parse((folder(config, name) / ROLE_NOTE).read_text(encoding="utf-8"), name, known)
        out.extend(found)
        if role is None:
            continue
        for shape in role.shapes():
            path = folder(config, name) / f"{shape}.md"
            if path.is_file():
                try:
                    out.extend(lessons(fm.read(path)[1], path.relative_to(config.vault_path).as_posix())[2])
                except fm.Malformed:
                    pass   # validate's malformed check names it
        if proposal_path(config, name).is_file():
            out.append(f"{FOLDER}/{name}: the shipped template changed; read "
                       f"{proposal_path(config, name).relative_to(config.vault_path)} and accept or decline it")
    return out
