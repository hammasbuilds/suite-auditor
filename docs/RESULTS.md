# Results

Two audits, re-run on 2026-10-04 with the current engine (twelve operators, inputs
recorded from the covering tests' real calls, admissibility rules for proof):

```bash
git clone https://github.com/pytoolz/toolz targets/toolz && git -C targets/toolz checkout 451af60
git clone https://github.com/hammasbuilds/repo-surgeon targets/repo-surgeon && git -C targets/repo-surgeon checkout d69c12d
# each target gets its own venv with pytest: python -m venv targets/<name>/.venv, then pip install pytest
suite-auditor audit targets/toolz        --test toolz/tests --per-function 5 -j 4
suite-auditor audit targets/repo-surgeon --test tests       --per-function 5 -j 4
```

The toolz column alone, from a clean checkout: `sh scripts/reproduce_toolz.sh`.

Environment: Windows 11, Python 3.12.14, pytest 9.1.1, a 16-core machine.

**The toolz column depends on the Python version.** The previous run (2026-09-27, Python
3.14.7) killed 385 and left 33 unproven; on 3.12 it is 387 and 31. The six mutants that
differ are all in code toolz runs differently by version: three `drop_return` mutants in
`toolz/_signatures.py` and `functoolz.is_arity` (its registry of builtin signatures), and
two in `Compose._combined_annotations`, which takes 3.14's `annotation_format`. repo-surgeon
gives identical numbers on both. Every number below comes out of
[`docs/audit-toolz.json`](https://github.com/hammasbuilds/suite-auditor/blob/main/docs/audit-toolz.json)
and [`docs/audit-repo-surgeon.json`](https://github.com/hammasbuilds/suite-auditor/blob/main/docs/audit-repo-surgeon.json).
No language model is involved anywhere in this tool.

## The two runs

| | [`toolz`](https://github.com/pytoolz/toolz) | [`repo-surgeon`](https://github.com/hammasbuilds/repo-surgeon) |
|---|---:|---:|
| functions in the package | 157 | 67 |
| reached by a passing test | 141 | 39 |
| reached only by a failing test | 1 | 0 |
| **reached by no test** | 15 | **28 (42%)** |
| mutants scored | 418 | 154 |
| killed by the suite | 385 | 93 |
| **kill rate** | **92.1%** | **60.4%** |
| **caught share** (kill rate x covered fraction) | 82.7% | 35.2% |
| proven gaps | **1** | **7** |
| of those, on a call the tests really made | - | 6 |
| unproven survivors | 32 | 54 |
| wall clock | 150 s | 117 s |

`toolz` had one test failing in this environment; it was left out of the run (a failing
test would "kill" every mutant it meets), and the one function only it reaches is listed
separately rather than as unreached.

### toolz's one gap, and why it was 0 until 2026-10-07

```
toolz/functoolz.py::is_arity   `return rv` -> `return None` when the signature is unknown
  is_arity(2, dict)   original False, mutant None
```

The zero was this tool's doing, not a property of toolz's suite. The prover rebuilt a
header around each extracted function and kept only assignments whose value was a
**literal**, so a module-level constant built by a call was dropped and the function raised
`NameError` on every generated input - reported as "all N argument sets raised on both
sides", which reads as a bad argument pool. Three further defects in the pool sat behind
it. [`docs/SENSITIVITY.md`](SENSITIVITY.md) has all four; the planted-gap study is what
exposed them, on `boltons` rather than here.

Checked outside this tool before being published: the mutant was reconstructed against
installed toolz, and `is_arity(2, dict)` - an ordinary two-argument call, no generated
third argument - returns `False` where the mutant returns `None`.

## What changed since the first published numbers

The first version of this page reported toolz 90.6% / 4 gaps and repo-surgeon 68.0% /
3 gaps, over five operators. Nothing about either target changed; the tool did:

- **toolz, 4 gaps -> 0.** Every one of them rested on a generated argument. The last
  three were all `get_exclude_keywords(0, 0)`: the original returns early on the first
  `0` and never looks at the second argument, the mutant does not return early and asks
  the int `0` for `.parameters`. A real signature object would not have noticed. On a
  made-up input, a mutant that trips over the argument's *type* is no longer proof.
- **More operators, and methods.** Seven operators were added (twelve in all) and methods,
  which silently produced no mutants before, are now mutated. That is why both mutant
  counts grew, and why repo-surgeon's kill rate fell: `drop_return` alone is 144 of
  toolz's 418 mutants.
- **Uncovered, 5 -> 15 on toolz.** The published 5 does not reproduce here: the old code,
  run with the same flags in this environment, reports 14 - nine of them in
  `toolz/sandbox`, whose tests live outside `--test toolz/tests`. The new code reports
  those same 14 plus `_InstanceAnnotations.__init__`, which the old lookup counted as
  covered only because it matched methods by bare name, so any `__init__` in a file
  "covered" every other `__init__` in it. The trace now keys functions by qualified name.

## The seven repo-surgeon gaps

| function | operator | call | before | after | input |
|---|---|---|---|---|---|
| `pipeline.py::_bound_names_of` | boolop | `('from pathlib import Path')` | `{'Path'}` | `{None}` | real call |
| `pipeline.py::_bound_names_of` | negate_if | `('from pathlib import Path')` | `{'Path'}` | `set()` | real call |
| `values.py::_looks_like_number` | compare | `('src_path')` | `False` | `True` | real call |
| `values.py::_looks_like_path` | compare | `('a')` | `False` | `True` | real call |
| `values.py::_looks_like_number` | drop_return | `('src_path')` | `False` | `None` | real call |
| `scout.py::free_names` | drop_return | `('    def inner(q): ...')` | `set()` | `None` | real call |
| `pipeline.py::apply_hunks` | drop_return | `(1, '')` | `[]` | `None` | generated |

The first four are the kind worth sending to a maintainer: the suite made that exact call
and accepted a different, truthy-different answer. The last three change a falsy value
into `None` - a real difference that an `assert not result` cannot see, and the generated
`apply_hunks(1, '')` needs the reader to judge whether that call is realistic. The report
says which is which rather than leaving it to be discovered.

## The cheapest finding is still the most useful one

Both audits produced a list of functions nothing executes, and that list costs one suite
run - no mutation - with `suite-auditor coverage <repo>`. For `repo-surgeon` it names
`pipeline.py::run` (the orchestrator the tool is built around), the whole CLI and the
model client: 28 of 67 functions, in a project whose README said "47 tests".

## Limits

- **Two repositories**, one of them mine. A kill rate is a property of a suite, and two
  suites are not a survey.
- **Unproven survivors are not gaps and not non-gaps.** toolz 31, repo-surgeon 54. For
  toolz: 16 disagreed only on inadmissible inputs or raised on every input, 5 agreed on
  every input, 8 are methods (never proven: a method needs its instance), 2 could not be
  loaded in isolation.
- **Uncovered functions are not mutated at all**, so they contribute nothing to the kill
  rate; the caught share is printed next to it for that reason.
- **Timing is from a loaded laptop** and is only indicative.
