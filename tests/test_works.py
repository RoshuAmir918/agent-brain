"""The runtime's Library connects task work to context, handoffs and advisory validation."""
import json
from datetime import date, timedelta

import pytest

from brain import cli, compile_context, fm, journal, needs, nightly, state, tasks, validate, works
from brain.config import Config, SECTIONS
from brain.lock import vault_lock
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


@pytest.mark.parametrize("extra,created,opened", [
    ("", None, None),
    (" | created 2026-10-29 | opened never", date(2026, 10, 29), "never"),
    (" | created 2026-10-29 | opened 2026-11-04", date(2026, 10, 29), date(2026, 11, 4)),
    (" | created 2026-10-29", date(2026, 10, 29), None),
    (" | created 2026-02-30 | opened never", None, "never"),
    (" | created 20261029 | opened 2026-02-30", None, None),
    (" | 2026-10-29 | never", None, None),
])
def test_parse_library_dates_and_legacy_rows(extra, created, opened):
    line = " \t- NYC deal campaign | nyc-deal-campaign | table | draft | editable | 2026-11-05T12:00:00Z" + extra
    body = "# Library\nLibrary sync error: retry later.\n" + line + "\n- Not a work\n"
    assert works.parse(body) == [works.Work("nyc-deal-campaign", "NYC deal campaign", "table", "draft", True,
                                           created, opened)]
    assert works.slugs(body) == {"nyc-deal-campaign"}


def draft_row(slug="trip-plan", title="Trip plan", *, status="draft", access="editable", age=7, opened="never"):
    return (f"- {title} | {slug} | document | {status} | {access} | 2026-11-05T12:00:00Z"
            f" | created {(DAY - timedelta(days=age)).isoformat()} | opened {opened}\n")


@pytest.mark.parametrize("row,eligible", [
    (draft_row(), True),
    (draft_row(age=8), True),
    (draft_row(age=14), True),
    (draft_row(age=6), False),
    (draft_row(age=-1), False),
    (draft_row(opened="2026-11-01"), False),
    (draft_row(status="kept"), False),
    (draft_row(status="retired"), False),
    (draft_row(access="read-only"), False),
    (LISTING.replace("active", "draft"), False),
    (draft_row().replace("created 2026-10-29", "created 2026-02-30"), False),
    (draft_row().replace("opened never", "opened unknown"), False),
])
def test_nightly_library_ask_eligibility_and_exact_text(vault, library_home, row, eligible):
    write_library(library_home, row)
    report = nightly.run(vault, DAY)
    items = needs.items(Config.load(vault), DAY)
    record = library_home / "records/library/asked.json"
    if eligible:
        assert len(items) == 1
        item = items[0]
        assert item.text == "Still want Trip plan in your Library? I can keep it, keep it updated, or put it away."
        assert (item.kind, item.since, item.source, item.task) == ("ask", DAY.isoformat(), "CURRENT#Library/trip-plan", "")
        assert json.loads(record.read_text()) == {"trip-plan": DAY.isoformat()}
        assert "needs-you: Library ask: trip-plan" in report
        assert validate.run(Vault(vault), {"CURRENT.md"}) == []
    else:
        assert items == []
        assert not record.exists()
        assert not any("Library ask:" in line for line in report)


@pytest.mark.parametrize("ending", ["evening", "resolve", "dismiss"])
def test_library_ask_never_repeats_after_leaving_current(vault, library_home, ending):
    write_library(library_home, draft_row())
    config = Config.load(vault)
    nightly.run(vault, DAY)
    record = library_home / "records/library/asked.json"
    before = record.read_bytes()
    nightly.run(vault, DAY)
    assert len(needs.items(config, DAY)) == 1
    if ending == "resolve":
        with vault_lock(config):
            needs.resolve(config, needs.items(config, DAY)[0].id, "Owner said keep it", DAY)
    elif ending == "dismiss":
        with vault_lock(config):
            needs.dismiss(config, needs.items(config, DAY)[0].id, DAY)
    else:
        nightly.run(vault, DAY + timedelta(days=4))
        assert len(needs.items(config, DAY)) == 1
        nightly.run(vault, DAY + timedelta(days=5))
        assert len(needs.items(config, DAY)) == 1
        needs.resolve(config, needs.items(config, DAY)[0].id, "Evening decided to put it away", DAY + timedelta(days=5))
    assert needs.items(config, DAY) == []
    # A title change still names the same work; the durable record is keyed on slug.
    write_library(library_home, draft_row(title="Renamed trip plan"))
    nightly.run(vault, DAY + timedelta(days=6))
    assert needs.items(config, DAY) == []
    assert record.read_bytes() == before


@pytest.mark.parametrize("already_asked", [False, True])
def test_library_preview_never_changes_real_vault_or_records(vault, library_home, already_asked):
    write_library(library_home, draft_row())
    if already_asked:
        nightly.run(vault, DAY)
    before = snapshot(vault.parent)
    report = nightly.preview(vault, DAY)
    assert snapshot(vault.parent) == before
    assert ("needs-you: Library ask: trip-plan" in report) == (not already_asked)
    nightly.run(vault, DAY)
    assert len(needs.items(Config.load(vault), DAY)) == 1


def test_library_asks_use_full_listing_and_distinguish_same_titles(vault, library_home):
    write_library(library_home, LISTING * 40 + draft_row(slug="first-plan") + draft_row(slug="second-plan"))
    nightly.run(vault, DAY)
    assert {item.source for item in needs.items(Config.load(vault), DAY)} == {
        "CURRENT#Library/first-plan", "CURRENT#Library/second-plan"}
    assert json.loads((library_home / "records/library/asked.json").read_text()) == {
        "first-plan": DAY.isoformat(), "second-plan": DAY.isoformat()}


def test_library_ask_recovers_existing_source_after_interruption(vault, library_home):
    write_library(library_home, draft_row())
    config = Config.load(vault)
    journal.add_ask(config, DAY - timedelta(days=1), "An older title's Library question?", "CURRENT#Library/trip-plan")
    nightly.run(vault, DAY)
    items = needs.items(config, DAY)
    assert len(items) == 1 and items[0].text == "An older title's Library question?"
    assert json.loads((library_home / "records/library/asked.json").read_text()) == {
        "trip-plan": (DAY - timedelta(days=1)).isoformat()}


@pytest.mark.parametrize("broken", ["{", "[]"])
def test_library_ask_does_not_reset_unreadable_records(vault, library_home, broken):
    write_library(library_home, draft_row())
    record = library_home / "records/library/asked.json"
    record.parent.mkdir(parents=True)
    record.write_text(broken)
    report = nightly.run(vault, DAY)
    assert needs.items(Config.load(vault), DAY) == []
    assert record.read_text() == broken
    assert any(line.startswith("library: asked record unreadable, skipped asks:") for line in report)


def test_library_record_failed_replace_preserves_record_and_recovers_ask(vault, library_home, monkeypatch):
    write_library(library_home, draft_row())
    record = library_home / "records/library/asked.json"
    record.parent.mkdir(parents=True)
    record.write_text('{"other-work": "2026-10-01"}\n')
    before = record.read_bytes()
    replace = fm.os.replace
    def fail_record(source, target):
        if target == record:
            raise OSError("interrupted record replace")
        replace(source, target)
    with monkeypatch.context() as patch:
        patch.setattr(fm.os, "replace", fail_record)
        with pytest.raises(OSError, match="interrupted record replace"):
            nightly.run(vault, DAY)
    assert record.read_bytes() == before
    assert list(record.parent.iterdir()) == [record]
    nightly.run(vault, DAY + timedelta(days=1))
    assert len(needs.items(Config.load(vault), DAY)) == 1
    assert json.loads(record.read_text()) == {"other-work": "2026-10-01", "trip-plan": DAY.isoformat()}


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
    cli.main(["compile-context", "--vault", str(vault), "--sections", "library"])
    text = capsys.readouterr().out
    assert "## Library" in text and "### T-001" not in text and "## Directory" not in text
    cli.main(["compile-context", "--vault", str(vault), "--sections", "tasks"])
    text = capsys.readouterr().out
    assert "### T-001" in text and "## Library" not in text
    config_file = vault.parent / "brain.toml"
    config_file.write_text(config_file.read_text() + '\n[context]\nsections = ["start", "tasks"]\n')
    cli.main(["compile-context", "--vault", str(vault), "--sections", "tasks,library"])
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
        cli.main(["handoff", "T-001", "--work", slug, "--report", "Home", "--vault", str(vault)])
        capsys.readouterr()
        task = tasks.load_tasks(config)[0][0]
        assert task.work == slug and task.body.count("**Work:**") == 1
        assert "ORIGINAL_FULL_BODY" in task.body
        assert task.handoff == (DAY.isoformat(), "Home")
    cli.main(["handoff", "T-001", "--vault", str(vault)])
    capsys.readouterr()
    assert tasks.load_tasks(config)[0][0].work == "trip-budget"
    cli.main(["continue", "--due", "--vault", str(vault)])
    assert capsys.readouterr().out == (
        "T-001 · handed off 2026-11-05 · Sample · next: ORIGINAL_FULL_BODY · work: trip-budget\n")
    exported = state.build(config, DAY)["tasks"][0]
    assert exported["work"] == "trip-budget"


@pytest.mark.parametrize("slug", ["Trip", "bad_slug", "two words", "../work", "", "plan\n- **Next:** injected"])
def test_handoff_refuses_bad_slug_before_writing(vault, slug, capsys):
    before = snapshot(vault)
    with pytest.raises(SystemExit) as error:
        cli.main(["handoff", "T-001", "--work", slug, "--vault", str(vault)])
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
        cli.main(["validate", "--vault", str(vault)])
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
