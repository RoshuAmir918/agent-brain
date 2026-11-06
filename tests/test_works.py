"""The runtime's Library connects task work to context, handoffs and advisory validation."""
from datetime import date

import pytest

from brain import compile_context, fm, nightly, state, tasks, validate
from brain.config import Config, SECTIONS
from brain.vault import Vault
from conftest import snapshot

DAY = date(2026, 11, 5)
LISTING = "- Trip plan | trip-plan | document | active | editable | 2026-11-05T12:00:00Z\n"


@pytest.fixture
def library_home(tmp_path, monkeypatch):
    home = tmp_path / "agent-home"
    (home / "works").mkdir(parents=True)
    monkeypatch.setenv("AGENT_HOME", str(home))
    return home


def write_library(home, body=LISTING):
    (home / "works/LIBRARY.md").write_text("# Library\n\n" + body, encoding="utf-8")


def test_library_section_present_after_tasks_before_directory(vault, library_home):
    write_library(library_home)
    text = compile_context.build(Vault(vault))
    assert "## Library\n\n" + LISTING in text
    assert "\n# Library\n" not in text
    assert text.index("### T-001") < text.index("## Library") < text.index("## Directory")
    assert SECTIONS[SECTIONS.index("tasks") + 1] == "library"


@pytest.mark.parametrize("body", [None, "", " \n\t\n"])
def test_library_missing_or_empty_is_silently_omitted(vault, library_home, body):
    if body is not None:
        write_library(library_home, body)
    assert "## Library" not in compile_context.build(Vault(vault))


def test_library_without_agent_home_is_silently_omitted(vault, library_home, monkeypatch):
    write_library(library_home)
    monkeypatch.delenv("AGENT_HOME")
    assert "## Library" not in compile_context.build(Vault(vault))


def test_library_notices_load_even_without_works(vault, library_home):
    notice = "Library could not refresh: access refused.\n"
    write_library(library_home, notice)
    assert "## Library\n\n" + notice in compile_context.build(Vault(vault))


def test_library_caps_work_lines_and_keeps_every_notice(vault, library_home):
    lines = [f"- Work {n} | work-{n} | page | active | editable | 2026-11-05T12:00:00Z" for n in range(45)]
    lines.insert(1, "work-0: push refused.")
    lines.insert(43, "work-41: merge conflict.")
    lines.append("Library sync error: retry later.")
    write_library(library_home, "\n".join(lines))
    text = compile_context.build(Vault(vault), ("library",))
    assert text.count(" | page | ") == compile_context.LIBRARY_WORKS == 40
    assert "- Work 39 |" in text and "- Work 40 |" not in text and "- Work 44 |" not in text
    for notice in ("work-0: push refused.", "work-41: merge conflict.", "Library sync error: retry later."):
        assert notice in text
    assert "(cut at 40 works; the rest is in $AGENT_HOME/works/LIBRARY.md)" in text


def test_library_sections_cli_and_explicit_vault_allowlist(vault, library_home, capsys, monkeypatch):
    write_library(library_home)
    nightly.main(["compile-context", "--vault", str(vault), "--sections", "library"])
    text = capsys.readouterr().out
    assert "## Library" in text and "### T-001" not in text and "## Directory" not in text
    nightly.main(["compile-context", "--vault", str(vault), "--sections", "tasks"])
    text = capsys.readouterr().out
    assert "### T-001" in text and "## Library" not in text
    config_file = vault.parent / "brain.toml"
    config_file.write_text(config_file.read_text() + '\n[context]\nsections = ["start", "tasks"]\n')
    nightly.main(["compile-context", "--vault", str(vault), "--sections", "tasks,library"])
    assert "## Library" not in capsys.readouterr().out
    monkeypatch.setenv("BRAIN_CONTEXT_SECTIONS", "library")
    assert "## Library" in compile_context.build(Vault(vault))


@pytest.mark.parametrize("body,work", [
    ("- **Work:** trip-plan", "trip-plan"),
    (" \t-  **Work:**  plan-2 \t", "plan-2"),
    ("- **Hub:** [[Trip]]\n- **Work:** trip-plan\n- **Next:** book", "trip-plan"),
    ("- **Work:** Trip-plan", None), ("- **Work:** trip_plan", None),
    ("- **Work:** ../trip", None), ("- **Work:** trip plan", None),
    ("- **Work:** trip-plan extra", None), ("- **Work:**", None),
    ("- **Status:** mentions - **Work:** trip-plan", None), ("", None),
])
def test_task_work_parsing(body, work):
    parsed = tasks.parse_register(f"### T-001 — Trip\n{body}\n")[1][0]
    assert parsed.work == work


@pytest.mark.parametrize("archived", [False, True])
def test_handoff_work_sets_replaces_and_preserves_full_entry(vault, archived, monkeypatch, capsys):
    monkeypatch.setenv("BRAIN_TODAY", DAY.isoformat())
    config = Config.load(vault)
    tasks.set_fields(config, "T-001", DAY, values={"delivers": "a finished trip plan in the Library"})
    if archived:
        task = tasks.load_tasks(config)[0][0]
        tasks.write_archive(config, task, "warm", DAY, whole=True)
        fm.write(vault / "agent/Tasks.md", {"tier": "hot"}, "# Tasks\n" + task.render_line(config))
    for slug in ("trip-plan", "trip-budget"):
        nightly.main(["handoff", "T-001", "--work", slug, "--report", "Home", "--vault", str(vault)])
        capsys.readouterr()
        task = tasks.load_tasks(config)[0][0]
        assert task.work == slug and task.body.count("**Work:**") == 1
        assert "ORIGINAL_FULL_BODY" in task.body
        assert task.handoff == (DAY.isoformat(), "Home")
    nightly.main(["handoff", "T-001", "--vault", str(vault)])
    capsys.readouterr()
    assert tasks.load_tasks(config)[0][0].work == "trip-budget"
    nightly.main(["continue", "--due", "--vault", str(vault)])
    assert capsys.readouterr().out == (
        "T-001 · handed off 2026-11-05 · Sample · next: ORIGINAL_FULL_BODY · work: trip-budget\n")
    exported = state.build(config, DAY)["tasks"][0]
    assert exported["work"] == "trip-budget"


@pytest.mark.parametrize("slug", ["Trip", "bad_slug", "two words", "../work", "", "plan\n- **Next:** injected"])
def test_handoff_refuses_bad_slug_before_writing(vault, slug, capsys):
    before = snapshot(vault)
    with pytest.raises(SystemExit) as error:
        nightly.main(["handoff", "T-001", "--work", slug, "--vault", str(vault)])
    assert error.value.code == 1
    assert "work slug must match [a-z0-9-]+" in capsys.readouterr().err
    assert snapshot(vault) == before


def test_hub_work_is_exposed_in_state(vault):
    fm.write(vault / "Trip.md", {"kind": "hub", "tier": "warm", "work": "trip-plan"}, "# Trip\n")
    hub = next(h for h in state.build(Config.load(vault), DAY)["hubs"] if h["title"] == "Trip")
    assert hub["work"] == "trip-plan"


def test_validate_work_references_are_only_warnings_when_library_exists(vault, library_home, capsys):
    config = Config.load(vault)
    tasks.hand_off(config, "T-001", DAY, work="missing-work")
    fm.write(vault / "Trip.md", {"kind": "hub", "tier": "warm", "work": "other-work"}, "# Trip\n")
    assert validate.work_warnings(Vault(vault)) == []
    write_library(library_home)
    with pytest.raises(SystemExit) as error:
        nightly.main(["validate", "--vault", str(vault)])
    assert error.value.code == 0
    text = capsys.readouterr().out
    assert "warning: T-001: work 'missing-work' is not listed" in text
    assert "warning: Trip.md: work 'other-work' is not listed" in text
    assert "0 problems\n2 warnings\n" in text
    write_library(library_home, "")
    assert len(validate.work_warnings(Vault(vault))) == 2


def test_validate_uses_full_listing_and_archived_work(vault, library_home):
    config = Config.load(vault)
    task = tasks.hand_off(config, "T-001", DAY, work="work-44")
    tasks.write_archive(config, task, "warm", DAY, whole=True)
    fm.write(vault / "agent/Tasks.md", {"tier": "hot"}, "# Tasks\n" + task.render_line(config))
    fm.write(vault / "Trip.md", {"kind": "hub", "tier": "warm", "work": "work-44"}, "# Trip\n")
    write_library(library_home, "\n".join(
        f"- Work {n} | work-{n} | page | active | read-only | 2026-11-05T12:00:00Z" for n in range(45)))
    assert validate.work_warnings(Vault(vault)) == []
    write_library(library_home, "work-44: could not refresh.\n")
    assert len(validate.work_warnings(Vault(vault))) == 2
    assert validate.run(Vault(vault), set()) == []


def test_validate_work_with_no_task_register(vault, library_home):
    (vault / "agent/Tasks.md").unlink()
    write_library(library_home)
    fm.write(vault / "Trip.md", {"kind": "hub", "tier": "warm", "work": "unknown"}, "# Trip\n")
    assert validate.work_warnings(Vault(vault)) == [
        "Trip.md: work 'unknown' is not listed in $AGENT_HOME/works/LIBRARY.md"]


@pytest.mark.parametrize("location", ["archive", "monthly"])
def test_validate_warns_for_closed_archived_task_work(vault, library_home, location):
    config = Config.load(vault)
    write_library(library_home)
    task = tasks.Task("T-002", "Done", "- **Work:** unknown\n", {"state": "completed"})
    if location == "archive":
        tasks.write_archive(config, task, "cold", DAY, whole=True)
    else:
        path = vault / "agent/Tasks Archive/2026-11.md"
        path.parent.mkdir(parents=True)
        path.write_text("# Completed tasks\n" + task.render())
    assert validate.work_warnings(Vault(vault)) == [
        "T-002: work 'unknown' is not listed in $AGENT_HOME/works/LIBRARY.md"]


def test_every_open_task_and_responsibility_names_a_deliverable(vault):
    config = Config.load(vault)
    open_ids = [task.tid for task in tasks.load_tasks(config)[0] if task.task_state not in ("completed", "cancelled", "set_aside", "yours")]
    warnings = validate.deliverable_warnings(Vault(vault))
    assert [w.split(":")[0] for w in warnings] == open_ids
    tasks.hand_off(config, "T-001", DAY, work="trip-plan")
    path = vault / config.agent("Tasks.md")
    for tid in open_ids[1:]:
        text = path.read_text()
        head = text.index(f"### {tid}")
        end = text.find("\n### ", head + 1)
        end = len(text) if end < 0 else end
        path.write_text(text[:end].rstrip("\n") + "\n- **Delivers:** lineups set, confirmed on the team page\n" + text[end:])
    assert validate.deliverable_warnings(Vault(vault)) == []
    assert all(task.delivers == "lineups set, confirmed on the team page"
               for task in tasks.load_tasks(config)[0] if task.tid in open_ids[1:])
    fm.write(vault / "Fantasy.md", {"kind": "hub", "tier": "warm", "skill": "fantasy", "records": "fantasy"}, "# Fantasy\n")
    assert validate.deliverable_warnings(Vault(vault)) == [
        "Fantasy.md: responsibility with no deliverable; add `work:` or `delivers:` to its frontmatter"]
    fm.write(vault / "Fantasy.md", {"kind": "hub", "tier": "warm", "skill": "fantasy", "records": "fantasy",
                                    "delivers": "lineups set each week"}, "# Fantasy\n")
    assert validate.deliverable_warnings(Vault(vault)) == []
