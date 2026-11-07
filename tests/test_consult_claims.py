"""Company mail supplies pointers and derived routing claims without copying messages."""
from datetime import date, timedelta
import json

import pytest

from brain import channel_files, channels, cli, consults, fm, identity, routing, seed, session_context, tasks
from brain.config import Config
from brain.vault import Vault
from conftest import snapshot

TODAY = date(2026, 11, 6)   # the synthetic mail window's last day
SLACK = "slack:U01KIM"   # a member's canonical routing id
EMAIL = "email:kim@example.com"   # the same member's mail evidence id
CLAIM = "Kim approves release schedules"   # a derived statement, never a message excerpt
LINK = "mail/2026-11-06/m1.md"   # a synthetic source pointer


@pytest.fixture
def enterprise(vault, monkeypatch):
    path = vault.parent / "brain.toml"
    path.write_text(path.read_text() + 'edition = "enterprise"\n')
    monkeypatch.setenv("BRAIN_TODAY", TODAY.isoformat())
    return Config.load(vault)


@pytest.fixture
def synced(enterprise, tmp_path, monkeypatch):
    home = tmp_path / "agent-home"
    monkeypatch.setenv("AGENT_HOME", str(home))
    rows = [
        ("Company", "owner", "Company <company@example.com>", "live"),
        ("Operations", "owner", "ops@example.com", "live"),
        ("Missing", "owner", "missing@example.com", "live"),
        ("Agent", "own", "agent@example.com", "live"),
        ("Person", "shared", "lee@example.com", "live"),
        ("Pending", "owner", "pending@example.com", "pending"),
        ("Revoked", "owner", "revoked@example.com", "revoked"),
    ]
    table = channels.HEADER + "\n" + "\n".join(
        channels.Channel(name, whose, address, "", "", "", "", status).render()
        for name, whose, address, status in rows)
    fm.write(enterprise.vault_path / "agent/Channels.md", {"kind": "orientation"}, table)
    return enterprise, home / "channels"


def message(root, ident="m1", account="company@example.com", body="per Kim: BODY_PRIVATE_MARKER", **headers):
    path = root / account / "mail" / str(TODAY) / f"{ident}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"date": "Fri, 6 Nov 2026 14:00:00 -0800", "from": "Ana <ANA@example.com>",
            "to": "recipient@example.com", "cc": "copy@example.com", "subject": "SUBJECT_PRIVATE_MARKER",
            "labels": ["INBOX"]}
    fm.write(path, data | headers, body)
    return path


def claim_line(person=SLACK, area="releases", claim=CLAIM, link=LINK, day=str(TODAY)):
    return f"- {day} · {person} · {area} · {claim} · {link}"


def test_reading_lists_only_synced_live_owner_accounts_and_is_read_only(synced):
    config, root = synced
    for account in ("company@example.com", "ops@example.com", "agent@example.com", "lee@example.com",
                    "pending@example.com", "revoked@example.com"):
        message(root, account=account)
    before = snapshot(config.repo)
    data = seed.deference_reading(Vault(config.vault_path), TODAY)
    assert data["accounts"] == ["company@example.com", "ops@example.com"]
    assert {item["account"] for item in data["messages"]} == set(data["accounts"])
    assert data["problems"] == []
    assert all(set(item) == {"path", "date", "from", "account", "cues"} for item in data["messages"])
    assert all(item["from"] == "ana@example.com" for item in data["messages"])
    scoped = seed.deference_reading(Vault(config.vault_path), TODAY, "OPS@EXAMPLE.COM")
    assert scoped["accounts"] == ["ops@example.com"] and len(scoped["messages"]) == 1
    assert snapshot(config.repo) == before


@pytest.mark.parametrize("body,cue", [
    ("Proceed per Kim", "per"), ("Lee signed off", "signed-off"), ("Get sign-off from Ana", "signed-off"),
    ("Please check with Kim", "check-with"), ("We checked with Lee", "check-with"),
    ("Please ask Ana", "ask"), ("It was approved by Kim", "approved-by"),
    ("Kim decides", "decides"), ("Lee owns", "decides"), ("Ana approves", "decides"),
    ("Kim signs off", "decides"),
])
def test_body_cues_are_named_without_returning_matches(synced, body, cue):
    config, root = synced
    message(root, body=body + ": BODY_PRIVATE_MARKER")
    item, = seed.deference_reading(Vault(config.vault_path), TODAY)["messages"]
    assert item["cues"] == [cue]
    assert body not in json.dumps(item)


@pytest.mark.parametrize("body", ["per kim", "check with kim", "checked with lee", "ask ana",
                                      "approved by kim", "kim decides", "ordinary mail"])
def test_lowercase_names_and_header_cues_do_not_select_mail(synced, body):
    config, root = synced
    message(root, body=body, subject="per Kim; signed off; check with Kim; ask Ana; approved by Lee; Ana decides",
            **{"from": "Kim decides <ana@example.com>", "to": "ask Ana <recipient@example.com>"})
    assert seed.deference_reading(Vault(config.vault_path), TODAY)["messages"] == []


def test_reading_window_is_inclusive_local_and_newest_first(synced):
    config, root = synced
    floor = TODAY - timedelta(days=seed.DAYS)
    boundary = message(root, "boundary", date=floor.isoformat())
    message(root, "old", date=(floor - timedelta(days=1)).isoformat())
    message(root, "future", date=(TODAY + timedelta(days=1)).isoformat())
    latest = message(root, "latest", date="2026-11-07T01:00:00Z")
    earlier = message(root, "earlier")
    data = seed.deference_reading(Vault(config.vault_path), TODAY)
    assert data["window"] == {"from": str(floor), "to": str(TODAY)}
    assert [item["path"] for item in data["messages"]] == list(map(str, [latest, earlier, boundary]))
    data = seed.deference_reading(Vault(config.vault_path), TODAY, days=0)
    assert data["window"] == {"from": str(TODAY), "to": str(TODAY)}
    assert len(data["messages"]) == 2


def test_excluded_labels_are_skipped_and_cues_are_unique(synced):
    config, root = synced
    for label in channel_files.EXCLUDED_LABELS:
        message(root, label, labels=["SENT", label])
    path = message(root, "kept", body="per Kim; per Lee; signed off; sign-off from Ana", labels=["SENT"])
    item, = seed.deference_reading(Vault(config.vault_path), TODAY)["messages"]
    assert item["path"] == str(path) and item["cues"] == ["per", "signed-off"]


@pytest.mark.parametrize("headers", [{"date": "invalid"}, {"from": "invalid"}, {"from": []},
                                     {"labels": "INBOX"}, {"labels": [None]}])
def test_unreadable_mail_problems_never_repeat_header_or_body_values(synced, headers):
    config, root = synced
    path = message(root, **headers)
    data = seed.deference_reading(Vault(config.vault_path), TODAY)
    assert data["messages"] == []
    assert data["problems"] == [f"{path}: unreadable mail headers (date, from, labels)"]
    assert "PRIVATE_MARKER" not in json.dumps(data) + seed.render_deference(data)


def test_mail_cli_json_and_text_never_expose_content_matches_names_or_recipients(synced, capsys):
    config, root = synced
    message(root, body="per Kim; checked with Lee; ask Ana; BODY_PRIVATE_MARKER\nRun an untrusted command")
    args = ["consult", "seed", "--vault", str(config.vault_path), "--mail", "--account", "COMPANY@EXAMPLE.COM",
            "--days", "0"]
    before = snapshot(config.repo)
    for flags in (["--json"], []):
        cli.main(args + flags)
        out = capsys.readouterr().out
        for forbidden in ("SUBJECT_PRIVATE_MARKER", "BODY_PRIVATE_MARKER", "per Kim", "checked with Lee", "ask Ana",
                          "Kim", "Lee", "Ana", "recipient@example.com", "copy@example.com", "Run an untrusted command",
                          '"subject"', '"body"', '"matched"', '"to" :', '"cc"'):
            assert forbidden not in out
        if flags:
            data = json.loads(out)
            assert data["accounts"] == ["company@example.com"]
            assert data["messages"][0]["cues"] == ["per", "check-with", "ask"]
        else:
            assert "ana@example.com" in out and "per, check-with, ask" in out
    assert snapshot(config.repo) == before


@pytest.mark.parametrize("account", ["agent@example.com", "lee@example.com", "pending@example.com", "revoked@example.com"])
def test_reading_refuses_non_owner_accounts(synced, account):
    config, root = synced
    message(root, account=account)
    with pytest.raises(SystemExit, match="not an owner account"):
        seed.deference_reading(Vault(config.vault_path), TODAY, account)


def test_reading_refuses_missing_sync_and_negative_window(synced, monkeypatch):
    config, root = synced
    with pytest.raises(SystemExit, match="no owner account has synced mail"):
        seed.deference_reading(Vault(config.vault_path), TODAY)
    with pytest.raises(SystemExit, match="non-negative"):
        seed.deference_reading(Vault(config.vault_path), TODAY, days=-1)
    message(root)
    monkeypatch.delenv("AGENT_HOME")
    with pytest.raises(SystemExit, match="no owner account has synced mail"):
        seed.deference_reading(Vault(config.vault_path), TODAY)


def test_personal_edition_refuses_mail_and_claims_without_writing(vault, tmp_path):
    path = tmp_path / "deference.md"
    path.write_text(claim_line())
    before = snapshot(vault)
    with pytest.raises(SystemExit, match="enterprise edition"):
        seed.deference_reading(Vault(vault), TODAY)
    for mode in (["--mail"], ["--claims", str(path)]):
        with pytest.raises(SystemExit, match="enterprise edition"):
            cli.main(["consult", "seed", "--vault", str(vault)] + mode)
    assert snapshot(vault) == before


def test_claims_parse_valid_lines_normalize_email_and_ignore_headings_and_frontmatter():
    text = "---\nsource: mail\nignored: private marker\n---\n\n# Deference\n\n"
    text += claim_line() + "\n\n## Another area\n"
    text += claim_line(person="KIM@EXAMPLE.COM", area="Release Plans", link="https://example.com/message/1")
    text += "\n" + claim_line(person="email:LEE@EXAMPLE.COM", area="support", claim="Lee owns escalation decisions")
    rows, problems = seed.deference_claims(text)
    assert problems == []
    assert rows == [seed.Row("releases", str(TODAY), SLACK, "deferred-to", CLAIM, LINK),
                    seed.Row("release plans", str(TODAY), EMAIL, "deferred-to", CLAIM, "https://example.com/message/1"),
                    seed.Row("support", str(TODAY), "email:lee@example.com", "deferred-to",
                             "Lee owns escalation decisions", LINK)]
    sixteen = "Kim decides " + " ".join(["scope"] * 14)
    assert len(seed.deference_claims(claim_line(claim=sixteen))[0]) == 1


def test_frontmatter_without_a_final_newline_is_ignored():
    assert seed.deference_claims("---\nsource: mail\n---") == ([], [])


def test_bare_email_normalizes_and_joins_candidate_aliases(enterprise):
    address = "Kim.Lee@Example.com"
    rows, problems = seed.deference_claims(claim_line(person=address))
    assert problems == [] and rows[0].person == "email:" + address.lower()
    profile = enterprise.vault_path / enterprise.people_dir / "Kim" / "Profile.md"
    profile.parent.mkdir(parents=True)
    fm.write(profile, {}, f"- **Slack:** U01KIM\n- **Email:** {address}\n")
    area = routing.area_path(enterprise, "releases")
    area.parent.mkdir(parents=True)
    fm.write(area, {}, f"## Evidence\n- {TODAY} · {rows[0].person} · deferred-to · {CLAIM} · [[{LINK}]]\n")
    candidate, = consults.candidates(enterprise, "release schedules", "releases")
    assert candidate.person == SLACK and candidate.evidence[0]["via"] == rows[0].person


@pytest.mark.parametrize("address", ["kim+releases@example.com", "lee.o'neil@example.com"])
def test_addresses_an_id_cannot_hold_are_refused_plainly(address):
    rows, problems = seed.deference_claims(claim_line(person=address))
    assert rows == [] and problems == ["line 1: an email address may use only letters, digits, dots, underscores and hyphens before @"]
    assert not tasks.PERSON.fullmatch("email:" + address)


@pytest.mark.parametrize("quote", ['"', "“", "”", "‘", "’", "`"])
def test_claims_refuse_each_quotation_mark_without_echoing_the_claim(quote):
    rows, problems = seed.deference_claims(claim_line(claim=f"PRIVATE_MARKER {quote}Kim approves"))
    assert rows == [] and problems == ["line 1: claim must omit quotation marks and backticks"]
    assert "PRIVATE_MARKER" not in str(problems)


@pytest.mark.parametrize("changes,reason", [
    ({"claim": "PRIVATE_MARKER " + "word " * 16}, "at most 16 words"),
    ({"claim": "PRIVATE_MARKER kim@example.com approves"}, "email addresses"),
    ({"claim": "PRIVATE_MARKER Kim approves 12345"}, "five or more digits"),
    ({"claim": "PRIVATE_MARKER https://example.com/decision"}, "URLs"),
    ({"claim": "PRIVATE_MARKER www.example.com"}, "URLs"),
    ({"claim": "PRIVATE_MARKER example.com/decision"}, "URLs"),
    ({"claim": "PRIVATE_MARKER ftp://example.com/decision"}, "URLs"),
    ({"claim": "PRIVATE_MARKER · extra"}, "field separator"),
    ({"claim": "PRIVATE_MARKER\vcontinuation"}, "single-line"),
    ({"claim": ""}, "claim is required"),
    ({"person": "PRIVATE_MARKER"}, "person must"),
    ({"area": "../PRIVATE_MARKER"}, "area must"),
    ({"area": "PRIVATE_MARKER\varea"}, "single-line"),
    ({"day": "PRIVATE_MARKER"}, "date must be YYYY-MM-DD"),
    ({"day": "2026-99-01"}, "valid YYYY-MM-DD"),
    ({"link": ""}, "link is required"),
    ({"link": "[[PRIVATE_MARKER]]"}, "brackets"),
    ({"link": "PRIVATE_MARKER\vcontinuation"}, "single-line"),
])
def test_each_refusal_reports_its_line_and_reason_without_echoing_fields(changes, reason):
    rows, problems = seed.deference_claims("# Claims\n\n" + claim_line(**changes))
    assert rows == [] and len(problems) == 1
    assert problems[0].startswith("line 3: ") and reason in problems[0]
    assert "PRIVATE_MARKER" not in str(problems) and CLAIM not in str(problems)


def test_missing_link_and_malformed_lines_are_problems_and_valid_rows_survive():
    rows, problems = seed.deference_claims(claim_line().rsplit(" · ", 1)[0] + "\nPRIVATE_MARKER\n- malformed\n"
                                           + claim_line())
    assert rows == [seed.Row("releases", str(TODAY), SLACK, "deferred-to", CLAIM, LINK)]
    assert problems == ["line 1: link is required", "line 2: expected a dated claim bullet",
                        "line 3: expected date, person, area, claim and link"]


@pytest.fixture
def claims_file(tmp_path):
    path = tmp_path / "deference.md"
    path.write_text("# Derived claims\n" + claim_line(person="KIM@EXAMPLE.COM") + "\n"
                    + claim_line(person="slack:U02LEE", area="support", claim="Lee owns escalation decisions")
                    + "\n" + claim_line(claim='PRIVATE_MARKER "quoted"'))
    return path


def test_claims_cli_dry_run_writes_nothing_and_reports_counts_and_safe_problems(enterprise, claims_file, capsys):
    args = ["consult", "seed", "--vault", str(enterprise.vault_path), "--claims", str(claims_file), "--dry-run"]
    before = snapshot(enterprise.repo)
    cli.main(args + ["--json"])
    data = json.loads(capsys.readouterr().out)
    assert data == {"found": 2, "added": 0, "areas": {"releases": {"deferred-to": 1}, "support": {"deferred-to": 1}},
                    "problems": ["line 4: claim must omit quotation marks and backticks"]}
    cli.main(args)
    out = capsys.readouterr().out
    assert "2 lines found, 0 lines added" in out and "releases · deferred-to 1" in out
    assert "problem: line 4:" in out and "PRIVATE_MARKER" not in out
    assert snapshot(enterprise.repo) == before


def test_claims_cli_records_deferred_to_evidence_and_is_idempotent(enterprise, claims_file, capsys):
    args = ["consult", "seed", "--vault", str(enterprise.vault_path), "--claims", str(claims_file), "--json"]
    cli.main(args)
    data = json.loads(capsys.readouterr().out)
    assert (data["found"], data["added"]) == (2, 2)
    path = routing.area_path(enterprise, "releases")
    assert f"- {TODAY} · {EMAIL} · deferred-to · {CLAIM} · [[{LINK}]]" in path.read_text()
    assert "PRIVATE_MARKER" not in "".join(p.read_text() for p in path.parent.glob("*.md"))
    before = snapshot(enterprise.repo)
    cli.main(args)
    assert json.loads(capsys.readouterr().out) == {**data, "added": 0}
    assert snapshot(enterprise.repo) == before


@pytest.mark.parametrize("flags", [[], ["--mail", "--reviews", "."], ["--mail", "--claims", "claims.md"],
                                  ["--reviews", ".", "--claims", "claims.md"],
                                  ["--mail", "--reviews", ".", "--claims", "claims.md"]])
def test_cli_requires_exactly_one_source(enterprise, flags, capsys):
    with pytest.raises(SystemExit) as error:
        cli.main(["consult", "seed", "--vault", str(enterprise.vault_path)] + flags)
    assert error.value.code == 2
    assert "--reviews" in capsys.readouterr().err


@pytest.mark.parametrize("flags,reason", [
    (["--claims", "absent.md"], "unreadable claims file"),
    (["--claims", "absent.md", "--account", "company@example.com"], "--account goes with --mail"),
    (["--claims", "absent.md", "--days", "1"], "--days goes with --reviews or --mail"),
    (["--mail", "--dry-run"], "--dry-run goes with --reviews or --claims"),
    (["--mail", "--days", "-1"], "non-negative"),
])
def test_cli_refuses_inapplicable_options_and_unreadable_claims(enterprise, flags, reason):
    with pytest.raises(SystemExit, match=reason):
        cli.main(["consult", "seed", "--vault", str(enterprise.vault_path)] + flags)


def test_email_profile_ids_join_slack_consults_and_retain_via(enterprise):
    path = enterprise.vault_path / enterprise.people_dir / "Kim" / "Profile.md"
    path.parent.mkdir(parents=True)
    fm.write(path, {}, "# Kim\n- **Slack:** U01KIM\n- **Email:** `KIM@EXAMPLE.COM`\n"
             "Kim mentions Email: stranger@example.com.\nEmail: invalid\n")
    retired = path.parent.parent / "Lee" / "Profile.md"
    retired.parent.mkdir()
    fm.write(retired, {"kind": "log"}, "Email: lee@example.com\n")
    area = routing.area_path(enterprise, "releases")
    area.parent.mkdir(parents=True)
    fm.write(area, {}, f"## Evidence\n- {TODAY} · {EMAIL} · deferred-to · {CLAIM} · [[{LINK}]]\n"
             f"- {TODAY} · {SLACK} · reviewed · Release plan\n")
    before = snapshot(enterprise.repo)
    assert routing.identities(enterprise) == {SLACK: SLACK, EMAIL: SLACK}
    candidate, = consults.candidates(enterprise, "release schedules", "releases")
    assert candidate.person == SLACK
    assert candidate.score == routing.WEIGHTS["deferred-to"] + routing.WEIGHTS["reviewed"]
    evidence = next(item for item in candidate.evidence if item["kind"] == "deferred-to")
    assert evidence["via"] == EMAIL and evidence["link"] == LINK
    assert "via" not in next(item for item in candidate.evidence if item["kind"] == "reviewed")
    assert identity.profile_ids("- **Email:** kim@example.com\n") == []
    assert snapshot(enterprise.repo) == before


@pytest.mark.parametrize("line", ["Email: KIM@EXAMPLE.COM", "- email: kim@example.com",
                                 "* **EMAIL:** `kim@example.com`", "+ **Email**: kim@example.com"])
def test_email_identity_uses_the_qualified_line_grammar(enterprise, line):
    path = enterprise.vault_path / enterprise.people_dir / "Kim" / "Profile.md"
    path.parent.mkdir(parents=True)
    fm.write(path, {}, line + "\n")
    assert routing.identities(enterprise) == {EMAIL: EMAIL}


def test_reading_includes_the_backfill_inside_the_window(synced):
    config, root = synced
    recent = message(root, "recent")
    old_day = TODAY - timedelta(days=200)
    old = root / "company@example.com" / "backfill" / "mail" / str(old_day) / "old.md"
    old.parent.mkdir(parents=True)
    fm.write(old, {"date": old_day.isoformat(), "from": "ana@example.com", "labels": ["INBOX"]}, "Kim decides releases.")
    assert [item["path"] for item in seed.deference_reading(Vault(config.vault_path), TODAY)["messages"]] == [str(recent)]
    assert [item["path"] for item in seed.deference_reading(Vault(config.vault_path), TODAY, days=365)["messages"]] == [str(recent), str(old)]
