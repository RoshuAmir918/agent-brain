from datetime import date
import json
from unittest.mock import patch

import pytest

from brain import attribute, compile_context, digest, fm, nightly, retrieve
from brain.config import Config
from brain.tasks import Task, bounded_hot_body, read_archive_body, seed_uses_from_body
from brain.vault import Vault
from conftest import snapshot


@pytest.fixture
def alternate(tmp_path):
    (tmp_path / "brain.toml").write_text('''[brain]
vault = "AgentThinkTree"
agent_dir = "Orphan"
people_dir = "People"
agents_dir = "Agents"
human_profile = "Roshan/Profile.md"
sources_dir = "Roshan"
hermes_dir = "Hermes"
skill_prefix = "orphan-"
human_name = "Owner Example"
agent_name = "Guide"
''')
    config = Config.load(cwd=tmp_path)
    root = config.vault_path
    tasks = [Task(f"T-{number:03d}", title, "- **Status:** open\n- **Next action:** preserve full body\n",
                  {"uses": day, "status": "open"}).render()
             for number, title, day in ((1, "Hot", "2026-10-19"), (2, "Warm", "2026-09-30"),
                                        (3, "Cold", "2026-09-01"))]
    notes = {
        "START.md": "# Start\n", "CURRENT.md": "# Current\n" + nightly.MARK_START + "\n" + nightly.MARK_END,
        config.agent("Tasks.md"): "# Tasks\n## Active work\n" + "\n".join(tasks),
        config.agent("Triggers.md"): "# Triggers\n| TR-001 | commitment | 2026-10-20 | revisit hot | T-001 | active | 2026-10-19 |\n"
        f"| TR-002 | intention | quiet work returns | revisit | [[{config.link('Quiet Hub')}]] | active | 2026-09-01 |\n",
        config.agent("SOUL.md"): "# Guide\n", config.agent("Memory Spec.md"): "# Spec\n",
        config.agent("Reflections/Review.md"): "# Review\n",
        config.agent("Primed/2026-10-19.md"): "# Expired\n",
        config.human_profile: "# Owner\n", config.people_dir + "/Alice_Example/Profile.md": "# Alice\n",
        config.agents_dir + "/Helper/Profile.md": "# Helper\n", config.hermes_dir + "/README.md": "# Hermes\n",
        "Hot Hub.md": "# Hot hub\nCurrent project.\n", "Warm Hub.md": "# Warm hub\nPrior project.\n",
        "Quiet Hub.md": "# Quiet hub\nDormant project.\n",
        "Source Hub.md": f"# Source hub\n[[{config.sources_dir}/Evidence]]\n[[Archive/Receipt]]\n",
    }
    for relative, body in notes.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        day = "2026-09-01" if relative == "Quiet Hub.md" else "2026-09-30"
        if relative == "Hot Hub.md":
            day = "2026-10-19"
        fm.write(path, {"uses": [day]}, body + "\n")
    dream = root / config.agent("Dreams/2026-10-19.md")
    dream.parent.mkdir()
    dream.write_bytes(b"---\ninvalid: [\n---\nFICTION\r\n")
    for relative in (config.sources_dir + "/Evidence.md", "Archive/Receipt.md"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("READ ONLY HUMAN EVIDENCE")
    sessions = root / "Conversations/Sessions"
    sessions.mkdir(parents=True)
    record = {"id": "fixture", "messages": [
        {"role": "user", "timestamp": "2026-10-20T12:00:00-07:00", "content": "Question"},
        {"role": "assistant", "tool_calls": [{"name": "read_file", "arguments":
         {"path": config.link("Warm Hub.md")}}]},
        {"role": "assistant", "content": "Answer"}]}
    (sessions / "all.jsonl").write_text(json.dumps(record) + "\n")
    return config


def test_alternate_layout_whole_night_and_preview(alternate, monkeypatch, capsys):
    config = alternate
    root = config.vault_path
    monkeypatch.chdir(root / config.agent_dir)
    before = snapshot(config.repo)
    nightly.main(["nightly", "--date", "2026-10-20"])
    assert snapshot(config.repo) == before
    sessions = root / "Conversations/Sessions"
    nightly.main(["nightly", "--date", "2026-10-20", "--sessions-dir", str(sessions), "--write"])
    hot = (root / "Hot Set.md").read_text()
    warm = (root / "Warm Index.md").read_text()
    assert f"[[{config.link('Hot Hub')}|" in hot
    assert f"[[{config.link('Warm Hub')}|" in hot
    assert f"[[{config.link(config.agent('Tasks Archive/T-003'))}|" in warm
    register = (root / config.agent("Tasks.md")).read_text()
    assert f"[[{config.link(config.agent('Tasks Archive/T-002'))}|" in register
    assert f"[[{config.link(config.agent('Tasks'))}#T-001" in (root / "CURRENT.md").read_text()
    assert "preserve full body" in read_archive_body(config, "T-003")
    trigger_text = (root / config.agent("Triggers.md")).read_text()
    assert "fired 2026-10-20" in trigger_text and "mentioned 2026-10-20" in trigger_text
    context = (config.repo / ".hermes.md").read_text()
    assert f"## {config.link(config.agent('Triggers.md'))}" in context
    assert "- People with profiles: Alice Example" in context
    assert "- Agents registered: Helper" in context
    assert context == compile_context.build(Vault(root))
    assert not (root / "agent").exists() and "[[memory/" not in hot + warm + register + context
    assert (root / config.agent("Sleep Log/2026-10.md")).exists()
    assert (root / config.agent("Dreams/2026-10-19.md")).read_bytes() == b"---\ninvalid: [\n---\nFICTION\r\n"
    assert fm.read(root / config.agent("Primed/2026-10-19.md"))[0]["tier"] == "cold"
    assert (config.repo / config.sources_dir / "Evidence.md").read_text() == "READ ONLY HUMAN EVIDENCE"
    vault = Vault(root)
    assert vault.kind_for(config.agent("SOUL.md")) == "orientation"
    assert vault.kind_for(config.people_dir + "/Alice_Example/Profile.md") == "profile"
    assert vault.kind_for(config.agents_dir + "/Helper/Profile.md") == "profile"
    assert vault.kind_for(config.agent("Reflections/Review.md")) == "reflection"
    assert vault.resolve(config.sources_dir + "/Evidence") == "../" + config.sources_dir + "/Evidence.md"
    assert vault.resolve("Evidence") == "../" + config.sources_dir + "/Evidence.md"
    assert vault.resolve("Archive/Receipt") == "../Archive/Receipt.md"
    assert all(candidate.path.startswith(config.vault + "/") for candidate in retrieve.search(vault, "project T-001 TR-001"))
    with pytest.raises(SystemExit) as validated:
        nightly.main(["validate"])
    assert validated.value.code == 0
    capsys.readouterr()
    nightly.main(["paths", "soul"])
    assert capsys.readouterr().out == str(config.soul_path) + "\n"
    text, _ = digest.build(config, date(2026, 10, 20))
    assert "**Owner Example**" in text and "**Guide**" in text
    assert f"reads: {config.link('Warm Hub.md')}" in text
    nightly.main(["install-skills"])
    for path in config.skills_path.glob("*/SKILL.md"):
        text = path.read_text()
        assert fm.split(text)[0]["name"] == path.parent.name
        assert path.parent.name.startswith(config.skill_prefix)
        assert all("{" + token + "}" not in text for token in config.template_values())


def test_alternate_plain_paths_and_task_evidence(alternate):
    config = alternate
    vault = Vault(config.vault_path)
    relatives = [config.human_profile, config.people_dir + "/Alice_Example/Profile.md",
                 config.agents_dir + "/Helper/Profile.md", config.agent("Reflections/Review.md")]
    for prefix in ("", config.vault + "/"):
        uses = attribute.explicit_uses(vault, "\n".join(prefix + path for path in relatives), "2026-10-20")
        assert [use.target for use in uses] == relatives
    task = Task("T-099", "Large", "- **Status:** open\n"
                f"- **Latest evidence:** [[{config.link('Conversations/2026-10-19')}]]\n"
                "- **Evidence:** " + "historical " * 200 + "\n")
    seed_uses_from_body(task, date(2026, 10, 20), config)
    assert task.uses == ["2026-10-19"]
    bounded = bounded_hot_body(task, config)
    assert f"[[{config.link(config.agent('Tasks Archive/T-099'))}|" in bounded
    assert "[[memory/" not in bounded


def test_nightly_run_loads_config_only_once(alternate):
    root = alternate.vault_path
    with patch.object(Config, "load", wraps=Config.load) as load:
        nightly.run(root, date(2026, 10, 20), root / "Conversations/Sessions")
    load.assert_called_once_with(root.resolve())


def test_context_and_digest_reuse_loaded_config(alternate):
    fm.write(alternate.vault_path / "Hot Set.md", {}, "## Automatic load\n")
    vault = Vault(alternate.vault_path)
    with patch.object(Config, "load", side_effect=AssertionError("config must be passed explicitly")):
        context = compile_context.build(vault)
        text, _ = digest.build(vault.config, date(2026, 10, 20))
        path = digest.write(vault.config, date(2026, 10, 20), text)
    assert f"## {alternate.link(alternate.agent('Tasks.md'))}" in context
    assert "**Owner Example**" in text and "**Guide**" in text
    assert path == alternate.vault_path / "Conversations/Digests/2026-10-20.md"
