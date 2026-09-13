

def test_cli_version_matches_pyproject():
    # 0.2.0 shipped to PyPI with `drift --version` still printing 0.1.1 because the string
    # was hard-coded. The version now comes from package metadata; this pins the two together.
    import re
    from pathlib import Path

    from click.testing import CliRunner

    from claude_drift.cli import main

    declared = re.search(r'^version = "([^"]+)"', Path("pyproject.toml").read_text(), re.M).group(1)
    out = CliRunner().invoke(main, ["--version"]).output
    assert declared in out, out
