from brain import nightly
from brain.redact import redact, redact_paths


def test_key_shapes_and_secret_assignments_are_scrubbed_and_prose_is_not():
    text = ("Here is the key AIza" + "B" * 35 + " for maps.\n"
            "export OPENAI_API_KEY=sk-proj-" + "a" * 40 + "\n"
            "SLACK_BOT_TOKEN: xoxb-1234567890-abcdefghij\n"
            "github ghp_" + "x" * 36 + "\n"
            "DB_PASSWORD=hunter2hunter2hunter2\n"
            "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----\n"
            "The token budget is fine; GITHUB_TOKEN=$GH_TOKEN and op://Vault/Item stay.\n")
    clean, n = redact(text)
    assert "AIza" not in clean and "sk-proj" not in clean and "xoxb" not in clean and "ghp_" not in clean
    assert "hunter2" not in clean and "MIIE" not in clean
    assert "OPENAI_API_KEY=[REDACTED:" in clean and "DB_PASSWORD=[REDACTED:secret]" in clean
    assert "The token budget is fine; GITHUB_TOKEN=$GH_TOKEN and op://Vault/Item stay." in clean
    assert n == 6
    assert redact(clean) == (clean, 0)


def test_the_redact_command_rewrites_only_files_that_held_a_credential(tmp_path, capsys):
    sessions = tmp_path / "Sessions"
    sessions.mkdir()
    (sessions / "a.md").write_text("key AIza" + "C" * 35 + "\n", encoding="utf-8")
    (sessions / "b.md").write_text("nothing here\n", encoding="utf-8")
    assert list(redact_paths([sessions])) == [sessions / "a.md"]
    assert "[REDACTED:google-api-key]" in (sessions / "a.md").read_text(encoding="utf-8")
    assert redact_paths([sessions]) == {}
