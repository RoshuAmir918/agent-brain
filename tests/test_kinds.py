from brain import fm, nightly, validate
from brain.vault import Vault


def test_kinds_reports_only_undeclared_frontmatter_kinds(vault):
    fm.write(vault / "Plan.md", {"kind": "hub", "tier": "hot"}, "# Plan\n")
    fm.write(vault / "Draft.md", {"kind": "draft", "tier": "cold"}, "# Draft\n")
    fm.write(vault / "Evidence.md", {"kind": "evidence", "tier": "cold"}, "# Evidence\n")
    fm.write(vault / "Plain.md", {"tier": "warm"}, "# Plain\n")
    assert validate.kinds(Vault(vault)) == ["Evidence.md: undeclared kind 'evidence'"]


def test_undeclared_kind_never_gates_the_nightly_but_brain_validate_reports_it(vault, capsys):
    fm.write(vault / "Evidence.md", {"kind": "evidence", "tier": "cold"}, "# Evidence\n")
    v = Vault(vault)
    assert not any("undeclared kind" in problem for problem in validate.run(v, set(v.notes)))
    try:
        nightly.main(["validate", "--vault", str(vault)])
    except SystemExit:
        pass
    assert "Evidence.md: undeclared kind 'evidence'" in capsys.readouterr().out
