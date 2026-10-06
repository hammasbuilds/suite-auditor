# How often does the prover prove a gap that is really there?

[<- back to README](https://github.com/hammasbuilds/suite-auditor#readme)

The headline evidence for this tool was *"toolz: 418 mutants, 92.6% kill rate, **0 proven
gaps**"*. That reads as a clean bill of health for toolz's suite, and it cannot be on its
own: **a prover that proved nothing anywhere would also score 0.** Nothing here measured
how often the prover proves a gap that is definitely present, so the 0 had no scale behind
it.

This measures it. Reproduce with
[`scripts/plant_and_score.py`](../scripts/plant_and_score.py).

## Method

A gap, in this tool's terms, is a mutant the suite fails to kill **and** for which a
concrete call separates the mutant from the original. So a gap is planted by weakening the
**suite**, never the source: take a test assertion that currently kills a mutant and loosen
it. The mutant then survives, it is still behaviourally different from the original, and
the prover ought to prove it.

Audit the pristine target, then audit each planted variant. Mutants that survive in the
variant and did not survive pristine are the hole the plant made. Of those:

    sensitivity = proven gaps among newly-surviving mutants / newly-surviving mutants

The denominator is survivors rather than mutants, because a survivor the prover cannot
separate is not evidence against the suite — counting it as one is the mistake this tool
exists to avoid.

## Result on a third-party target

`toolz` at `451af60`, `toolz/dicttoolz.py`, five plants in `toolz/tests/test_dicttoolz.py`.
Each plant is one edit, listed in the script.

| plant | newly surviving | proven |
|---|---:|---:|
| `valmap` — every assertion loosened | 0 | — |
| `keymap` — every assertion loosened | 0 | — |
| `valmap`/`keymap` — `os.environ` checks dropped | 0 | — |
| `valfilter` — equality loosened | 1 | **1** |
| `keyfilter` — equality loosened | 1 | **1** |
| | **2** | **2 = 100%** |

**Three of the five plants created no survivor at all.** Loosening one assertion in a suite
with a 92.4% kill rate does not make a hole: other assertions still catch the mutant. That
is a real property of a thorough suite and the reason a planted-gap study has to be run
against one.

### The 100% is after a fix this study found

The first run of exactly this measurement was **0 of 2**. Both planted gaps came back with
the same reason:

```
survived; inconclusive: all 64 argument sets raised on both sides
```

`valfilter(predicate, d, factory=dict)` takes a **callable** first. The argument pool is
harvested from literals in the covering tests — constants, lists, tuples, dicts, sets — and
a callable passed in a test is an `ast.Name`, never a `Constant`, so it was never harvested.
All 64 generated calls raised `TypeError` on both sides, and a gap that was definitely
there could not be proven.

`toolz` is a functional library, so that is most of it. It is a much better explanation of
*"toolz: 0 proven gaps"* than *"toolz's suite is excellent"*.

The fix is small: a parameter whose **name** says it takes a function
(`func`, `predicate`, `key`, `factory`, …) is offered builtins and a lambda alongside the
usual values — builtins only, because the probe must resolve them without importing
anything from the target's test module, which is the one thing this design does not do. So
`iseven` from the test file is unavailable even though it would be the ideal argument.

Fixing `predicate` alone was not enough: the third argument then came out as `{1: 1}`, a
dict *instance*, so `factory()` raised *"'dict' object is not callable"* and all 64 calls
still failed. `factory` had to be in the name list and the type constructors in the pool.

### What that fix did and did not change

It did not move any published number. The full `toolz` audit was run twice in one
environment, with the new pool and with the old:

| | mutants | killed | kill rate | proven gaps | unproven |
|---|---:|---:|---:|---:|---:|
| old pool | 422 | 390 | 92.4% | 0 | 32 |
| new pool | 422 | 390 | 92.4% | 0 | 32 |

Identical, and the `no valid input could be built` bucket stayed at 12. The callables
unlocked `valfilter` and `keyfilter` — which the planted study proves — and none of the 12
functions in that bucket on the full run, so the published breakdown is unchanged.

(Both runs give 422 mutants and 92.4% against the published 418 and 92.6%, because they ran
on **Python 3.14** and the published column is 3.12. `RESULTS.md` already records that six
mutants come out differently there. Running the old pool in the same environment is what
isolates the two causes.)

## What this does not measure

Whether the plants resemble the gaps that matter in real code. They are deliberately
ordinary — a loosened equality, a dropped assertion, a tautology — but they were chosen by
the same person who wrote the prover, and no planted-gap study escapes that. It is a
**floor** on sensitivity, from one module of one library.

A rate of exactly 100% also says the plants did not discriminate: nothing here was hard
enough to separate a good prover from a lucky one. The script prints that warning itself
when the rate comes out at 1.000, and the number worth quoting is the pair — **0 of 2
before the fix, 2 of 2 after** — because the change between them is the only part that
measures the prover rather than the plants.

## Still missing

A sweep over several third-party targets, so the rate has more than two observations behind
it. The script takes `--target`, `--plants`, `--test-target` and `--python` for exactly
that; what is missing is the targets, not the harness.
