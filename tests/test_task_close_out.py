"""A completed task says what was left and what could come next."""
import pytest

from brain import cli as brain_cli, state, tasks
from brain.config import Config
from datetime import date


def cli(vault, capsys, *args):
    brain_cli.main(["task", *args, "--vault", str(vault)])
    return capsys.readouterr()


def row(vault, tid):
    return next(t for t in state.build(Config.load(vault), date(2026, 11, 5))["tasks"] if t["id"] == tid)


def test_done_records_left_and_then_and_reopen_clears_them(vault, capsys):
    tid = tasks.load_tasks(Config.load(vault))[0][0].tid
    cli(vault, capsys, tid, "done", "--why", "saved the list", "--left", "two listings had no phone number",
        "--then", "Apply to the three best listings", "--then", "Set up viewing times")
    got = row(vault, tid)
    assert (got["outcome"], got["left"], got["then"]) == ("saved the list", "two listings had no phone number",
                                                          ["Apply to the three best listings", "Set up viewing times"])
    cli(vault, capsys, tid, "reopen", "--why", "more to do")
    got = row(vault, tid)
    assert (got["left"], got["then"]) == (None, [])
    cli(vault, capsys, tid, "done", "--why", "finished again")
    got = row(vault, tid)
    assert (got["outcome"], got["left"], got["then"]) == ("finished again", None, [])


@pytest.mark.parametrize("args, message", [
    (("set", "--left", "x"), "--left and --then go with task T-nnn done"),
    (("drop", "--why", "no", "--then", "x"), "--left and --then go with task T-nnn done"),
    (("done", "--why", "ok", "--then", "a", "--then", "b", "--then", "c", "--then", "d"), "at most 3 --then stages"),
])
def test_close_out_flags_refused_without_writes(vault, capsys, args, message):
    config = Config.load(vault)
    tid = tasks.load_tasks(config)[0][0].tid
    before = (vault / config.agent("Tasks.md")).read_text()
    with pytest.raises(SystemExit):
        cli(vault, capsys, tid, *args)
    assert message in capsys.readouterr().err
    assert (vault / config.agent("Tasks.md")).read_text() == before
