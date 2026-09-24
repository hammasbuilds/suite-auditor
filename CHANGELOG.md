# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-09-24

First release.

### Added

- `suite-auditor coverage <repo>` — reports test coverage as **a list of function names
  no test reaches**, rather than a percentage. A percentage averages the gaps away; a
  list of names is something to act on.
- `suite-auditor audit <repo>` — the fuller report.
- `demo.py` — audits `toolz`, a real library vendored under `targets/`, in one command.
  152 of its 157 functions are reached and the 5 that are not are named.

[0.1.0]: https://github.com/hammasbuilds/suite-auditor/releases/tag/v0.1.0
