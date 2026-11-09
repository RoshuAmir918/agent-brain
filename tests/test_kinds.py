from brain import cli, fm, validate
from brain.vault import Vault


def test_only_undeclared_kinds_are_reported_by_validate_never_by_the_nightly(vault, capsys):
    fm.write(vault / "Plan.md", {"kind": "hub", "tier": "hot"}, "# Plan\n")
    fm.write(vault / "Draft.md", {"kind": "draft", "tier": "cold"}, "# Draft\n")
    fm.write(vault / "Thought.md", {"kind": "reflection"}, "# Thought\n")
    fm.write(vault / "Evidence.md", {"kind": "evidence", "tier": "cold"}, "# Evidence\n")
    fm.write(vault / "Plain.md", {"tier": "warm"}, "# Plain\n")
    v = Vault(vault)
    assert [v.kind_for(rel) for rel in ("Draft.md", "Thought.md", "Plan.md")] == ["draft", "reflection", "hub"]
    assert validate.kinds(v) == ["Evidence.md: undeclared kind 'evidence'"]
    assert not any("undeclared kind" in problem for problem in validate.run(v, set(v.notes)))
    try:
        cli.main(["validate", "--vault", str(vault)])
    except SystemExit:
        pass
    assert "Evidence.md: undeclared kind 'evidence'" in capsys.readouterr().out
