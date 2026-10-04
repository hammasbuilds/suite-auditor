# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - unreleased

First release.

### Added

- `suite-auditor coverage <repo>` - one suite run; lists the functions no passing test
  reaches, by name rather than as a percentage.
- `suite-auditor audit <repo>` - mutation-tests the suite on a scratch copy of the
  repository and reports a survivor as a gap only when a separating call is found. The
  witness says whether the call is one the tests really made, a recombination of their
  values, or generated.
- `--python` to run the target's suite with the target's interpreter; otherwise the
  project's own `.venv`/`venv`/`env`, then an activated venv (with a warning when it
  lives outside the project), then the tool's own.
- `-j/--jobs` for parallel mutant runs, each in its own scratch copy.
- Per-function progress with a running mutant count and ETA; a live status line on a
  terminal.
- `--version`; documented exit codes (0 ok, 1 gaps with `--fail-on-gap`, 2 could not
  audit).
- `demo.py`, which audits the bundled `examples/pricing` project - no external checkout.

### Fixed before release

- A `--max-seconds` audit cut off part-way said "stopped after 3 of 3 covered functions"
  when 3 of 17 mutants had been scored. It now gives mutants scored out of planned.
- The audit wrote mutants onto the user's files and restored them with universal
  newlines, turning CRLF files into LF. The repository is now never written.
- Inputs were scraped from test source text, so `@pytest.mark.parametrize` arguments were
  passed to functions as if they were calls - false gaps on a correct suite. Inputs now
  come from the arguments recorded while the covering tests ran.
- "Both versions raise, differently" was counted as a proven gap. It no longer is, and a
  generated input only counts when the original accepts it.
- Both versions of a function were handed the same argument objects, so one that mutates
  its input made the second version see a changed input.
- Modules whose names merely contained "test" (`latest.py`, `contest.py`) were skipped.
- Methods produced no mutants at all (their indented source did not parse).
- Tests that already fail were used as covering tests and "killed" every mutant.
- A missing pytest, a nonexistent path and "nothing scored" now give a clear message and
  exit status 2 instead of a traceback or exit 0.
- No `__pycache__`, plugin or other file is left in the target by either command.

[0.1.0]: https://github.com/hammasbuilds/suite-auditor/releases/tag/v0.1.0
