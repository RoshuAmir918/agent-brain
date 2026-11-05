"""Move a vault's runtime folder and tracked path references, without a model."""
from __future__ import annotations

import os
import re
import subprocess
import tomllib
from pathlib import Path

from .config import Config

TEXT_SUFFIXES = {".md", ".toml", ".json", ".sh", ".py"}


def config_text(text: str) -> str:
    """Edit only the layout keys in [brain], retaining other tables and comments."""
    # Validate before editing, as schedule.save does for its TOML.
    tomllib.loads(text)
    lines = (text if not text or text.endswith("\n") else text + "\n").splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if re.match(r"^\s*\[brain\]\s*(?:#.*)?$", line)), None)
    if start is None:
        return text.rstrip("\n") + ('\n\n' if text else '') + '[brain]\nruntime_dir = "Runtime"\n'
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^\s*\[", lines[i])), len(lines))
    body, inserted = [], False
    for line in lines[start + 1:end]:
        if re.match(r'''^\s*(?:runtime_dir|hermes_dir|"runtime_dir"|"hermes_dir"|'runtime_dir'|'hermes_dir')\s*=''', line):
            if not inserted:
                body.append('runtime_dir = "Runtime"\n')
                inserted = True
        else:
            body.append(line)
    if not inserted:
        body.insert(0, 'runtime_dir = "Runtime"\n')
    return "".join(lines[:start + 1] + body + lines[end:])


def rewrite(text: str, old: str, new: str, vault: str) -> tuple[str, int]:
    # Match a path prefix, never a name in prose or a substring of a different folder.
    prefixes = {f"{vault}/{old}": f"{vault}/{new}", old: new}
    pattern = re.compile(r'''(^|\[\[|[(`"'\s])(''' + "|".join(re.escape(key) for key in sorted(prefixes, key=len, reverse=True)) + r")(?=[/|\]])", re.M)
    text, count = pattern.subn(lambda match: match[1] + prefixes[match[2]], text)
    if vault and vault != ".":
        # An absolute path into the repo (`/Users/…/repo/memory/hermes/…`, `$VAULT_REPO/memory/hermes/…`) carries the
        # repo-relative form after a slash; scripts run by these paths would break when the old name's symlink goes.
        absolute = re.compile(r"(?<=/)" + re.escape(f"{vault}/{old}") + r"(?=[/|\]])")
        text, more = absolute.subn(f"{vault}/{new}", text)
        count += more
    return text, count


def _git(config: Config, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(config.repo), *args], text=True, stderr=subprocess.PIPE)


def move(config: Config, *, dry_run: bool = False) -> list[str]:
    old = config.vault_path / config.runtime_dir
    target = config.vault_path / "Runtime"
    old_context, context = config.repo / ".hermes.md", config.repo / ".context.md"
    moving = old != target and old.exists() and not old.is_symlink()
    same_file = moving and target.exists() and old.samefile(target)
    if moving and target.exists() and not same_file:
        raise ValueError(f"cannot move {old}: {target} already exists")
    if old_context.exists() and context.exists():
        raise ValueError(f"cannot move {old_context}: {context} already exists")
    temporary = old.with_name(".brain-runtime-move")
    case_only = old != target and old.name.casefold() == target.name.casefold() and old.parent == target.parent
    if moving and case_only and temporary.exists():
        raise ValueError(f"temporary move path already exists: {temporary}")
    tracked = _git(config, "ls-files", "-z").split("\0")
    edits, count = [], 0
    for relative in tracked:
        path = config.repo / relative
        if path.suffix not in TEXT_SUFFIXES or not path.is_file() or path.is_symlink():
            continue
        try:
            text = path.read_bytes().decode("utf-8")
        except UnicodeError:
            continue
        if "\0" in text:
            continue
        updated, references = rewrite(text, config.runtime_dir, "Runtime", config.vault) if config.runtime_dir != "Runtime" else (text, 0)
        # The compiled file is another path, not a word in prose.
        # File references end at quotes, whitespace or Markdown punctuation, rather than a folder separator.
        updated, file_refs = re.subn(r'''(?<![\w.-])\.hermes\.md(?=$|[\s`"'\])|#])''', '.context.md', updated)
        references += file_refs
        if references:
            edits.append((path, updated, references))
            count += references
    source = config.repo / "brain.toml"
    before = source.read_text(encoding="utf-8") if source.exists() else ""
    # Apply layout edits to the reference-rewritten config, if it too contains paths.
    after = config_text(next((text for path, text, _ in edits if path == source), before))
    report = ["dry run: no files changed"] if dry_run else []
    if moving:
        report.append(f"move {old.relative_to(config.repo)} -> {target.relative_to(config.repo)}" + (" (via temporary name)" if case_only else ""))
    if old_context.exists():
        report.append("move .hermes.md -> .context.md")
    for path, _, references in edits:
        report.append(f"rewrite {path.relative_to(config.repo)}: {references} reference(s)")
    if after != before:
        report.append('brain.toml: runtime_dir = "Runtime"; remove legacy key')
    symlink = old != target and not old.is_symlink() and (moving or target.is_dir())
    if symlink:
        report.append(f"skip compatibility symlink: names share a path on this case-insensitive filesystem" if same_file else
                      f"symlink {old.relative_to(config.repo)} -> {os.path.relpath(target, old.parent)}")
    report.append(f"{len(edits)} file(s) rewritten; {count} reference(s)")
    if dry_run:
        return report
    if moving:
        if case_only:
            _git(config, "mv", "--", str(old), str(temporary))
            _git(config, "mv", "--", str(temporary), str(target))
        else:
            _git(config, "mv", "--", str(old), str(target))
    if old_context.exists():
        _git(config, "mv", "--", str(old_context), str(context))
    written = []
    for path, text, _ in edits:
        if moving and path.is_relative_to(old):
            path = target / path.relative_to(old)
        elif path == old_context:
            path = context
        path.write_bytes(text.encode("utf-8"))
        written.append(path)
    if after != before:
        source.write_text(after, encoding="utf-8")
        written.append(source)
    if written:
        # Staged with the moves, so committing the index commits exactly this change and none of the agent's other work.
        _git(config, "add", "--", *(str(path) for path in written))
    if symlink and not same_file:
        old.parent.mkdir(parents=True, exist_ok=True)
        old.symlink_to(os.path.relpath(target, old.parent), target_is_directory=True)
        _git(config, "add", "--", str(old))
    return report
