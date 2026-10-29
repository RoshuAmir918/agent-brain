import io
import json
from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from brain import audit, fm, ledger, nightly
from brain.config import Config
from brain.vault import Vault
from conftest import nightly_argv, snapshot


@pytest.fixture
def config(tmp_path):
    (tmp_path / "brain.toml").write_text(
        '[brain]\nagent_dir = "Orphan"\nlog_dirs = ["Watch", "Letters", "Results"]\n', encoding="utf-8")
    return Config.load(cwd=tmp_path)


def write_ledger(config, folder, body, month="2026-10"):
    path = config.vault_path / config.agent(folder) / f"{month}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ledger.HEADER + body, encoding="utf-8")
    return path


def read_rows(path):
    return [row for line in path.read_text(encoding="utf-8").splitlines()
            if (row := ledger.parse_row(line)) is not None]


@pytest.mark.parametrize("prefix", ["", "- ", "  - "])
def test_parse_legacy_row(prefix):
    assert ledger.parse_row(prefix + "2026-10-26 | watch | 17:02 PDT own-mail checked; no new urgency. | Preserve existing waits.") == ledger.Row(
        "2026-10-26", "watch", None, "17:02 PDT own-mail checked; no new urgency.", "Preserve existing waits.")


@pytest.mark.parametrize("when", ["2026-10-26", "2026-10-26 17:02", "2026-10-26 08:30–22:30"])
@pytest.mark.parametrize("outcome", ledger.OUTCOMES)
def test_parse_and_render_structured_row(when, outcome):
    row = ledger.Row(when, "watch", outcome, "Own mail checked.", "Preserve existing waits.")
    line = ledger.render_row(row)
    assert line == f"{when} | watch | {outcome} | Own mail checked. | Preserve existing waits."
    assert ledger.parse_row(line) == row
    assert ledger.parse_row("- " + line) == row


@pytest.mark.parametrize("outcome", [None, "progress"])
def test_parse_pipes_inside_account(outcome):
    columns = "progress | " if outcome else ""
    row = ledger.parse_row(f"2026-10-26 17:02 | morning | {columns}Read one | two | three. | ")
    assert row == ledger.Row("2026-10-26 17:02", "morning", outcome, "Read one | two | three.", "")
    if outcome:
        assert ledger.parse_row(ledger.render_row(row)) == row


def test_unrecognised_outcome_is_legacy_account():
    assert ledger.parse_row("2026-10-26 | watch | checked | Own mail. | Wait.") == ledger.Row(
        "2026-10-26", "watch", None, "checked | Own mail.", "Wait.")


@pytest.mark.parametrize("line", [
    "", "  ", "---", "kind: log", "tier: cold", "# 2026-10-26 | watch | nothing | Quiet. | Wait.",
    "when | routine | outcome | account | next step", "date | routine | account | next step",
    "Format: `when | routine | outcome | account | next step`", "2026-10-26 | watch | Quiet.",
    "Not 2026-10-26 | watch | nothing | Quiet. | Wait.",
])
def test_non_rows_are_ignored(line):
    assert ledger.parse_row(line) is None


def test_discovery_registered_and_self_declared_monthly_routine_ledgers(config):
    expected = write_ledger(config, "Watch", "")
    nested = write_ledger(config, "Results/Nested", "")
    declared = write_ledger(config, "Unregistered", "")
    plain = config.vault_path / config.agent("Notes") / "2026-10.md"
    plain.parent.mkdir(parents=True)
    plain.write_text("---\nkind: hub\n---\n\n2026-10-26 | watch | nothing | Not a ledger. | Wait.\n")
    (expected.parent / "2026-10-26.md").write_text("Not monthly.\n")
    (expected.parent / "README.md").write_text("Not monthly.\n")
    for folder in ledger.ENGINE_LOGS:
        write_ledger(config, folder, "2026-10-26 | watch | nothing | Not a routine schema. | Wait.\n")
    config = replace(config, log_dirs=[*config.log_dirs, *sorted(ledger.ENGINE_LOGS), "Results/Nested"])
    assert ledger.discover(config) == sorted([expected, nested, declared])


@pytest.mark.parametrize("columns", ["Mail checked. | Wait.", "done | Mail checked. | Wait."])
def test_ledger_for_finds_routine_not_folder_case_insensitively(config, columns):
    path = write_ledger(config, "Letters", f"2026-10-26 | morning | {columns}\n")
    assert ledger.ledger_for(config, "MORNING", "2026-10") == path


def test_ledger_for_uses_latest_month_if_current_missing(config):
    write_ledger(config, "Letters", "2026-08-26 | evening | Mail. | Wait.\n", "2026-08")
    path = write_ledger(config, "Letters", "2026-09-26 | morning | Mail. | Wait.\n", "2026-09")
    assert ledger.ledger_for(config, "morning", "2026-10") == path.with_name("2026-10.md")
    assert ledger.ledger_for(config, "evening", "2026-10") == config.vault_path / "Orphan/Evening/2026-10.md"


def test_ledger_for_current_month_takes_precedence_and_falls_back(config):
    write_ledger(config, "Letters", "2026-09-26 | morning | Mail. | Wait.\n", "2026-09")
    write_ledger(config, "Letters", "2026-10-26 | evening | Mail. | Wait.\n")
    assert ledger.ledger_for(config, "morning", "2026-10") == config.vault_path / "Orphan/Morning/2026-10.md"
    assert ledger.ledger_for(config, "newRoutine", "2026-10") == config.vault_path / "Orphan/NewRoutine/2026-10.md"


def test_append_creates_header_sanitises_and_leaves_config_alone(config):
    before = (config.repo / "brain.toml").read_bytes()
    path = ledger.append(config, "newRoutine", "done", "Own \n mail\t| checked.", " Keep\n waiting | please. ",
                         datetime(2026, 10, 28, 17, 2))
    assert path == config.vault_path / "Orphan/NewRoutine/2026-10.md"
    data, body = fm.read(path)
    assert data == {"kind": "log", "tier": "cold"}
    assert body.lstrip().startswith("Format: `when | routine | outcome | account | next step`")
    assert read_rows(path) == [ledger.Row("2026-10-28 17:02", "newRoutine", "done", "Own mail / checked.", "Keep waiting / please.")]
    assert (config.repo / "brain.toml").read_bytes() == before


def test_append_uses_existing_folder_and_handles_missing_final_newline(config):
    path = write_ledger(config, "Letters", "2026-10-26 | morning | Mail checked. | Wait.")
    assert ledger.append(config, "morning", "nothing", "Quiet.", "", datetime(2026, 10, 28, 8, 30)) == path
    assert len(read_rows(path)) == 2
    assert path.read_text().count("Format:") == 1
    assert read_rows(path)[-1] == ledger.Row("2026-10-28 08:30", "morning", "nothing", "Quiet.", "")


def test_append_converts_aware_time_before_selecting_month(config):
    path = ledger.append(config, "watch", "waiting", "Quiet.", "", datetime(2026, 11, 1, 1, 5, tzinfo=timezone.utc))
    assert path.name == "2026-10.md"
    assert read_rows(path)[0].when == "2026-10-31 18:05"


def test_append_uses_configured_zone(config):
    path = ledger.append(replace(config, timezone="Asia/Tokyo"), "watch", "progress", "Mail.", "",
                         datetime(2026, 10, 28, 18, 30, tzinfo=timezone.utc))
    assert read_rows(path)[0].when == "2026-10-29 03:30"


def test_append_rejects_bad_outcome_without_writing(config):
    before = snapshot(config.repo)
    with pytest.raises(ValueError, match="invalid outcome"):
        ledger.append(config, "watch", "unknown", "Mail.", "", datetime(2026, 10, 28, 8, 30))
    assert snapshot(config.repo) == before


@pytest.mark.parametrize("routine", ["", "..", "../Outside", "/outside", "watch\nrow", "watch | row"])
def test_append_rejects_unsafe_routine(config, routine):
    with pytest.raises(ValueError, match="routine"):
        ledger.append(config, routine, "done", "Mail.", "", datetime(2026, 10, 28, 8, 30))


def test_ledger_cli_flags(config, capsys):
    path = write_ledger(config, "Letters", "2026-10-26 | morning | Mail. | Wait.\n")
    nightly.main(["ledger", "append", "morning", "--outcome", "progress", "--account", "Mail | checked.\nQuiet.",
                  "--next", "Wait.", "--at", "2026-10-28 08:30", "--vault", str(config.vault_path)])
    assert capsys.readouterr().out.strip() == str(path)
    assert read_rows(path)[-1] == ledger.Row("2026-10-28 08:30", "morning", "progress", "Mail / checked. Quiet.", "Wait.")


@pytest.mark.parametrize("as_json", [False, True])
@pytest.mark.parametrize("optional", [False, True])
def test_ledger_cli_defaults_and_json(config, monkeypatch, capsys, as_json, optional):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 28, 12, 34, tzinfo=tz)

    monkeypatch.setattr(nightly, "datetime", Clock)
    values = {"routine": "watch", "outcome": "nothing", "account": "Mail checked."}
    if optional:
        values.update(next="Wait.", at="2026-10-27 08:30")
    argv = ["ledger", "append", "--vault", str(config.vault_path)]
    if as_json:
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(values)))
        argv.append("--json")
    else:
        argv.append(values["routine"])
        for name, value in values.items():
            if name != "routine":
                argv.extend(["--" + name, value])
    nightly.main(argv)
    path = config.vault_path / "Orphan/Watch/2026-10.md"
    assert capsys.readouterr().out.strip() == str(path)
    assert read_rows(path) == [ledger.Row(values.get("at", "2026-10-28 12:34"), "watch", "nothing", "Mail checked.",
                                          values.get("next", ""))]


@pytest.mark.parametrize("argv, payload, message", [
    ([], None, "missing required field: routine"),
    (["watch", "--account", "Mail."], None, "missing required field: outcome"),
    (["watch", "--outcome", "done"], None, "missing required field: account"),
    (["watch", "--outcome", "bad", "--account", "Mail."], None, "invalid outcome"),
    (["watch", "--outcome", "done", "--account", "Mail.", "--at", "yesterday"], None, "does not match format"),
    (["--json"], '{"routine":"watch","outcome":"done"}', "missing required field: account"),
    (["--json"], '{"routine":"watch","outcome":"bad","account":"Mail."}', "invalid outcome"),
    (["--json"], '{"routine":"watch","outcome":"done","account":null}', "account must be a string"),
    (["--json"], "[]", "one JSON object"),
    (["--json"], "{", "ledger append:"),
    (["--json", "watch"], "{}", "cannot be combined"),
])
def test_ledger_cli_errors_do_not_write(config, monkeypatch, argv, payload, message):
    if payload is not None:
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    before = snapshot(config.repo)
    with pytest.raises(SystemExit) as error:
        nightly.main(["ledger", "append", "--vault", str(config.vault_path), *argv])
    assert message in str(error.value)
    assert error.value.code != 0
    assert snapshot(config.repo) == before


def test_audit_counts_new_rows_without_format_header(config):
    path = ledger.append(config, "watch", "done", "Mail checked.", "Wait.", datetime(2026, 10, 28, 8, 30))
    vault = Vault(config.vault_path)
    vault.load()
    assert audit.ledgers(vault) == [{"ledger": path.relative_to(vault.root).as_posix(), "rows": 1,
                                    "mean_words_per_row": float(len(ledger.render_row(read_rows(path)[0]).split()))}]


def test_collapse_merges_at_first_position_and_preserves_other_rows(config):
    lines = [
        "- 2026-10-26 08:30 | watch | nothing | First check. | First next.",
        "2026-10-26 09:00 | watch | progress | Filed a note. | Continue.",
        "2026-10-26 10:00 | morning | nothing | Mail checked. | Wait.",
        "2026-10-26 | watch | nothing | Untimed check. | Untimed next.",
        "2026-10-26 | watch | Old legacy account. | Legacy next.",
        "2026-10-26 21:00–22:30 | watch | nothing | 3 runs, nothing new. | Range next.",
        "2026-10-26 06:45 | watch | nothing | Out of order. | ",
        "2026-10-28 08:30 | watch | nothing | Today. | Wait.",
        "2026-10-28 09:30 | watch | nothing | Today again. | Wait.",
        "2026-10-29 08:30 | watch | nothing | Future. | Wait.",
        "2026-10-29 09:30 | watch | nothing | Future again. | Wait.",
    ]
    for outcome in ("done", "waiting", "blocked", "failed"):
        lines.append(f"2026-10-26 18:30 | watch | {outcome} | Preserve this. | Continue.")
    path = write_ledger(config, "Watch", "\n".join(lines) + "\n")
    assert ledger.collapse(config, date(2026, 10, 28)) == [path]
    expected = ["2026-10-26 06:45–22:30 | watch | nothing | 6 runs, nothing new. | "]
    expected.extend(line for position, line in enumerate(lines) if position not in (0, 3, 5, 6))
    assert path.read_text() == ledger.HEADER + "\n".join(expected) + "\n"
    before = path.read_bytes()
    assert ledger.collapse(config, date(2026, 10, 28)) == []
    assert path.read_bytes() == before


def test_collapse_date_only_and_multiple_routines_and_months(config):
    paths = []
    for month in ("2026-09", "2026-10"):
        body = (f"{month}-25 | morning | nothing | Mail. | First.\n"
                f"{month}-25 | watch | nothing | Mail. | Watch first.\n"
                f"{month}-25 | morning | nothing | Mail. | Last.\n"
                f"{month}-25 | watch | nothing | Mail. | Watch last.\n"
                f"{month}-26 | morning | nothing | Only one. | Wait.\n")
        paths.append(write_ledger(config, "Letters", body, month))
    assert ledger.collapse(config, date(2026, 10, 28)) == paths
    for path in paths:
        day = path.stem + "-25"
        assert read_rows(path)[:2] == [ledger.Row(day, "morning", "nothing", "2 runs, nothing new.", "Last."),
                                      ledger.Row(day, "watch", "nothing", "2 runs, nothing new.", "Watch last.")]
        assert read_rows(path)[2].account == "Only one."
    assert ledger.collapse(config, date(2026, 10, 28)) == []


def test_collapse_sums_existing_ranges_and_date_only_counts(config):
    path = write_ledger(config, "Watch",
                        "2026-10-26 08:30–12:30 | watch | nothing | 4 runs, nothing new. | First.\n"
                        "2026-10-26 13:30–22:30 | watch | nothing | 3 runs, nothing new. | Second.\n"
                        "2026-10-26 | watch | nothing | 5 runs, nothing new. | Third.\n"
                        "2026-10-26 07:30 | watch | nothing | One more. | Last.\n")
    assert ledger.collapse(config, date(2026, 10, 28)) == [path]
    assert read_rows(path) == [ledger.Row("2026-10-26 07:30–22:30", "watch", "nothing", "13 runs, nothing new.", "Last.")]
    assert ledger.collapse(config, date(2026, 10, 28)) == []


def test_collapse_can_extend_a_previously_collapsed_row(config):
    path = write_ledger(config, "Watch", "2026-10-26 09:30 | watch | nothing | First. | Wait.\n"
                        "2026-10-26 10:30 | watch | nothing | Second. | Wait.\n")
    ledger.collapse(config, date(2026, 10, 28))
    ledger.append(config, "watch", "nothing", "Late receipt.", "Last.", datetime(2026, 10, 26, 8, 30))
    assert ledger.collapse(config, date(2026, 10, 28)) == [path]
    assert read_rows(path) == [ledger.Row("2026-10-26 08:30–10:30", "watch", "nothing", "3 runs, nothing new.", "Last.")]


def test_collapse_preserves_crlf_and_non_row_bytes(config):
    path = write_ledger(config, "Watch", "")
    prefix = ledger.HEADER.replace("\n", "\r\n") + "# Unchanged heading\r\n"
    body = ("2026-10-26 09:30 | watch | nothing | First. | Wait.\r\n"
            "Unchanged prose.\r\n"
            "2026-10-26 | watch | Legacy text. | Keep.\r\n"
            "2026-10-26 | watch | nothing | Second. | Last.")
    path.write_bytes((prefix + body).encode())
    ledger.collapse(config, date(2026, 10, 28))
    assert path.read_bytes() == (prefix + "2026-10-26 09:30–09:30 | watch | nothing | 2 runs, nothing new. | Last.\r\n"
                                 "Unchanged prose.\r\n2026-10-26 | watch | Legacy text. | Keep.\r\n").encode()


def test_nightly_preview_is_read_only_and_write_collapses_before_validation_and_commit(vault, monkeypatch, capsys):
    config_path = vault.parent / "brain.toml"
    config_path.write_text(config_path.read_text() + 'log_dirs = ["Watch"]\n')
    config = Config.load(vault)
    path = write_ledger(config, "Watch", "2026-10-26 08:30 | watch | nothing | First. | Wait.\n"
                        "2026-10-26 22:30 | watch | nothing | Second. | Last.\n")
    before = snapshot(config.repo)
    nightly.main(nightly_argv(vault, "2026-10-28"))
    assert snapshot(config.repo) == before
    assert "report only; nothing written" in capsys.readouterr().out
    validate_run = nightly.validate.run
    checked, committed = [], []

    def validate_after_collapse(current, changed):
        assert read_rows(path)[0].account == "2 runs, nothing new."
        assert path.relative_to(vault).as_posix() in changed
        checked.append(True)
        return validate_run(current, changed)

    def commit_after_collapse(root, message):
        assert root == vault
        assert read_rows(path)[0].account == "2 runs, nothing new."
        committed.append(True)
        return "fixture"

    monkeypatch.setattr(nightly.validate, "run", validate_after_collapse)
    monkeypatch.setattr(nightly, "commit", commit_after_collapse)
    nightly.main(nightly_argv(vault, "2026-10-28", "--write", "--commit"))
    assert checked == [True] and committed == [True]
    assert "ledger: collapsed nothing rows" in capsys.readouterr().out
    before = path.read_bytes()
    monkeypatch.setattr(nightly.validate, "run", validate_run)
    nightly.main(nightly_argv(vault, "2026-10-28", "--write"))
    assert path.read_bytes() == before
    assert "ledger: collapsed" not in capsys.readouterr().out


def test_first_ledger_of_a_new_routine_is_found_and_collapsed_before_log_dirs_lists_it(config):
    first = ledger.append(config, "harvest", "nothing", "Checked.", "", datetime(2026, 10, 26, 9, 0))
    assert ledger.ledger_for(config, "harvest", "2026-10") == first
    ledger.append(config, "harvest", "nothing", "Checked again.", "", datetime(2026, 10, 26, 10, 0))
    assert ledger.collapse(config, date(2026, 10, 27)) == [first]
    assert [row.account for row in read_rows(first)] == ["2 runs, nothing new."]
