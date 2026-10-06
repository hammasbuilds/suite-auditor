<h1 align="center">suite-auditor</h1>
<p align="center"><i>What your pytest suite would not notice, with the input that proves it</i></p>

<p align="center">
  <a href="https://github.com/hammasbuilds/suite-auditor/actions/workflows/ci.yml"><img src="https://github.com/hammasbuilds/suite-auditor/actions/workflows/ci.yml/badge.svg" alt="ci"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="python">
  <img src="https://img.shields.io/badge/runtime%20deps-0-brightgreen" alt="zero dependencies">
  <img src="https://img.shields.io/badge/tests-117-brightgreen" alt="tests">
  <a href="https://github.com/hammasbuilds/suite-auditor/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="license"></a>
</p>

suite-auditor mutation-tests a Python project's test suite and reports only what it can
**prove**: a surviving mutant becomes a *gap* once there is a concrete call on which the
mutant and the original return different things. Survivors it cannot separate stay
"unproven" and never reach the headline.

```
trace once        mutate one      run ONLY the        suite failed  -> killed
which tests  -->  change at  -->  tests that    -->
reach what        a time          cover it            suite passed  -> PROVE IT: call both
                                                                       versions on the inputs
                                                                       the tests really used
                                                                         differ -> GAP + the call
                                                                         agree  -> unproven
```

It never modifies your files: the audit runs on a scratch copy of the repository.

## Install

```bash
pip install git+https://github.com/hammasbuilds/suite-auditor
# or: pipx install git+https://github.com/hammasbuilds/suite-auditor
```

PyPI release coming: `pip install suite-auditor` (or `pipx` / `uv tool install`) will work
once it is published.

No runtime dependencies. suite-auditor runs **your project's** tests with **your
project's** interpreter, so pytest and the project's dependencies must be installed
there - not alongside suite-auditor. The interpreter is picked in this order:

1. `--python PATH` (an interpreter, or a virtual environment directory)
2. a virtual environment inside the project: `.venv/`, `venv/` or `env/`
3. the activated virtual environment (`$VIRTUAL_ENV`) - with a warning on stderr if it
   lives outside the project, since a shell left activated in another project is the
   usual way to end up auditing against the wrong dependencies
4. the interpreter suite-auditor itself runs on

The first line of output says which one was used and why. If it cannot import pytest,
the tool says so and exits with status 2.

## Use

```bash
cd your-project

# One suite run: which functions does no test reach?
suite-auditor coverage .

# The full audit
suite-auditor audit . --out audit-out

# Bound the time: stop after ten minutes and report what was scored
suite-auditor audit . --max-seconds 600

# In CI: fail the build if a gap is proven
suite-auditor audit . --fail-on-gap
```

## What it looks like

`python demo.py` in a clone audits [`examples/pricing`](https://github.com/hammasbuilds/suite-auditor/tree/main/examples/pricing),
a small project bundled for the purpose: `discount` is tested over every branch and both
sides of each boundary; `shipping` at one point either side of its boundary; `with_tax`
by `assert with_tax(10) > 10`; `format_price` not at all. Real output:

```
$ suite-auditor audit examples/pricing --python <this interpreter> -j auto
python: ...\Scripts\python.exe  (from --python)
copying the repository to a scratch directory (your files are never modified)...
tracing which tests cover which functions (one suite run)...
  3 functions traced, 8 tests passed
  3 functions have covering tests, 1 have none

mutating 3 function(s): 17 mutants, 3 in parallel...
  [1/3] pricing/rules.py::discount                   6/6 killed, 0 gap(s)   [14/17 mutants, ETA 0s]
  [2/3] pricing/rules.py::with_tax                   3/5 killed, 1 gap(s)   [16/17 mutants, ETA 0s]
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

  took 4s
```

Read the two gaps: the test *made* the call `with_tax(10, 0.2)` and accepted `22.0` as
readily as `12.0`; and nothing tests `shipping` at 3 kg, where a `<= 2` quietly turned
into `<= 3` goes unnoticed. The thoroughly tested `discount` has every mutant killed. The
two unproven survivors are equivalent on every input tried (`round(..., 2)` vs
`round(..., 3)` on these values; `< 2` vs `<= 2` where both branches give 5) and are not
accused. `python demo.py` (the coverage pass, then this audit) took 5 s on a quiet 16-core
Windows machine, and about a minute when the same machine was busy with other jobs.

## How it works

**Trace once.** A small pytest plugin (written to a temporary directory, never into the
project) runs the suite once under `sys.settrace` and records, for every function, which
passing tests enter it **and the literal arguments it was called with**. Tests that
already fail are left out: a failing test would "kill" every mutant it meets.

**Mutate plausibly, on a copy.** Twelve operators, drawn round-robin so no kind of
mistake crowds out the rest: comparison flips (`<`/`<=`, `==`/`!=`, `in`/`not in`,
`is`/`is not`), arithmetic, `and`/`or`, integer constant +1, negated `if`, dropped
`not`, `return x` to `return None`, swallowed `except`, deleted `assert`, deleted
`raise`, and slice bounds off by one. Each mutant is written into a scratch copy of the
repository - byte-exact apart from the mutated lines, line endings included.

**Run only the covering tests.** Usually a handful per function instead of the whole
suite, with `-x`. First they run once on the unmutated copy: a test that fails on its own
(order-dependent, or leaning on state another test left behind) would otherwise count
every mutant as caught, so that function is skipped and says why. A mutant that makes the
tests hang past 10x their clean run counts as caught.

**Prove every survivor.** The original and the mutant are loaded side by side and called
on, in order of strength:

| input | counts as proof when |
|---|---|
| a call a covering test really made | either version returns a value and they differ |
| values the tests used, recombined | the original returns cleanly and the mutant differs |
| generated: the function's own constants and their neighbours, literals from the covering test files, a few generic corners | the original returns cleanly and the mutant differs |

Both versions raising, differently, is never proof - on an injected `''` it says more
about the argument than about the suite.

**Grade the evidence.** Grade 0: both return values and they differ. Grade 1: one
returns, the other raises. The report leads with grade 0 and with calls the tests
really made.

## Options

| | |
|---|---|
| `--test PATH` | restrict to a test directory or file (relative to the repo) |
| `--python PATH` | interpreter or venv that runs the target's tests |
| `-j N` | functions audited in parallel, each worker in its own scratch copy; default `auto` (half the CPUs, at most 4) |
| `--per-function N` | mutants per function (default 6) |
| `--limit N` | only the first N covered functions |
| `--max-seconds S` | stop after S seconds and report the partial sample, marked PARTIAL |
| `--timeout S` | cap on any one test run (default 300) |
| `--out DIR` | write `AUDIT.md` and `audit.json` |
| `--fail-on-gap` | exit 1 if a gap is proven |
| `-q` | no progress output |

Exit status: **0** finished; **1** `--fail-on-gap` and a gap was proven; **2** could not
audit - bad path, pytest missing, or nothing could be traced or scored. "Nothing scored"
is never exit 0.

## Two real audits

| | [`toolz`](https://github.com/pytoolz/toolz) | [`repo-surgeon`](https://github.com/hammasbuilds/repo-surgeon) |
|---|---:|---:|
| functions in the package | 157 | 67 |
| **reached by no test** | 15 | **28 (42%)** |
| mutants scored | 418 | 154 |
| **kill rate** | **92.6%** | **60.4%** |
| caught share (kill rate x covered fraction) | 83.1% | 35.2% |
| proven gaps | **0** | **7** (6 on a call the tests really made) |
| unproven survivors | 31 | 54 |
| &nbsp;&nbsp;of those: ran on a real input and agreed | 5 | 12 |
| &nbsp;&nbsp;a method, which cannot be called without its instance | 8 | 19 |
| &nbsp;&nbsp;no valid input could be built | 12 | 20 |
| &nbsp;&nbsp;disagreed only where both versions raised | 4 | 3 |
| &nbsp;&nbsp;the unmutated function could not be called either | 2 | 0 |

**Read the unproven rows before the gap rows.** Only the first of them is about the test
suite. The other three quarters are how far this tool could reach: a function it never
managed to hand a valid argument is a fact about the tool, and one that ran and refused is
a fact about the package. So `toolz`'s **0** is not by itself evidence that the suite is
good - a prover that proved nothing anywhere would also score 0, and 24 of those 31
survivors were never run on a real input at all. What the 0 does establish is narrower and
still worth stating: of the 9 toolz survivors this tool did exercise, 5 agreed on every
input and 4 disagreed only where both versions raised, which is not proof of anything.

`toolz` is a functional library maintained since 2013; its suite killed 387 of 418 mutants.
`repo-surgeon` is one of mine, whose README said "47 tests": 42% of its functions are
reached by no test, and four of its gaps are calls the suite made and then accepted a
different answer for, e.g. `_looks_like_number('src_path')` returning `True` instead of
`False`.

**What is missing here, and is the honest next step:** no study plants known gaps and
measures what share of them the prover proves. Without that, the prover's sensitivity is
unmeasured, and these two columns cannot separate "this suite is thorough" from "this tool
is quiet". `flake-detective`'s `scripts/inject_and_score.py` is the working template.

Both columns were re-run on 2026-10-04 on Python 3.12, toolz at upstream commit `451af60`
(`sh scripts/reproduce_toolz.sh`) and repo-surgeon at `d69c12d` (command in
[docs/RESULTS.md](https://github.com/hammasbuilds/suite-auditor/blob/main/docs/RESULTS.md)).
repo-surgeon gives the same numbers as the 2026-09-27 run; toolz differs in six mutants,
because toolz itself takes different code paths on Python 3.14, where that run was made
(its signature registry for builtins, and `annotation_format`). The first published numbers
(90.6% / 4 gaps and 68.0% / 3 gaps) came from an earlier version with five operators and
text-scraped inputs, and every one of toolz's four old gaps rested on a generated argument
the function would never see.

Full detail in [docs/RESULTS.md](https://github.com/hammasbuilds/suite-auditor/blob/main/docs/RESULTS.md).

## Scope

- **It does not claim a gap it cannot prove.** Survivors without an admissible separating
  input are reported as unproven and never counted. A disagreement is also refused as
  proof when the function does not even agree with its own repeat, or when its source
  calls `random`, `uuid`, a clock or similar - a coin flip is not evidence of a mutant.
  The named-call check is exact for those; an unnamed source of nondeterminism is
  caught by the repeat check, which is very likely but not certain to notice it.
- **Methods are mutated but never "proven".** A method needs its instance, and calling it
  with `self` faked proves nothing, so a surviving method mutant stays unproven.
- **It does not mutate what no test reaches.** Those functions are listed instead, and
  the *caught share* (kill rate x covered fraction) is printed so a high kill rate over a
  small covered fraction cannot pass for a good suite.
- **It is not fast on big suites.** Every mutant is a pytest start-up. On a loaded
  Windows machine that was 1-5 s per mutant (toolz: 418 mutants in 11.5 minutes with 4
  workers; 3.4 minutes when the same machine was quiet); `--limit`, `--per-function` and
  `--max-seconds` are the levers. Workers run in
  parallel by default; tests that share state outside the project (a fixed port, a file in
  the home directory) could collide - use `-j 1` for those.
- **Compiled extensions must be built in place.** The copy's own source directories are
  put first on `PYTHONPATH` so the tests import the mutated copy, not an installed one.
- **It does not replace `mutmut` or `cosmic-ray`,** which are more thorough. This adds the
  proof step and the grading.

## Development

```bash
git clone https://github.com/hammasbuilds/suite-auditor
cd suite-auditor
uv sync                 # creates .venv with the dev group (pytest, ruff)
uv run pytest -q
uv run python demo.py
```

Without uv: `python -m venv .venv`, activate it, then `pip install -e . pytest`.

## License

MIT - see [LICENSE](https://github.com/hammasbuilds/suite-auditor/blob/main/LICENSE).
