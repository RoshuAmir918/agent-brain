"""A task entry stays short enough for the night to keep it hot: a write that would grow its current fields past the
limit is refused while the writer still has what it meant, and a write that shrinks it is always taken."""
from datetime import date

import pytest

from brain import cli as brain_cli, tasks
from brain.config import Config

TODAY = date(2026, 11, 6)


def cli(vault, capsys, *args):
    brain_cli.main(["task", *args, "--vault", str(vault)])
    return capsys.readouterr().out.strip()


def words(n):
    return " ".join(f"w{i}" for i in range(n))


def find(config, tid):
    registered, archived = tasks.load_tasks(config)
    return next(t for t in registered + archived if t.tid == tid)


def test_a_write_that_grows_the_entry_past_the_limit_is_refused_with_how_to_fix_it(vault, capsys):
    cli(vault, capsys, "T-001", "set", "--next", words(40))
    with pytest.raises(SystemExit):
        cli(vault, capsys, "T-001", "set", "--status", words(90), "--verified", words(60))
    assert "Say it shorter" in capsys.readouterr().err
    assert "w89" not in find(Config.load(vault), "T-001").body


def test_an_entry_already_too_long_takes_writes_that_shrink_it_and_its_dated_reviews(vault, capsys):
    config = Config.load(vault)
    task = find(config, "T-001")
    task.body += f"- **Status:** {words(200)}\n"
    tasks.save_task(config, task, TODAY)
    cli(vault, capsys, "T-001", "set", "--status", "Two chapters left.")
    cli(vault, capsys, "T-001", "set", "--next", words(30))
    tasks.set_fields(config, "T-001", TODAY, reviewed="kept: still due Friday")
    assert "Two chapters left." in find(config, "T-001").body


def test_a_new_task_must_start_short(vault, capsys):
    with pytest.raises(SystemExit):
        cli(vault, capsys, "new", "Long one", "--source", "owner words", "--next", words(170))
    assert "Say it shorter" in capsys.readouterr().err


def test_a_short_entry_with_a_latest_evidence_link_is_trimmed_by_the_night_however_long_its_history(vault, capsys):
    config = Config.load(vault)
    cli(vault, capsys, "T-001", "set", "--next", "Draft chapter 3.", "--evidence", "2026-11-05 [[Receipt]]")
    task = find(config, "T-001")
    task.body += "".join(f"- **Evidence:** 2026-10-{d:02d} {words(30)}\n" for d in range(1, 11))
    tasks.save_task(config, task, TODAY)
    assert tasks.current_words(find(config, "T-001"), config) <= tasks.MAX_WORDS
    assert len(tasks.bounded_hot_body(find(config, "T-001"), config).split()) <= tasks.MAX_WORDS
