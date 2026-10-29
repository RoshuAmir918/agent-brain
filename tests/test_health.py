"""Health report: stages from commit subjects, engine state, counts; never note contents."""
import subprocess
from datetime import date

from brain import fm, health, nightly
from brain.config import Config
from conftest import make_vault


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                                   text=True, stderr=subprocess.PIPE).strip()


def repo_vault(tmp_path):
    root = make_vault(tmp_path / "memory")
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "init")
    for subject in ("sleep1(2026-10-03)", "attribution(2026-10-05)", "sleep1(2026-10-05)", "rem(2026-10-06)"):
        git(tmp_path, "commit", "-q", "--allow-empty", "-m", subject)
    (root / "Conversations/Digests").mkdir(parents=True)
    (root / "Conversations/Digests/2026-10-05.md").write_text(
        "---\nkind: log\n---\n\ncoverage: 2 sessions included, 9 routine sessions and 1 evaluation sessions excluded, 0 turns omitted by the word cap\n")
    (root / "hermes").mkdir()
    (root / "hermes/engine-version").write_text("0.1.0\n")
    (root / "hermes/pending-migration.md").write_text("---\nkind: log\n---\n# pending\n")
    (root / "people/Alice").mkdir(parents=True)
    fm.write(root / "people/Alice/Profile.md", {"tier": "warm"}, "# Alice\nPRIVATE FACT about Alice.\n")
    return root


def test_report_reads_stages_engine_export_and_counts(tmp_path):
    root = repo_vault(tmp_path)
    data = health.report(Config.load(root), date(2026, 10, 6))
    assert data["stages"]["sleep1"] == {"last": "2026-10-05", "state": "ok"}
    assert data["stages"]["rem"] == {"last": "2026-10-06", "state": "ok"}
    assert data["stages"]["attribution"]["state"] == "ok"
    assert data["stages"]["sleep2"] == {"last": None, "state": "never"}
    assert data["engine"]["recorded"] == "0.1.0" and data["engine"]["pending_migration_since"] == "uncommitted"
    assert data["export"]["digest"] == "2026-10-05" and data["export"]["coverage"].startswith("2 sessions included")
    assert data["vault"]["open_tasks"] == 1 and data["vault"]["notes"]["warm"] == 1
    assert data["git"]["dirty"] > 0 and data["git"]["ahead"] == "?"


def test_stale_stage_and_missing_digest(tmp_path):
    root = repo_vault(tmp_path)
    data = health.report(Config.load(root), date(2026, 10, 9))
    assert data["stages"]["sleep1"]["state"] == "stale"
    assert data["export"] == {"expected": "2026-10-08", "digest": None, "coverage": None}
    assert "no digest for 2026-10-08" in health.render(data)


def test_render_and_write_carry_no_note_contents(tmp_path, capsys):
    root = repo_vault(tmp_path)
    text = health.render(health.report(Config.load(root), date(2026, 10, 6)))
    assert text.startswith("# Health: Agent, 2026-10-06\n") and "PRIVATE" not in text and "Alice" not in text
    assert "Git:" in text
    nightly.main(["health", "--vault", str(root), "--date", "2026-10-06", "--write"])
    written = root / "hermes/health.md"
    assert str(written) in capsys.readouterr().out
    saved = written.read_text()
    assert saved.startswith("---\nkind: log\n---\n# Health:") and "Git:" not in saved
    before = written.stat().st_mtime_ns
    nightly.main(["health", "--vault", str(root), "--date", "2026-10-06", "--write"])
    assert written.stat().st_mtime_ns == before
    nightly.main(["health", "--vault", str(root), "--date", "2026-10-06", "--json"])
    assert '"stages"' in capsys.readouterr().out


def test_report_outside_git_is_still_a_report(tmp_path):
    root = make_vault(tmp_path / "memory")
    data = health.report(Config.load(root), date(2026, 10, 6))
    assert all(entry["state"] == "never" for entry in data["stages"].values())
    assert "no git repository" in health.render(data)


def test_pending_since_is_when_this_migration_arrived_not_when_the_last_one_left(tmp_path):
    import os
    root = repo_vault(tmp_path)
    pending = root / "hermes/pending-migration.md"
    env = {"GIT_COMMITTER_DATE": "2026-10-21T12:00:00", "GIT_AUTHOR_DATE": "2026-10-21T12:00:00"}
    def dated(day, *args):
        os.environ.update({"GIT_COMMITTER_DATE": f"{day}T12:00:00", "GIT_AUTHOR_DATE": f"{day}T12:00:00"})
        try:
            git(tmp_path, *args)
        finally:
            for key in env:
                os.environ.pop(key, None)
    dated("2026-10-21", "add", "-A"); dated("2026-10-21", "commit", "-q", "-m", "older migration arrives")
    pending.unlink(); dated("2026-10-27", "add", "-A"); dated("2026-10-27", "commit", "-q", "-m", "older migration done")
    pending.write_text("# newer\n")
    assert health.report(Config.load(root), date(2026, 10, 28))["engine"]["pending_migration_since"] == "uncommitted"
    dated("2026-10-28", "add", "-A"); dated("2026-10-28", "commit", "-q", "-m", "newer migration arrives")
    assert health.report(Config.load(root), date(2026, 10, 28))["engine"]["pending_migration_since"] == "2026-10-28"
