from datetime import date

import pytest

from brain import access, fm
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
    note(friends_vault, 'people/Access/2026-11.md', request(BOB, f'Asking about [[{ALICE}]]')
         + '- malformed line\n')
    candidates = access.friends(Vault(friends_vault), TODAY)['candidates']
    assert [(item['profile'], item['access']) for item in candidates[:2]] == [(ALICE, 2), (BOB, 2)]


def test_only_the_close_friends_group_matched_case_insensitively_excludes_its_members(friends_vault):
    group = friends_vault / 'people/Groups/cLoSe FrIeNdS.md'
    note(friends_vault, 'people/Groups/cLoSe FrIeNdS.md', f'# Close friends\n[[{BOB}]]\n## Members\n[[{ALICE}]]\n', kind='group')
    data = access.friends(Vault(friends_vault), TODAY)
    assert data['exists'] is True and [item['profile'] for item in data['candidates']] == [BOB, 'people/Cara/Profile.md']
    group.unlink()
    note(friends_vault, 'people/Groups/Close friends.md', '# Close friends\n## Members\n', kind='group')
    note(friends_vault, 'people/Groups/Work.md', f'# Work\n## Members\n[[{ALICE}]]\n', kind='group')
    data = access.friends(Vault(friends_vault), TODAY)
    assert data['exists'] and data['candidates'][0]['profile'] == ALICE
