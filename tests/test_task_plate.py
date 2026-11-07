"""The owner's own things go on their plate, with the day they named."""
from datetime import date

import pytest

from brain import cli as brain_cli, state
from brain.config import Config


def cli(vault, capsys, *args):
    brain_cli.main(["task", *args, "--vault", str(vault)])
    return capsys.readouterr()


def test_new_yours_task_carries_its_due_day_into_the_export(vault, capsys):
    out = cli(vault, capsys, "new", "Renew the passport", "--source", "I need to renew my passport before March",
              "--state", "yours", "--due", "2027-02-28", "--json").out
    import json
    tid = json.loads(out)["id"]
    row = next(t for t in state.build(Config.load(vault), date(2026, 11, 7))["tasks"] if t["id"] == tid)
    assert (row["state"], row["due"]) == ("yours", "2027-02-28")


def test_due_must_be_a_day(vault, capsys):
    with pytest.raises(SystemExit):
        cli(vault, capsys, "new", "Renew the passport", "--source", "someday", "--state", "yours", "--due", "March")
    assert "due must be YYYY-MM-DD" in capsys.readouterr().err
