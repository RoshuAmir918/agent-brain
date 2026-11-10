import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from brain import cli, evidence
from brain.sources import imessage

NOW = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)


def stamp(at: datetime) -> int:
    return int((at - imessage.EPOCH).total_seconds() * 1e9)


def blob(text: str) -> bytes:
    data = text.encode()
    size = bytes([len(data)]) if len(data) < 0x80 else b"\x81" + len(data).to_bytes(2, "little")
    return b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+" + size + data + b"\x86\x84"


@pytest.fixture
def messages(tmp_path):
    path = tmp_path / "chat.db"
    db = sqlite3.connect(path)
    db.executescript("""
      CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT, service TEXT);
      CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, style INTEGER, display_name TEXT);
      CREATE TABLE message (ROWID INTEGER PRIMARY KEY, date INTEGER, is_from_me INTEGER, handle_id INTEGER, text TEXT, attributedBody BLOB,
                            associated_message_type INTEGER DEFAULT 0, item_type INTEGER DEFAULT 0, is_system_message INTEGER DEFAULT 0);
      CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
      CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
    """)
    db.executemany("INSERT INTO handle VALUES (?, ?, 'iMessage')", [(1, "+15550100199"), (2, "sam@example.com"), (3, "+15550100277")])
    db.executemany("INSERT INTO chat VALUES (?, ?, ?)", [(10, 45, ""), (11, 45, ""), (12, 43, "Climbing crew")])
    db.executemany("INSERT INTO chat_handle_join VALUES (?, ?)", [(10, 1), (11, 2), (12, 1), (12, 3)])
    rows, n = [], 0

    def say(chat, at, mine, handle, text=None, body=None, **extra):
        nonlocal n
        n += 1
        rows.append((n, stamp(at), mine, 0 if mine else handle, text, body, extra.get("assoc", 0)))
        db.execute("INSERT INTO chat_message_join VALUES (?, ?)", (chat, n))

    day = NOW - timedelta(days=3)
    say(10, day, 0, 1, "dinner?")                                    # Kim starts
    say(10, day + timedelta(minutes=5), 1, 1, None, blob("yes, try https://www.example-bistro.com/menu?utm_source=x ok"))
    say(10, day + timedelta(minutes=6), 0, 1, "", None, assoc=2000)  # a tapback: not a message
    say(10, day + timedelta(days=1), 1, 1, "running late")           # owner starts a new conversation
    say(10, day + timedelta(days=1, minutes=1), 0, 1, "reset your password https://auth.example.com/reset?token=abc")
    say(11, NOW - timedelta(days=200), 0, 2, "long time https://youtube.com/watch?v=abc&t=4&si=track")
    say(12, day, 0, 3, "gym saturday https://maps.example.com/place/Boulder+Barn")
    say(12, day + timedelta(minutes=2), 1, 1, "in")
    db.executemany("INSERT INTO message (ROWID, date, is_from_me, handle_id, text, attributedBody, associated_message_type) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    db.commit()
    db.close()
    book = tmp_path / "AddressBook-v22.abcddb"
    b = sqlite3.connect(book)
    b.executescript("""
      CREATE TABLE ZABCDRECORD (Z_PK INTEGER PRIMARY KEY, ZFIRSTNAME TEXT, ZLASTNAME TEXT, ZORGANIZATION TEXT);
      CREATE TABLE ZABCDPHONENUMBER (ZOWNER INTEGER, ZFULLNUMBER TEXT);
      CREATE TABLE ZABCDEMAILADDRESS (ZOWNER INTEGER, ZADDRESS TEXT);
      INSERT INTO ZABCDRECORD VALUES (1, 'Kim', 'Lee', NULL), (2, 'Sam', NULL, NULL);
      INSERT INTO ZABCDPHONENUMBER VALUES (1, '(555) 010-0199');
      INSERT INTO ZABCDEMAILADDRESS VALUES (2, 'Sam@Example.com');
    """)
    b.commit()
    b.close()
    return path, book


def test_attributed_bodies_give_up_their_text():
    assert imessage.body_text(blob("hello there")) == "hello there"
    assert imessage.body_text(blob("x" * 300)) == "x" * 300
    assert imessage.body_text(None) == "" and imessage.body_text(b"no string here") == ""


def test_dates_read_as_nanoseconds_or_old_seconds():
    assert imessage.when(stamp(NOW)) == NOW
    assert imessage.when(int((NOW - imessage.EPOCH).total_seconds())) == NOW


def test_links_lose_tracking_and_anything_that_carries_a_secret():
    assert imessage.clean("https://www.Example-Bistro.com/menu?utm_source=x).") == "https://www.example-bistro.com/menu"
    assert imessage.clean("https://youtube.com/watch?v=abc&t=4&si=track") == "https://youtube.com/watch?v=abc"
    assert imessage.clean("https://auth.example.com/reset?token=abc") is None
    assert imessage.clean("https://example.com/verify-email/123") is None


def test_export_ranks_people_by_closeness_names_them_and_keeps_no_text(messages):
    db, book = messages
    data = imessage.export(db, [book], NOW)
    kim, other, sam = data["people"]   # a small group's days count toward closeness
    assert (kim["name"], kim["sent"], kim["received"], kim["direct"], kim["groups"]) == ("Kim Lee", 2, 2, 1, 1)
    assert (kim["startedByOwner"], kim["startedByThem"], kim["daysRecent"]) == (1, 1, 2)
    assert sam["name"] == "Sam" and sam["daysRecent"] == 0 and sam["received"] == 1
    assert other["name"] == "unknown (0277)" and other["groups"] == 1 and other["sent"] == 0
    assert data["groups"] == [{"name": "Climbing crew", "members": 2, "messages": 2, "mine": 1, "last": (NOW - timedelta(days=3)).date().isoformat()}]
    urls = {link["url"]: link for link in data["links"]}
    assert set(urls) == {"https://www.example-bistro.com/menu", "https://maps.example.com/place/Boulder+Barn"}
    assert data["media"]["sites"][0]["site"] == "youtube.com" and data["media"]["sites"][0]["shared"] == 1
    assert urls["https://www.example-bistro.com/menu"]["where"] == ["Kim Lee"] and urls["https://www.example-bistro.com/menu"]["mine"] == 1
    assert urls["https://maps.example.com/place/Boulder+Barn"]["where"] == ["Climbing crew"]
    dumped = json.dumps(data)
    assert "dinner" not in dumped and "running late" not in dumped and "token=abc" not in dumped


def test_a_window_and_no_contacts(messages):
    db, _ = messages
    data = imessage.export(db, [], NOW, days=30)
    assert [p["name"] for p in data["people"]][:1] == ["unknown (0199)"]
    assert all("youtube" not in link["url"] for link in data["links"]) and data["media"]["sites"] == []


def test_rows_are_evidence_that_names_no_handle_and_quotes_no_message(messages):
    db, book = messages
    rows = imessage.rows(imessage.export(db, [book], NOW), NOW.date())
    rendered = "\n".join(evidence.render(row) for row in rows)
    kim = next(row for row in rows if "Kim Lee" in row.fact and row.verb == "talks")
    assert kim.hints == ("people",) and kim.whose == "own" and kim.extra["closeness"] and kim.pointer.startswith("imessage:person/")
    assert "2 of the last 90 days one to one, 1 in small groups" in kim.fact
    bistro = next(row for row in rows if "example-bistro" in row.fact)
    assert (bistro.verb, bistro.whose, bistro.date.isoformat()) == ("shared", "own", (NOW - timedelta(days=3)).date().isoformat())
    barn = next(row for row in rows if "Boulder+Barn" in row.fact)
    assert barn.whose == "other:Climbing crew" and barn.hints == ("places",)
    assert any(row.fact.startswith("1 links from youtube.com") for row in rows)
    assert "5550100199" not in rendered and "sam@example.com" not in rendered.lower()
    assert "dinner" not in rendered and "running late" not in rendered and "token=abc" not in rendered
    assert all(evidence.parse(line) for line in rendered.splitlines())


def test_extract_to_home_or_json_lines_and_import_is_idempotent(messages, tmp_path, capsys, monkeypatch):
    import io
    db, book = messages
    monkeypatch.setattr(imessage, "default_books", lambda: [book])
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGENT_HOME", raising=False)
    cli.main(["source", "imessage", "extract", "--input", str(db), "--jsonl", "--vault", str(tmp_path / "none")])
    lines = capsys.readouterr().out.splitlines()
    assert lines and all(set(json.loads(line)) == {"date", "verb", "fact", "whose", "hints", "pointer", "extra"} for line in lines)
    home = tmp_path / "agent"
    monkeypatch.setenv("AGENT_HOME", str(home))
    for expected in (len(lines), 0):   # the same batch twice adds nothing the second time
        monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(lines) + "\n"))
        cli.main(["evidence", "import", "--source", "imessage", "--vault", str(tmp_path / "none")])
        assert capsys.readouterr().out.strip() == f"imessage: {len(lines)} rows, {expected} new"
    assert len(evidence.read("imessage")) == len(lines)
    bad = json.dumps({"date": "2026-11-01", "verb": "talks", "fact": "x", "whose": "theirs", "hints": ["people"], "pointer": "p"})
    monkeypatch.setattr("sys.stdin", io.StringIO(lines[0] + "\n" + bad + "\n"))
    with pytest.raises(SystemExit, match="line 2: evidence whose must be own or other:<who>; nothing imported"):
        cli.main(["evidence", "import", "--source", "imessage", "--vault", str(tmp_path / "none")])
    for row, error in (({"date": "2026-11-01"}, "missing verb"), ({**json.loads(lines[0]), "text": "hi"}, "unknown field text"),
                       ({**json.loads(lines[0]), "hints": []}, "non-empty list")):
        with pytest.raises(ValueError, match=error):
            evidence.from_json(row)
    from brain import sources
    assert sources.registry()["imessage"].source.runs == "owner-mac"
    home = tmp_path / "extracted"
    monkeypatch.delenv("AGENT_HOME")
    cli.main(["source", "imessage", "extract", "--input", str(db), "--home", str(home), "--vault", str(tmp_path / "none")])
    first = capsys.readouterr().out
    assert first.startswith("imessage: ") and "new" in first
    cli.main(["source", "imessage", "extract", "--input", str(db), "--home", str(home), "--vault", str(tmp_path / "none")])
    assert ", 0 new" in capsys.readouterr().out   # a second run the same day adds nothing
    monkeypatch.setenv("AGENT_HOME", str(home))
    assert any("Kim Lee" in row.fact for row in evidence.read("imessage", "people"))
    cli.main(["source", "list", "--vault", str(tmp_path / "none")])
    listed = capsys.readouterr().out
    assert "imessage · " in listed and "rows, newest 2026-" in listed and "owner-mac, sensitive" in listed
    with pytest.raises(SystemExit, match="no Messages database"):
        cli.main(["source", "imessage", "extract", "--input", str(tmp_path / "missing.db"), "--home", str(home),
                  "--vault", str(tmp_path / "none")])
    with pytest.raises(SystemExit, match="no source 'fax'"):
        cli.main(["source", "fax", "extract", "--vault", str(tmp_path / "none")])


def test_the_script_runs_on_its_own_on_the_owners_mac_and_its_rows_import(messages, tmp_path, capsys, monkeypatch):
    import io
    import subprocess
    import sys
    db, book = messages
    cli.main(["source", "imessage", "script", "--vault", str(tmp_path / "none")])
    script = tmp_path / "carried" / "imessage.py"
    script.parent.mkdir()
    script.write_text(capsys.readouterr().out)
    run = subprocess.run([sys.executable, "-I", str(script), "--db", str(db)], capture_output=True, text=True,
                         env={"HOME": str(tmp_path)}, cwd=script.parent)   # isolated: nothing of the package is importable
    assert run.returncode == 0, run.stderr
    lines = run.stdout.splitlines()
    assert lines and all(evidence.from_json(json.loads(line)) for line in lines)
    assert "dinner" not in run.stdout and "token=abc" not in run.stdout and "5550100199" not in run.stdout
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "agent"))
    monkeypatch.setattr("sys.stdin", io.StringIO(run.stdout))
    cli.main(["evidence", "import", "--source", "imessage", "--vault", str(tmp_path / "none")])
    assert capsys.readouterr().out.strip() == f"imessage: {len(lines)} rows, {len(lines)} new"
    missing = subprocess.run([sys.executable, "-I", str(script), "--db", str(tmp_path / "nope.db")], capture_output=True, text=True)
    assert missing.returncode == 2 and "no Messages database" in missing.stderr
    with pytest.raises(SystemExit, match="has no script to carry"):
        cli.main(["source", "mail", "script", "--vault", str(tmp_path / "none")])


def test_an_organisations_brain_refuses_a_persons_source(messages, vault, tmp_path, capsys, monkeypatch):
    import io
    db, book = messages
    monkeypatch.setattr(imessage, "default_books", lambda: [book])
    rows = [json.dumps(evidence.to_json(row)) for row in imessage.rows(imessage.export(db, [book], NOW), NOW.date())]
    path = vault.parent / "brain.toml"
    path.write_text(path.read_text() + 'edition = "enterprise"\n')
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "agent"))
    monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(rows) + "\n"))
    with pytest.raises(SystemExit, match="imessage is read for personal brains, and this one is enterprise; nothing imported"):
        cli.main(["evidence", "import", "--source", "imessage", "--vault", str(vault)])
    assert evidence.read() == []
