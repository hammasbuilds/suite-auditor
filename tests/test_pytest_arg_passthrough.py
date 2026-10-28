"""Extra pytest arguments reach the target's pytest.

The coverage report says how many tests the target's own `addopts` deselected and
suggests re-running with the marker enabled - advice the tool could not follow, because
there was no way to pass an argument through. flake-detective's addopts read
`-m 'not slow'`, so auditing it listed 16 unreached functions including every one of its
arm builders, which are exercised by exactly the tests that marker removes.

The `=` form matters and is in the help text: argparse reads a bare `-m` after
`--pytest-arg` as an option of ours and refuses it as a value.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from suite_auditor.cli import build_parser  # noqa: E402


def parse(*argv: str):  # noqa: ANN201
    return build_parser().parse_args(list(argv))


class TestTheOption:
    def test_it_defaults_to_nothing(self) -> None:
        assert parse("coverage", ".").pytest_arg == []
        assert parse("audit", ".").pytest_arg == []

    def test_it_is_on_both_commands(self) -> None:
        for command in ("coverage", "audit"):
            args = parse(command, ".", "--pytest-arg=-x")
            assert args.pytest_arg == ["-x"]

    def test_it_repeats_in_order(self) -> None:
        """Order matters: `-m` and its expression are two arguments to pytest."""
        args = parse("coverage", ".", "--pytest-arg=-m", "--pytest-arg=slow or not slow")
        assert args.pytest_arg == ["-m", "slow or not slow"]

    def test_a_value_that_is_not_option_shaped_needs_no_equals(self) -> None:
        assert parse("coverage", ".", "--pytest-arg", "mytests").pytest_arg == ["mytests"]


class TestItReachesPytest:
    def test_the_arguments_are_appended_to_the_command(self, monkeypatch, tmp_path) -> None:
        """Captured at the subprocess boundary, so this tests the command, not the parser.

        A test that only checked the parser would pass while the value was dropped
        between `cmd_coverage` and `build_map` - which is the half that was missing.
        """
        from suite_auditor import coverage as cov

        seen: list[list[str]] = []

        def fake_run(cmd, repo, env, timeout):  # noqa: ANN001, ANN202
            seen.append(list(cmd))
            raise OSError("stopped on purpose, after the command was built")

        monkeypatch.setattr(cov, "run", fake_run)
        (tmp_path / "tests").mkdir()
        cov.build_map(tmp_path, pytest_args=["-m", "slow or not slow"])
        assert seen, "the pytest command was never built"
        cmd = seen[0]
        # The LAST -m: the command is `python -B -m pytest ...`, so an index search for
        # the first one finds the interpreter's own flag. The first version of this test
        # asserted on that and failed against correct code.
        assert cmd.count("-m") == 2, cmd
        last = len(cmd) - 1 - cmd[::-1].index("-m")
        assert cmd[last + 1] == "slow or not slow"

    def test_no_arguments_leaves_the_command_alone(self, monkeypatch, tmp_path) -> None:
        from suite_auditor import coverage as cov

        seen: list[list[str]] = []

        def fake_run(cmd, repo, env, timeout):  # noqa: ANN001, ANN202
            seen.append(list(cmd))
            raise OSError("stopped on purpose")

        monkeypatch.setattr(cov, "run", fake_run)
        cov.build_map(tmp_path)
        # One -m, the interpreter's own, and no marker expression.
        assert seen[0].count("-m") == 1, seen[0]
        assert "slow or not slow" not in seen[0]

    def test_the_target_path_stays_last(self, monkeypatch, tmp_path) -> None:
        """A pytest option must not end up after the path it applies to."""
        from suite_auditor import coverage as cov

        seen: list[list[str]] = []

        def fake_run(cmd, repo, env, timeout):  # noqa: ANN001, ANN202
            seen.append(list(cmd))
            raise OSError("stopped on purpose")

        monkeypatch.setattr(cov, "run", fake_run)
        cov.build_map(tmp_path, test_target="tests/test_one.py", pytest_args=["-x"])
        cmd = seen[0]
        assert cmd[-1] == "tests/test_one.py"
        assert cmd.index("-x") < len(cmd) - 1
