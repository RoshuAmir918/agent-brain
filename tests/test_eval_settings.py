"""BRAIN_TODAY and the [context] settings: what an evaluation harness pins and varies per run."""
import json
from datetime import date, datetime

import pytest

from brain import compile_context, fm, nightly
from brain.config import Config, Context
from brain.vault import Vault

ENV = ("BRAIN_TODAY", *Context.ENV.values())


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def vault(vault):
    fm.write(vault / "Hot Set.md", {}, "# Hot set\n\n## Automatic load\n\n- HOT_ENTRY\n")
    return vault


def toml(vault, context):
    (vault.parent / "brain.toml").write_text('[brain]\nvault = "' + vault.name + '"\n\n[context]\n' + context)


def run(capsys, *argv):
    nightly.main(list(argv))
    return capsys.readouterr().out


# ---------- BRAIN_TODAY

def test_brain_today_pins_today_and_now_keeps_the_time_of_day(vault, monkeypatch):
    config = Config.load(vault)
    monkeypatch.setenv("BRAIN_TODAY", "2026-03-04")
    assert config.today() == date(2026, 3, 4)
    moment = datetime(2026, 10, 28, 12, 34, tzinfo=config.zone)
    assert config.now(moment) == datetime(2026, 3, 4, 12, 34, tzinfo=config.zone)
    monkeypatch.delenv("BRAIN_TODAY")
    assert config.now(moment) == moment


@pytest.mark.parametrize("value", ["2026-13-01", "tomorrow", "04/03/2026"])
def test_invalid_brain_today_fails_every_command(vault, monkeypatch, capsys, value):
    monkeypatch.setenv("BRAIN_TODAY", value)
    with pytest.raises(SystemExit):
        nightly.main(["state", "--vault", str(vault)])
    assert "BRAIN_TODAY must be a date as YYYY-MM-DD" in capsys.readouterr().err


def test_state_window_and_ledger_rows_follow_brain_today(vault, monkeypatch, capsys):
    monkeypatch.setenv("BRAIN_TODAY", "2026-03-04")
    run(capsys, "ledger", "append", "watch", "--vault", str(vault), "--outcome", "nothing", "--account", "Checked.")
    rows = json.loads(run(capsys, "state", "--vault", str(vault), "--json"))["ledger"]
    assert [row["at"][:10] for row in rows] == ["2026-03-04"]
    monkeypatch.setenv("BRAIN_TODAY", "2026-05-01")
    assert json.loads(run(capsys, "state", "--vault", str(vault), "--json"))["ledger"] == []


def test_needs_you_and_stale_tasks_follow_brain_today(vault, monkeypatch, capsys):
    monkeypatch.setenv("BRAIN_TODAY", "2026-03-04")
    run(capsys, "needs-you", "--vault", str(vault), "--ask", "Pick a venue?")
    items = json.loads(run(capsys, "needs-you", "--vault", str(vault), "--json"))
    assert [(item["text"], item["since"]) for item in items] == [("Pick a venue?", "2026-03-04")]
    monkeypatch.setenv("BRAIN_TODAY", "2026-09-25")   # T-001 was last used 2026-09-20
    assert run(capsys, "stale-tasks", "--vault", str(vault)) == ""
    monkeypatch.setenv("BRAIN_TODAY", "2026-10-11")
    assert run(capsys, "stale-tasks", "--vault", str(vault)).startswith("T-001 · ")


def test_nightly_follows_brain_today_and_date_wins(vault, monkeypatch, capsys):
    monkeypatch.setenv("BRAIN_TODAY", "2026-03-04")
    run(capsys, "nightly", "--vault", str(vault), "--write")
    assert "## 2026-03-04 nightly script" in (vault / "agent/Sleep Log/2026-03.md").read_text()
    assert "Generated 2026-03-04" in (vault / "Hot Set.md").read_text()
    run(capsys, "nightly", "--vault", str(vault), "--write", "--date", "2026-05-02")
    assert "## 2026-05-02 nightly script" in (vault / "agent/Sleep Log/2026-05.md").read_text()


def test_at_wins_over_brain_today(vault, monkeypatch, capsys):
    monkeypatch.setenv("BRAIN_TODAY", "2026-03-04")
    path = run(capsys, "ledger", "append", "watch", "--vault", str(vault), "--outcome", "nothing",
               "--account", "Checked.", "--at", "2026-03-01 08:00").strip()
    assert "2026-03-01 08:00" in open(path).read()


# ---------- [context]

def test_defaults_are_the_hard_coded_values(vault):
    assert Config.load(vault).context == Context()
    default = compile_context.build(Vault(vault))
    toml(vault, 'hot_words = 6000\nsections = ["before", "start", "current", "primed", "goals", "triggers", "upcoming", "tasks", "hot", "directory"]\n'
                'prefetch = true\nprefetch_limit = 6\nprefetch_chars = 1500\nprefetch_form = "names"\n'
                'hot_threshold = 7\nwarm_threshold = 28\n')
    assert Config.load(vault).context == Context()
    assert compile_context.build(Vault(vault)) == default


def test_sections_choose_the_parts_of_hermes_md(vault, monkeypatch):
    fm.write(vault / "START.md", {"tier": "hot", "pinned": True}, "# Start\nSTART_BODY\n")
    full = compile_context.build(Vault(vault))
    assert "START_BODY" in full and "# Current" in full and "HOT_ENTRY" in full and "## Directory" in full
    toml(vault, 'sections = ["directory"]\n')
    text = compile_context.build(Vault(vault))
    assert "START_BODY" not in text and "# Current" not in text and "## Directory" in text
    monkeypatch.setenv("BRAIN_CONTEXT_SECTIONS", "start, triggers")
    text = compile_context.build(Vault(vault))
    assert "## memory/START.md\n\n# Start\nSTART_BODY" in text and "# Triggers" in text
    assert all(part not in text for part in ("# Current", "HOT_ENTRY", "## Directory", "# Tasks"))
    assert full.index("## memory/START.md") < full.index("## memory/agent/Triggers.md")
    assert text.endswith("Warm Index, Conversations and everything else are retrieved on demand with `brain retrieve`.\n")


def test_hot_words_sets_the_hot_set_budget_and_context_warning(vault, monkeypatch, capsys):
    toml(vault, "hot_words = 20\n")
    run(capsys, "nightly", "--vault", str(vault), "--write", "--date", "2026-10-05")
    assert "Budget 20 words" in (vault / "Hot Set.md").read_text()
    monkeypatch.setenv("BRAIN_HOT_WORDS", "3")
    run(capsys, "nightly", "--vault", str(vault), "--write", "--date", "2026-10-05")
    assert "Budget 3 words" in (vault / "Hot Set.md").read_text()
    nightly.main(["compile-context", "--vault", str(vault)])
    assert "WARNING: over 3 words" in capsys.readouterr().err


def test_tier_thresholds_retier_notes(vault, monkeypatch):
    fm.write(vault / "Hub.md", {"uses": ["2026-10-05"], "tier": "warm"}, "# Hub\n")
    today = date(2026, 10, 15)   # ten days after its one use

    def tier():
        loaded = Vault(vault)
        nightly.retier(loaded, today, [])
        return loaded.notes["Hub.md"].data["tier"]

    assert tier() == "warm"
    toml(vault, "hot_threshold = 14\nwarm_threshold = 30\n")
    assert tier() == "hot"
    monkeypatch.setenv("BRAIN_HOT_THRESHOLD", "3")
    monkeypatch.setenv("BRAIN_WARM_THRESHOLD", "5")
    assert tier() == "cold"


@pytest.mark.parametrize("name, value, message", [
    ("BRAIN_CONTEXT_SECTIONS", "start,sidebar", "unknown sections sidebar"),
    ("BRAIN_HOT_WORDS", "many", "BRAIN_HOT_WORDS must be a non-negative integer"),
    ("BRAIN_PREFETCH", "yes", "BRAIN_PREFETCH must be 0 or 1"),
    ("BRAIN_PREFETCH_LIMIT", "-1", "BRAIN_PREFETCH_LIMIT must be a non-negative integer"),
    ("BRAIN_PREFETCH_FORM", "summary", "BRAIN_PREFETCH_FORM must be one of names, snippet, full"),
    ("BRAIN_WARM_THRESHOLD", "3", "the hot threshold cannot exceed the warm threshold"),
])
def test_invalid_settings_fail_loudly(vault, monkeypatch, capsys, name, value, message):
    monkeypatch.setenv(name, value)
    with pytest.raises(SystemExit):
        nightly.main(["state", "--vault", str(vault)])
    assert message in capsys.readouterr().err


@pytest.mark.parametrize("context, message", [
    ('sections = "start"\n', "context.sections must be a list"),
    ("prefetch_chars = 1.5\n", "context.prefetch_chars must be a non-negative integer"),
    ("prefetch = 1\n", "context.prefetch must be 0 or 1"),
    ("budget = 3\n", "only supported context keys"),
])
def test_invalid_toml_settings_fail_loudly(vault, context, message):
    toml(vault, context)
    with pytest.raises(ValueError, match=message):
        Config.load(vault)
