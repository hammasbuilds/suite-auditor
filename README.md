# suite-auditor

[![ci](https://github.com/hammasbuilds/suite-auditor/actions/workflows/ci.yml/badge.svg)](https://github.com/hammasbuilds/suite-auditor/actions/workflows/ci.yml)
![python](https://img.shields.io/badge/python-3.11%2B-blue)
![zero dependencies](https://img.shields.io/badge/runtime%20deps-0-brightgreen)
[![license](https://img.shields.io/badge/license-MIT-green)](https://github.com/hammasbuilds/suite-auditor/blob/main/LICENSE)

We built suite-auditor, a mutation tester for Python projects that reports only what it can
**prove**: a surviving mutant becomes a *gap* once there is a concrete call on which the
mutant and the original return different things, and that call is printed. Survivors it
cannot separate stay "unproven" and never reach the headline. It is for developers and
reviewers who want to know what a passing pytest suite would not notice. It never modifies
your files: the audit runs on a scratch copy.

## What it does

- **Traces once.** A small pytest plugin (written to a temporary directory) runs the suite
  under `sys.settrace` and records which passing tests enter each function and the literal
  arguments they called it with. Already-failing tests are left out.
- **Mutates plausibly, on a copy.** Twelve operators drawn round-robin: comparison flips,
  arithmetic, `and`/`or`, integer constant +1, negated `if`, dropped `not`, `return x` →
  `return None`, swallowed `except`, deleted `assert`, deleted `raise`, slice bounds off by
  one. Each mutant is byte-exact apart from the mutated lines.
- **Runs only the covering tests** (with `-x`), after first checking they pass on the
  unmutated copy; a hang past 10× the clean run counts as caught.
- **Proves every survivor** by calling the original and the mutant side by side: first on
  calls the tests really made, then on recombined test values, then on generated values.
  Both versions raising is never proof; nondeterministic functions (`random`, `uuid`, clocks,
  or a function that disagrees with its own repeat) are refused; methods are mutated but
  never "proven".
- **Grades the evidence** (both return and differ, or one raises) and reports the **caught
  share** (kill rate × covered fraction), which testing less cannot raise. Functions no test
  reaches are listed, not mutated.

```
trace once        mutate one      run ONLY the        suite failed  -> killed
which tests  -->  change at  -->  tests that    -->
reach what        a time          cover it            suite passed  -> PROVE IT: call both
                                                                       versions on the inputs
                                                                       the tests really used
                                                                         differ -> GAP + the call
                                                                         agree  -> unproven
```

## Screenshots

A small browser front end drives the package for demonstrations. It is a local demo and is **not part of
the package** — the package has no web surface and no runtime dependencies. Every result on these screens
is a live call into the library.

![Overview](https://raw.githubusercontent.com/hammasbuilds/suite-auditor/main/docs/screenshots/00-overview.png)
*The front end as it opens: every feature is a tab, and the page states that nothing on it is precomputed.*

![Audit A Suite](https://raw.githubusercontent.com/hammasbuilds/suite-auditor/main/docs/screenshots/01-audit-a-suite.png)
*Mutants killed per function, and each proven gap with the concrete call that proves it.*

![What Tests Reach](https://raw.githubusercontent.com/hammasbuilds/suite-auditor/main/docs/screenshots/02-what-tests-reach.png)
*Which functions the tests actually reach, and the arguments they were reached with.*

![Proving Safely](https://raw.githubusercontent.com/hammasbuilds/suite-auditor/main/docs/screenshots/03-proving-safely.png)
*The proof step attempting to write, delete, spawn and connect — and being refused each time.*

## Sample input / output

`python demo.py` audits
[`examples/pricing`](https://github.com/hammasbuilds/suite-auditor/tree/main/examples/pricing),
a bundled project: `discount` is tested over every branch and both sides of each boundary,
`shipping` at one point either side of its boundary, `with_tax` by
`assert with_tax(10) > 10`, and `format_price` not at all. Real output (progress lines trimmed):

```text
mutating 3 function(s): 17 mutants, 3 in parallel...
  [1/3] pricing/rules.py::with_tax                   3/5 killed, 1 gap(s)   [14/17 mutants, ETA 1s]
  [2/3] pricing/rules.py::discount                   6/6 killed, 0 gap(s)   [16/17 mutants, ETA 0s]
  [3/3] pricing/rules.py::shipping                   4/6 killed, 1 gap(s)   [17/17 mutants, ETA 0s]

==============================================================================
SUITE AUDIT - pricing
==============================================================================
  mutants scored     : 17
  killed by the suite: 13  (76.5%)
  PROVEN GAPS        : 2  (2 with an unarguable witness)
  unproven survivors : 2  (not counted as gaps; by reason below)
         1  disagreed only where both versions raised, so it is not proof
         1  ran on real inputs and agreed - possibly an equivalent mutant
      only 1 of 2 ran on a real input and agreed; the rest is
      this tool's reach, not evidence about the suite
  uncovered functions: 1  (no test reaches them at all)
  covered fraction   : 75.0%  (3 of 4 functions)
  CAUGHT SHARE       : 57.4%  (kill rate x covered fraction - the share of the whole package
                       whose mutants this suite would notice, and the one
                       figure that testing less cannot raise)

  gaps, strongest evidence first:

  pricing/rules.py::with_tax   [const]
    why it counts : both versions return a value, and the values differ
    call          : with_tax(10, 0.2)
    input         : a call your tests really made
    before        : ok: 12.0
    after         : ok: 22.0
  pricing/rules.py::shipping   [const]
    why it counts : both versions return a value, and the values differ
    call          : shipping(3)
    input         : generated - not a value your tests use; judge whether it is realistic
    before        : ok: 7
    after         : ok: 5

  1 function(s) no test reaches:
    pricing/rules.py::format_price

  took 9s
```

The test *made* the call `with_tax(10, 0.2)` and accepted `22.0` as readily as `12.0`;
nothing tests `shipping` at 3 kg. The two unproven survivors are equivalent on every input
tried and are not accused.

## Results

Three real audits (Python 3.12; toolz at `451af60`, repo-surgeon at `d69c12d`; details in
[docs/RESULTS.md](https://github.com/hammasbuilds/suite-auditor/blob/main/docs/RESULTS.md)):

| | [`toolz`](https://github.com/pytoolz/toolz) | [`tomli`](https://github.com/hukkin/tomli) | [`repo-surgeon`](https://github.com/hammasbuilds/repo-surgeon) |
|---|---:|---:|---:|
| third-party? | yes | yes | no, ours |
| functions in the package | 157 | 39 | 67 |
| **reached by no test** | 15 | **0** | **28 (42%)** |
| mutants scored | 418 | 154 | 154 |
| **kill rate** | **92.1%** | **97.4%** | **60.4%** |
| caught share | 82.7% | **97.4%** | 35.2% |
| proven gaps | **1** | **0** | **7** (6 on a call the tests really made) |
| unproven survivors | 32 | 4 | 54 |
| &nbsp;&nbsp;ran on a real input and agreed | 12 | 1 | 12 |
| &nbsp;&nbsp;a method, which cannot be called without its instance | 6 | 1 | 19 |
| &nbsp;&nbsp;no valid input could be built | 9 | 2 | 20 |
| &nbsp;&nbsp;disagreed only where both versions raised | 5 | 0 | 3 |
| &nbsp;&nbsp;the unmutated function could not be called either | 0 | 0 | 0 |

Only the first unproven row is about the suite; the others measure how far the tool could
reach. The toolz gap, verified independently against installed toolz:

```
toolz/functoolz.py::is_arity   `return rv` -> `return None` when the signature is unknown
  is_arity(2, dict)   original False, mutant None
```

In repo-surgeon, four gaps are calls the suite made and then accepted a different answer
for, e.g. `_looks_like_number('src_path')` returning `True` instead of `False`.

**Sensitivity.** [docs/SENSITIVITY.md](https://github.com/hammasbuilds/suite-auditor/blob/main/docs/SENSITIVITY.md)
plants gaps in a target's own tests and counts how many the prover proves:

| target | planted survivors | proven |
|---|---:|---:|
| `toolz` `451af60` | 2 | **2** |
| `boltons` `4e5faa3` | 2 | **2** |
| `more-itertools` `1ea82a7` | 1 | **1** |
| `cachetools` `9976f1a` | 0 | no plant created a survivor |

In `cachetools`, `methodkey` delegates to `hashkey`, so weakening `test_hashkey` leaves
`test_methodkey` still killing the mutant; the script reports that instead of a rate over
an empty denominator.

**Against cosmic-ray** on the bundled example
([docs/COMPARISON.md](https://github.com/hammasbuilds/suite-auditor/blob/main/docs/COMPARISON.md)):
cosmic-ray reports 41 survivors as function-plus-operator names, 26 of them in the one
function no test reaches; suite-auditor reports 2 proven gaps with the call that proves each,
plus 2 refusals with their reason.

**Speed.** Every mutant is a pytest start-up: toolz's 418 mutants took 3.4 minutes with 4
workers on a quiet 16-core Windows machine and 11.5 minutes when it was loaded.

## Tech stack

- Python 3.11+, **no runtime dependencies** (`ast`, `sys.settrace`, `subprocess`)
- pytest (in the target project's own environment)
- ruff, uv, hatchling, GitHub Actions (test matrix, wheel build-and-install job)

## Run it

```bash
pip install git+https://github.com/hammasbuilds/suite-auditor
# or: pipx install git+https://github.com/hammasbuilds/suite-auditor

cd your-project
suite-auditor coverage .                       # one suite run: which functions no test reaches
suite-auditor audit . --out audit-out          # full audit, writes AUDIT.md and audit.json
suite-auditor audit . --max-seconds 600        # stop after ten minutes, report what was scored
suite-auditor audit . --fail-on-gap            # in CI: exit 1 if a gap is proven
```

suite-auditor runs **your project's** tests with **your project's** interpreter, so pytest
and the project's dependencies must be installed there. The interpreter is chosen in this
order: `--python PATH` (interpreter or venv directory); `.venv/`, `venv/` or `env/` inside the
project; the activated `$VIRTUAL_ENV` (with a warning if it is outside the project); the
interpreter suite-auditor runs on. The first output line says which was used.

| Option | |
|---|---|
| `--test PATH` | restrict to a test directory or file (relative to the repo) |
| `--python PATH` | interpreter or venv that runs the target's tests |
| `-j N` | functions audited in parallel, each in its own scratch copy; default `auto` (half the CPUs, at most 4). Use `-j 1` for tests that share outside state (a fixed port, a file in the home directory) |
| `--per-function N` | mutants per function (default 6) |
| `--limit N` | only the first N covered functions |
| `--max-seconds S` | stop after S seconds and report the partial sample, marked PARTIAL |
| `--timeout S` | cap on any one test run (default 300) |
| `--out DIR` | write `AUDIT.md` and `audit.json` |
| `--fail-on-gap` | exit 1 if a gap is proven |
| `-q` | no progress output |

Exit status: **0** finished; **1** `--fail-on-gap` and a gap was proven; **2** could not
audit (bad path, pytest missing, nothing traced or scored). Compiled extensions must be built
in place in the target.

Development:

```bash
git clone https://github.com/hammasbuilds/suite-auditor
cd suite-auditor
uv sync                 # .venv with pytest and ruff
uv run pytest -q
uv run python demo.py
```

## Licence

MIT, see [LICENSE](https://github.com/hammasbuilds/suite-auditor/blob/main/LICENSE).
