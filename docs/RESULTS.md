# Results

Two audits, run with:

```bash
suite-auditor audit targets/toolz        --test toolz/tests --per-function 5
suite-auditor audit targets/repo-surgeon --test tests       --per-function 5
```

No language model is involved anywhere in this tool. Every number below comes out of
`docs/audit-*.json`.

## The two runs

| | [`toolz`](https://github.com/pytoolz/toolz) | [`repo-surgeon`](https://github.com/hammasbuilds/repo-surgeon) |
|---|---:|---:|
| functions in the package | 157 | 67 |
| **reached by no test** | 14 | **28 (42%)** |
| mutants scored | 181 | 100 |
| killed by the suite | 164 | 68 |
| **kill rate** | **90.6%** | **68.0%** |
| proven gaps | 4 | 3 |
| of those, unarguable | 0 | 0 |
| unproven survivors | 13 | 29 |

`toolz` is a mature functional library maintained since 2013. `repo-surgeon` is one of
mine, shipped a few hours before this run, described in its own README as "47 tests, all on
the code that decides".

**42% of its functions have no test at all.** The count was true and it measured nothing.

## What the audits actually found

### The cheapest finding is the most useful one

Neither audit produced a gap worth sending to a maintainer. Both produced a list of
functions nothing executes, and that list costs **one suite run** - no mutation, no
differential, seconds rather than minutes:

```bash
suite-auditor coverage <repo>
```

For `repo-surgeon` it named `pipeline.py::run` - the orchestrator the whole tool is built
around - along with the entire CLI and the model client. Those absences are defensible
choices, and they were invisible in "47 tests".

### A maintained suite accepts very little

The contrast with [`mbpp-false-accepts`](https://github.com/hammasbuilds/mbpp-false-accepts)
is the point of running this at all. There, MBPP's three-assert suites let **17.6%** of
mutants through, and 29% of problems accepted a provably wrong program. Here a real suite
kills 91%.

Three asserts accept a lot. A suite somebody maintains accepts very little. That is worth
knowing before drawing conclusions about test adequacy from a benchmark.

### Zero unarguable gaps, stated plainly

A gap is graded by its witness:

| grade | meaning |
|---|---|
| 0 | both versions return a value, and the values differ - unarguable |
| 1 | one returns, the other raises - real, and the reader must judge the input |
| 2 | both raise, differently - weakest |

Across both audits, **every gap is grade 1 or 2**. None rests on two differing return
values. The tool says so rather than presenting seven "findings" and letting the reader
discover it.

An earlier version of this run reported one grade-0 gap on `toolz`. It was wrong: the
grading function read the `ok:` prefix of `ok: []...then TypeError: ...`, which is what a
drained generator that raised looks like. Both sides had raised. A tool whose product is
graded evidence cannot mis-grade its own, so that is now pinned by a test.

## Limits

- **Two repositories**, one of them mine. A kill rate is a property of a suite, and two
  suites are not a survey.
- **Five mutation operators**: comparison, arithmetic, boolean, integer constant, negated
  `if`. Chosen as plausible mistakes rather than maximally destructive ones. A suite could
  score well here and miss an entire class of error these operators do not produce.
- **Unproven survivors are not gaps and not non-gaps.** 13
  and 29 respectively. Some are equivalent mutants nothing
  could catch; others are failures of the input generator. They are reported and never
  folded into the headline.
- **Uncovered functions are not mutated at all**, so they contribute nothing to the kill
  rate. A suite can post a high kill rate over a small covered fraction - which is exactly
  what `repo-surgeon` does.
