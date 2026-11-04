from brain import nightly
from brain.redact import redact, redact_paths


def test_key_shapes_and_secret_assignments_are_scrubbed_and_prose_is_not():
    text = ("Here is the key AIza" + "B7" * 17 + "B" + " for maps.\n"
            "export OPENAI_API_KEY=sk-proj-" + "a9" * 20 + "\n"
            "SLACK_BOT_TOKEN: xoxb-1234567890-abcdefghij\n"
            "github ghp_" + "Q1" * 18 + "\n"
            "DB_PASSWORD=hunter2hunter2hunter2\n"
            "Set SLACK_BOT_TOKEN=xoxb-your-bot-token and OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx in the env.\n"
            "STORAGE_KEY=borough-filter-v2 DEMIURGE_COMPUTER_KEY=~/.ssh/demiurge_computer_ed25519 NOPASSWD=/usr/bin/true\n"
            "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----\n"
            "The token budget is fine; GITHUB_TOKEN=$GH_TOKEN and op://Vault/Item stay.\n")
    clean, n = redact(text)
    assert "AIza" not in clean and "sk-proj" not in clean and "xoxb-1234567890" not in clean and "ghp_" not in clean
    assert "hunter2" not in clean and "MIIE" not in clean
    assert "OPENAI_API_KEY=[REDACTED:" in clean and "DB_PASSWORD=[REDACTED:secret]" in clean
    assert "The token budget is fine; GITHUB_TOKEN=$GH_TOKEN and op://Vault/Item stay." in clean
    assert "xoxb-your-bot-token" in clean and "sk-xxxxxxxxxxxxxxxxxxxxxxxx" in clean
    assert "STORAGE_KEY=borough-filter-v2 DEMIURGE_COMPUTER_KEY=~/.ssh/demiurge_computer_ed25519 NOPASSWD=/usr/bin/true" in clean
    assert n == 6
    assert redact(clean) == (clean, 0)


def test_the_redact_command_rewrites_only_files_that_held_a_credential(tmp_path, capsys):
    sessions = tmp_path / "Sessions"
    sessions.mkdir()
    (sessions / "a.md").write_text("key AIza" + "C3" * 17 + "C" + "\n", encoding="utf-8")
    (sessions / "b.md").write_text("nothing here\n", encoding="utf-8")
    assert list(redact_paths([sessions])) == [sessions / "a.md"]
    assert "[REDACTED:google-api-key]" in (sessions / "a.md").read_text(encoding="utf-8")
    assert redact_paths([sessions]) == {}
