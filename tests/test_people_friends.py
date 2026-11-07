import json
from datetime import date

import pytest

from brain import access, cli, fm
from brain.vault import Vault
from conftest import snapshot

TODAY = date(2026, 11, 4)
ALICE = 'people/Alice_Example/Profile.md'
BOB = 'people/Bob/Profile.md'


def note(vault, rel, body, **data):
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    fm.write(path, {'tier': 'warm'} | data, body)


def request(person, asked='Free Saturday?'):
    return f'- 2026-01-01 12:00 · [[{person}]] · schedule · told gist · default · {asked}\n'


@pytest.fixture
def friends_vault(vault, monkeypatch):
    monkeypatch.setenv('BRAIN_TODAY', TODAY.isoformat())
    config = vault.parent / 'brain.toml'
    config.write_text(config.read_text() + 'human_profile = "people/Owner/Profile.md"\n')
    note(vault, 'people/Owner/Profile.md', '# Owner\nDemiurge: owner\n')
    note(vault, ALICE, '# Alicia\n- **Demiurge:** `alice`\n')
    note(vault, BOB, '# Bob\nSlack: U_BOB\n')
    note(vault, 'people/Cara/Profile.md', '# Cara\nEmail: cara@example.com\n')
    note(vault, 'people/No_id/Profile.md', '# No id\nI mentioned Demiurge: fake in prose.\n')
    note(vault, 'people/Unconfirmed/Profile.md', '# Unconfirmed\nIdentity unconfirmed\nDemiurge: nope\n')
    note(vault, 'people/Unresolved/Profile.md', '# Unresolved someone\nSlack: nope\n')
    note(vault, 'people/Access/2025-01.md', request('memory/' + ALICE) * 2)
    note(vault, 'people/Access/2026-10.md', request(BOB) + request('people/Owner/Profile.md') * 10
         + request('people/Unconfirmed/Profile.md') * 10 + request('people/Unresolved/Profile.md') * 10
         + request('people/No_id/Profile.md') * 10)
    note(vault, 'agents/Ally/Profile.md', f'# Ally\nOwner: [[memory/{ALICE.removesuffix(".md")}|Alicia]]\nDemiurge: ally\n')
    note(vault, 'agents/Buddy/Profile.md', '# Buddy\n- **Owner:** Bob\nSlack: buddy\n')
    note(vault, 'agents/Unknown/Profile.md', '# Unknown\nOwner: Not a profile\nDemiurge: unknown\n')
    note(vault, 'Conversations/2026-11-04 Noted.md', '- 12:00 · Alice Example said: Hello.\n- 12:01 · Alicia said: Again.\n'
         '- 12:02 · Bob said: Hello.\n- 12:03 · The owner mentions Bob said: not his line.\n'
         '- 12:04 · An owner line mentioning Alicia.\n')
    note(vault, 'Conversations/2026-09-06 Noted.md', '- 10:00 · Bob said: Boundary.\n')
    note(vault, 'Conversations/2026-09-05 Noted.md', '- 10:00 · Bob said: Too old.\n' * 10)
    note(vault, 'Conversations/2026-11-05 Noted.md', '- 10:00 · Bob said: Future.\n' * 10)
    note(vault, 'Conversations/2026-11-04 Something else.md', '- 10:00 · Bob said: Not Noted.\n' * 10)
    return vault


def test_friends_rank_total_evidence_and_require_confirmed_platform_ids(friends_vault):
    vault = Vault(friends_vault)
    before = snapshot(friends_vault)
    data = access.friends(vault, TODAY)
    assert data == {'exists': False, 'candidates': [
        {'profile': ALICE, 'name': 'Alicia', 'access': 2, 'intellects': 1, 'noted': 2},
        {'profile': BOB, 'name': 'Bob', 'access': 1, 'intellects': 1, 'noted': 2},
        {'profile': 'people/Cara/Profile.md', 'name': 'Cara', 'access': 0, 'intellects': 0, 'noted': 0},
    ]}
    assert snapshot(friends_vault) == before


def test_existing_group_is_case_insensitive_and_excludes_only_members(friends_vault):
    note(friends_vault, 'people/Groups/cLoSe FrIeNdS.md', f'# Close friends\n[[{BOB}]]\n## Members\n[[{ALICE}]]\n', kind='group')
    data = access.friends(Vault(friends_vault), TODAY)
    assert data['exists'] is True and [item['profile'] for item in data['candidates']] == [BOB, 'people/Cara/Profile.md']


def test_empty_group_exists_and_other_groups_do_not_exclude_candidates(friends_vault):
    note(friends_vault, 'people/Groups/Close friends.md', '# Close friends\n## Members\n', kind='group')
    note(friends_vault, 'people/Groups/Work.md', f'# Work\n## Members\n[[{ALICE}]]\n', kind='group')
    data = access.friends(Vault(friends_vault), TODAY)
    assert data['exists'] and data['candidates'][0]['profile'] == ALICE


def test_evidence_counts_only_the_named_profile_not_other_mentions(friends_vault):
    note(friends_vault, 'people/Access/2026-11.md', request(BOB, f'Asking about [[{ALICE}]]')
         + '- malformed line\n')
    data = access.friends(Vault(friends_vault), TODAY)
    assert data['candidates'][0]['profile'] == ALICE
    assert data['candidates'][0]['access'] == 2
    assert data['candidates'][1]['access'] == 2


def test_friends_top_eight_and_ambiguous_intellect_owner(vault):
    for n in range(10):
        note(vault, f'people/Person_{n}/Profile.md', f'# Person {n}\nTelegram: person{n}\n')
    note(vault, 'agents/Their/Profile.md', '# Their\nOwner: Person 9\n')
    data = access.friends(Vault(vault), TODAY)
    assert len(data['candidates']) == 8
    assert data['candidates'][0]['name'] == 'Person 9' and data['candidates'][0]['intellects'] == 1
    assert [item['name'] for item in data['candidates'][1:]] == [f'Person {n}' for n in range(7)]
    note(vault, 'people/Duplicate/Profile.md', '# Person 9\nSignal: duplicate\n')
    assert all(item['intellects'] == 0 for item in access.friends(Vault(vault), TODAY)['candidates'])


def test_friends_cli_text_and_json_are_read_only(friends_vault, capsys):
    args = ['people', 'friends', '--vault', str(friends_vault)]
    before = snapshot(friends_vault.parent)
    cli.main(args + ['--json'])
    data = json.loads(capsys.readouterr().out)
    assert not data['exists'] and data['candidates'][0]['name'] == 'Alicia'
    cli.main(args)
    text = capsys.readouterr().out
    assert 'Close friends group: none' in text
    assert 'Alicia · 2 access requests · 1 intellects · 2 Noted lines' in text
    assert snapshot(friends_vault.parent) == before
