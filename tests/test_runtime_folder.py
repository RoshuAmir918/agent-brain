"""Runtime folder migration against throwaway Git vaults."""
import os
import tomllib

import pytest

from brain import install, nightly, runtime_folder
from brain.config import Config
from conftest import git, snapshot


@pytest.fixture(params=[("AgentThinkTree", "Hermes"), ("memory", "hermes")])
def layout(repo, request):
    vault, _ = repo
    repo = vault.parent
    name, old = request.param
    if vault.name != name:
        vault.rename(repo / name)
    vault = repo / name
    (repo / "brain.toml").write_text(f'[brain]\nvault = "{name}"\nhermes_dir = "{old}" # old layout\nagent_name = "Mira"\n\n[context]\nprefetch = false\n')
    folder = vault / old
    folder.mkdir()
    (folder / "README.md").write_text(f'# Runtime\n[[{old}/skills/x/SKILL]]\n')
    (folder / "schedule.toml").write_text('[routines.watch]\nevery = "1h"\n')
    (repo / ".hermes.md").write_text(f'[[{name}/{old}/README]]\n')
    refs = (f'[[{name}/{old}/README|runtime]] [[{old}/README]]\n'
            f'({old}/README.md) `{old}/skills/` "{old}/" \'{old}/\'\n'
            f'{old}/README.md {name}/{old}/README.md\n'
            f'[[{old}|runtime]] [[{old}]]\n'
            f'Hermes is a name in prose. hermes is also prose.\n'
            f'Other{old}/README.md other/{old}/README.md\n'
            '.hermes.md ../.hermes.md `.hermes.md`\n')
    for suffix in (".md", ".toml", ".json", ".sh", ".py"):
        (vault / ("paths" + suffix)).write_text(refs)
    (vault / "ignored.txt").write_text(refs)
    (vault / "binary.json").write_bytes(b'\xffHermes/README')
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "old runtime fixture")
    (vault / "untracked.md").write_text(f'{old}/README.md')
    return Config.load(vault)


def test_dry_run_changes_neither_worktree_nor_index(layout, capsys):
    before = snapshot(layout.repo)
    index = git(layout.repo, "diff", "--cached")
    nightly.main(["runtime-folder", "--move", "--dry-run", "--vault", str(layout.vault_path)])
    output = capsys.readouterr().out
    assert "dry run: no files changed" in output
    assert f"move {layout.vault}/{layout.runtime_dir} -> {layout.vault}/Runtime" in output
    assert "reference(s)" in output and "paths.md:" in output
    assert snapshot(layout.repo) == before
    assert git(layout.repo, "diff", "--cached") == index


def test_real_move_paths_symlink_config_context_and_idempotence(layout):
    old = layout.runtime_dir
    root = layout.vault_path
    runtime_folder.move(layout)
    assert (root / "Runtime/README.md").read_text() == '# Runtime\n[[Runtime/skills/x/SKILL]]\n'
    assert (root / old).is_symlink() and os.readlink(root / old) == "Runtime"
    assert (root / old / "README.md").read_bytes() == (root / "Runtime/README.md").read_bytes()
    assert git(layout.repo, "ls-files", "--stage", "--", str(root / old)).startswith("120000")
    values = tomllib.loads((layout.repo / "brain.toml").read_text())
    assert values["brain"] == {"vault": layout.vault, "runtime_dir": "Runtime", "agent_name": "Mira"}
    assert values["context"] == {"prefetch": False}
    text = (root / "paths.md").read_text()
    assert f'[[{layout.vault}/Runtime/README|runtime]] [[Runtime/README]]' in text
    assert '(Runtime/README.md) `Runtime/skills/` "Runtime/" \'Runtime/\'' in text
    assert '[[Runtime|runtime]] [[Runtime]]' in text
    assert 'Hermes is a name in prose. hermes is also prose.' in text
    assert f'Other{old}/README.md other/{old}/README.md' in text
    assert '.context.md ../.context.md `.context.md`' in text
    for suffix in (".toml", ".json", ".sh", ".py"):
        assert (root / ("paths" + suffix)).read_text() == text
    assert old + "/README.md" in (root / "ignored.txt").read_text()
    assert (root / "untracked.md").read_text() == f'{old}/README.md'
    assert (root / "binary.json").read_bytes() == b'\xffHermes/README'
    assert not (layout.repo / ".hermes.md").exists()
    assert (layout.repo / ".context.md").read_text() == f'[[{layout.vault}/Runtime/README]]\n'
    before = snapshot(layout.repo)
    assert runtime_folder.move(Config.load(root)) == ["0 file(s) rewritten; 0 reference(s)"]
    assert snapshot(layout.repo) == before


def test_case_only_move_uses_temporary_name_and_skips_alias_when_needed(repo, monkeypatch):
    vault, _ = repo
    (vault.parent / "brain.toml").write_text('[brain]\nruntime_dir = "runtime"\n')
    old = vault / "runtime"
    old.mkdir()
    (old / "README.md").write_text('# runtime\n')
    git(vault.parent, "add", "brain.toml", "memory/runtime")
    git(vault.parent, "commit", "-m", "case fixture")
    insensitive = (vault / "Runtime").exists()
    calls = []
    real = runtime_folder._git
    def trace(config, *args):
        calls.append(args)
        return real(config, *args)
    monkeypatch.setattr(runtime_folder, "_git", trace)
    output = runtime_folder.move(Config.load(vault))
    moves = [call for call in calls if call[0] == "mv"]
    assert len(moves) == 2
    assert moves[0][-1] == str(vault / ".brain-runtime-move")
    assert moves[1][-2] == str(vault / ".brain-runtime-move")
    assert (vault / "Runtime/README.md").exists()
    assert not (vault / ".brain-runtime-move").exists()
    if insensitive:
        assert not old.is_symlink()
        assert any("skip compatibility symlink" in line for line in output)
    else:
        assert old.is_symlink() and os.readlink(old) == "Runtime"
    assert runtime_folder.move(Config.load(vault)) == ["0 file(s) rewritten; 0 reference(s)"]


def test_existing_destination_is_never_overwritten(layout):
    (layout.vault_path / "Runtime").mkdir()
    before = snapshot(layout.repo)
    with pytest.raises(ValueError, match="already exists"):
        runtime_folder.move(layout)
    assert snapshot(layout.repo) == before


@pytest.mark.parametrize("text", ['[brain]', '# top\n[context]\nprefetch = false\n',
    '[brain]\n"hermes_dir" = "old"\nruntime_dir = "other"\n\n[context]\nprefetch = false\n'])
def test_config_edit_preserves_other_tables_and_handles_missing_layout(text):
    result = tomllib.loads(runtime_folder.config_text(text))
    assert result["brain"] == {"runtime_dir": "Runtime"}
    if "context" in tomllib.loads(text):
        assert result["context"] == tomllib.loads(text)["context"]


@pytest.mark.parametrize("keys,expected", [('', 'Runtime'), ('hermes_dir = "Hermes"', 'Hermes'),
    ('runtime_dir = "Runs"', 'Runs'), ('hermes_dir = "Hermes"\nruntime_dir = "Runs"', 'Runs')])
def test_runtime_config_and_legacy_placeholder(tmp_path, keys, expected):
    (tmp_path / "brain.toml").write_text('[brain]\n' + keys)
    config = Config.load(cwd=tmp_path)
    assert config.runtime_dir == expected
    assert not hasattr(config, "hermes_dir")
    assert config.template_values()["hermes_dir"] == config.template_values()["runtime_dir"] == expected
    config.skills_path.mkdir(parents=True)
    target = config.skills_path / "brain-watch/SKILL.md"
    target.parent.mkdir()
    target.write_text('old\n\n## Local\nRead {hermes_dir}/README.md and {runtime_dir}/schedule.toml.\n')
    spec = config.vault_path / config.agent("Memory Spec.md")
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text('# Spec\n\n## Local\nRead {hermes_dir}/README.md.\n')
    install.skills(config)
    assert f'Read {expected}/README.md.' in spec.read_text()
    assert f'Read {expected}/README.md and {expected}/schedule.toml.' in target.read_text()


def test_absolute_paths_into_the_old_folder_are_rewritten_but_other_folders_and_prose_are_not():
    text = ("node /Users/r/tars/memory/hermes/skills/x/scripts/a.mjs\n"
            "- `skills.external_dirs: [$VAULT_REPO/memory/hermes/skills]`\n"
            "/Users/r/.hermes/profiles/tars/memory/hermes-old/notes.md\n"
            "Hermes ran the agents until November.\n")
    updated, count = runtime_folder.rewrite(text, "hermes", "Runtime", "memory")
    assert "/Users/r/tars/memory/Runtime/skills/x/scripts/a.mjs" in updated
    assert "$VAULT_REPO/memory/Runtime/skills" in updated
    assert "memory/hermes-old/notes.md" in updated and "Hermes ran the agents" in updated
    assert count == 2


def test_the_move_stages_only_what_it_changed(tmp_path):
    import subprocess
    from brain.config import Config
    repo = tmp_path / "repo"
    (repo / "memory/hermes").mkdir(parents=True)
    (repo / "brain.toml").write_text('[brain]\nvault = "memory"\nhermes_dir = "hermes"\n', encoding="utf-8")
    (repo / "memory/hermes/README.md").write_text("See [[hermes/skills]].\n", encoding="utf-8")
    (repo / "memory/Note.md").write_text("Points at memory/hermes/README.md.\n", encoding="utf-8")
    (repo / "memory/Other.md").write_text("Unrelated.\n", encoding="utf-8")
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True).stdout
    git("init", "-q"); git("-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "seed")
    (repo / "memory/Other.md").write_text("Agent's uncommitted work.\n", encoding="utf-8")
    runtime_folder.move(Config.load(repo / "memory"))
    staged = set(git("diff", "--cached", "--name-only").split())
    assert {"brain.toml", "memory/Note.md", "memory/Runtime/README.md"} <= staged
    assert "memory/Other.md" not in staged and "memory/Other.md" in git("diff", "--name-only")
