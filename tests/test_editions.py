"""An intellect is one person's (personal) or an organisation's (enterprise): the same engine, installing only the
routines and skill sections its edition is for. A personal vault renders exactly as before."""
from datetime import date


from brain import cli, health, install, schedule, validate
from brain.config import Config, SECTIONS
from brain.vault import Vault


def enterprise(vault, extra=""):
    toml = vault.parent / "brain.toml"
    toml.write_text(f'[brain]\nvault = "{vault.name}"\nedition = "enterprise"\n{extra}')
    return Config.load(vault)


def skill(config, name):
    path = config.skills_path / (config.skill_prefix + name) / "SKILL.md"
    return path.read_text() if path.exists() else None


def test_enterprise_installs_the_work_without_the_owners_day_and_removes_what_it_had(vault):
    personal = Config.load(vault)
    install.skills(personal)
    assert skill(personal, "morning") and "## Goals" in skill(personal, "recall")
    assert "<!--" not in skill(personal, "recall") and "Talking to your colleagues" not in skill(personal, "recall")
    config = enterprise(vault)
    install.skills(config)
    for gone in ("morning", "people", "prime"):
        assert skill(config, gone) is None
    for kept in ("continue", "watch", "evening", "explore", "improve", "sleep1", "recall", "setup"):
        assert skill(config, kept)
    recall = skill(config, "recall")
    assert "## Talking to your colleagues" in recall and "## Goals" not in recall and "Disclosure.md" not in recall
    assert "## Grants" not in skill(config, "setup") and "*People:*" not in skill(config, "explore")
    assert "<!--" not in recall


def test_an_enterprise_context_leaves_out_goals_and_primed_unless_the_vault_chose_them(vault):
    config = enterprise(vault)
    assert "goals" not in config.context.sections and "primed" not in config.context.sections
    chosen = enterprise(vault, '\n[context]\nsections = ["start", "goals", "tasks"]\n')
    assert chosen.context.sections == ("start", "goals", "tasks")
    listed = ", ".join(f'"{section}"' for section in SECTIONS)
    assert enterprise(vault, f'\n[context]\nsections = [{listed}]\n').context.sections == SECTIONS
    (vault.parent / "brain.toml").write_text(f'[brain]\nvault = "{vault.name}"\n')
    assert Config.load(vault).context.sections == SECTIONS


def test_enterprise_health_and_validation_do_not_expect_one_persons_stages_or_profile(vault):
    config = enterprise(vault)
    stages = health.stages(config.repo, date(2026, 11, 6), config.now(), config.edition)
    assert "people" not in stages and "prime" not in stages and "sleep1" in stages
    assert validate.owner_profile(Vault(config.vault_path)) == []


def test_schedule_init_adds_the_editions_routines_and_never_touches_one_already_there(vault, capsys):
    config = enterprise(vault)
    cli.main(["schedule", "set", "watch", "--cron", "0 9 * * 1-5", "--skills", "brain-watch", "--vault", str(vault)])
    capsys.readouterr()
    cli.main(["schedule", "init", "--vault", str(vault)])
    assert "added" in capsys.readouterr().out
    routines = schedule.load(config)
    assert {"evening", "sleep1", "attribution"} <= set(routines)
    assert not {"continue", "continue-daily"} & set(routines)
    assert not {"morning", "people", "prime"} & set(routines)
    assert routines["watch"].cron == "0 9 * * 1-5"
    cli.main(["schedule", "init", "--vault", str(vault)])
    assert "already scheduled" in capsys.readouterr().out
    assert {"morning", "people", "prime"} <= set(schedule.defaults(Config(repo=config.repo)))
    assert not {"continue", "continue-daily"} & set(schedule.defaults(Config(repo=config.repo)))


def test_schedule_init_removes_another_editions_routines_and_rechains_what_followed_them(vault, capsys):
    config = enterprise(vault)
    for args in (("sleep1", "--cron", "30 2 * * *", "--skills", "brain-sleep1"),
                 ("people", "--after", "sleep1", "--skills", "brain-people"),
                 ("image", "--after", "people", "--skills", "brain-image"),
                 ("morning", "--cron", "0 8 * * *", "--skills", "brain-morning"),
                 ("sleep2", "--cron", "0 4 * * *", "--skills", "brain-sleep2")):
        cli.main(["schedule", "set", *args, "--vault", str(vault)])
    capsys.readouterr()
    assert len(schedule.problems(config)) == 3
    cli.main(["schedule", "init", "--vault", str(vault)])
    assert "removed morning, people, sleep2" in capsys.readouterr().out
    routines = schedule.load(config)
    assert not {"morning", "people", "sleep2"} & set(routines)
    assert routines["image"].after == "sleep1"
    assert schedule.problems(config) == []


def test_enterprise_skills_say_who_speaks_for_the_organisation_and_personal_ones_do_not(vault):
    personal = Config.load(vault)
    install.skills(personal)
    config = enterprise(vault, 'human_name = "Harbor"\n')
    install.skills(config)
    for name in ("sleep1", "recall", "continue", "evening", "explore", "watch", "improve"):
        assert "**Who Harbor is.**" in skill(config, name)
        assert "{organisation}" not in skill(config, name)
    install.skills(personal)
    assert all("**Who " not in skill(personal, name) for name in ("sleep1", "recall", "continue"))
