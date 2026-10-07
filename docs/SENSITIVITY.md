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

## Four targets, and the four defects the second one found

`toolz` was one module of one library, which was the open item on this page. Three more
third-party targets were added on 2026-10-07, each a real repository with its own suite:

| target | revision | module | tests weakened | planted survivors | proven |
|---|---|---|---|---:|---:|
| `toolz` | `451af60` | `toolz/dicttoolz.py` | `toolz/tests/test_dicttoolz.py` | 2 | **2** |
| `boltons` | `4e5faa3` | `boltons/strutils.py` | `tests/test_strutils.py` | 2 | **2** |
| `more-itertools` | `1ea82a7` | `more_itertools/recipes.py` | `tests/test_recipes.py` | 1 | **1** |
| `cachetools` | `9976f1a` | `src/cachetools/keys.py` | `tests/test_keys.py` | 0 | — |

`cachetools` produced no survivor from any of four plants, and the reason is worth more
than the row: `methodkey` delegates to `hashkey`, so a mutant in `hashkey` is still killed
by `test_methodkey` even after `test_hashkey` has been gutted. A suite where several tests
reach one function cannot be holed by weakening one of them. That is a property of the
suite, not a failure of the harness, and the script says "the plants no longer bite" rather
than reporting a rate over an empty denominator.

### Where this started: toolz, 0 of 2

The first run of this measurement was **0 of 2**, and both planted gaps came back with the
same reason:

```
survived; inconclusive: all 64 argument sets raised on both sides
```

`valfilter(predicate, d, factory=dict)` takes a **callable** first. The argument pool is
harvested from literals in the covering tests — constants, lists, tuples, dicts, sets — and
a callable passed in a test is an `ast.Name`, never a `Constant`, so it was never harvested.
All 64 generated calls raised `TypeError` on both sides, and a gap that was definitely
there could not be proven. `toolz` is a functional library, so that is most of it, and it
is a much better explanation of *"toolz: 0 proven gaps"* than *"toolz's suite is
excellent"*.

A parameter whose **name** says it takes a function (`func`, `predicate`, `key`, `factory`,
…) is offered builtins and a lambda alongside the usual values — builtins only, because the
probe has to resolve them without importing anything from the target's test module, which
is the one thing this design does not do. So `iseven` from the test file is unavailable
even though it would be the ideal argument. Fixing `predicate` alone was not enough: the
third argument then came out as `{1: 1}`, a dict *instance*, so `factory()` raised
*"'dict' object is not callable"* and all 64 calls still failed. `factory` had to be in the
name list and the type constructors in the pool.

Three of the five toolz plants create no survivor at all. Loosening one assertion in a
suite with a 92% kill rate does not make a hole — other assertions still catch the mutant —
and that is a real property of a thorough suite, which is the reason to run this against
one.

### The run that mattered was the one that failed

`boltons` came back **0 of 2** first, and both rows read `no_input` - the same verdict
`toolz` gave before the callable fix. Chasing it found four separate defects, each hidden
behind the last:

1. **The extracted function had no module globals.** `header_for` rebuilt a header around
   each mutant from the imports and assignments it read, but kept only assignments whose
   value was a **literal**. `ANSI_SEQUENCES = re.compile(...)` is a call, so it was
   dropped, and every one of the 23 generated calls raised
   `NameError: name 'ANSI_SEQUENCES' is not defined`. Reported as *"all 23 argument sets
   raised on both sides"*, which reads as a bad argument pool and sends the diagnosis to
   the wrong place entirely. The header is now a transitive closure over module-level
   statements - imports, assignments of any shape, helper functions and classes.
2. **No `bytes` in the generic pool.** With the function running, mutant and original
   agreed on all 11 exercised inputs, because every one was a `str` and `strip_ansi`
   differs only on its `bytes` branch.
3. **The generic pool was being cut out entirely.** It was appended last and the result
   trimmed to 24 values, so on a function whose covering tests supply plenty of literals
   **none** of it survived. Six slots are now reserved for it.
4. **Those six slots were all ints and strings.** `GENERIC` was ordered by how ordinary a
   value looks, so the reserved six came out `0, 1, -1, "", "a", []`: two types for six
   slots, and the bytes still never offered. It is ordered by **type** now - int, str,
   None, bytes, list, bool.

With all four: `strip_ansi(b"")` returns `b''` where the mutant returns `''`, and the
planted gaps are proven. Each fix alone would have left the rate at 0 of 2, which is why
the reason a survivor was unproven is now recorded per plant and printed. A sensitivity
number with no reason beside it says the prover failed without saying what to do about it.

### And the one after that

`more-itertools` came back **0 of 1**, reason `equivalent` - "every argument set agrees,
possibly an equivalent mutant". It was not equivalent. The mutation was
`tabulate(function, start=0)` → `start=1`, and **a mutated default cannot be reached while
every parameter is always supplied**. Calls that omit the optional parameters are now
generated; the first version of that still failed, because one truncated call reused the
baseline's values and gave `tabulate(dict)`, which raises on its first item either way.
Truncated calls vary their remaining arguments like any other, and `CALLABLE_POOL` was
split: a parameter named `factory` or `cls` still gets the type constructors first, because
`factory()` must be callable with no arguments, while `function` and `predicate` get the
identity lambda first. `tabulate((lambda x: x))` then yields `[0, 1, 2, …]` against
`[1, 2, 3, …]`.

"Possibly an equivalent mutant" is the wrong thing to say about a mutation nothing could
reach, and it is the verdict most likely to be believed, because it sounds like a fact
about the code rather than a limit of the tool.

### A third one, found by reading the reasons rather than the rate

The nine `no_input` survivors on toolz are what the audit calls "no valid input could be
built", and once the message carried the exception two of them turned out not to be that
at all:

```
_restore_curry()  missing 1 required positional argument: 'is_decorated'   (64 of 64)
```

`toolz/functoolz.py::_restore_curry(cls, func, args, kwargs, userdict, is_decorated)` is
a **module-level function** whose first parameter happens to be named `cls`. Both the
argument generator and the call recorder dropped it as a receiver, so every generated
call and every observed call was one argument short - and an observed arity that
disagrees with the signature makes the generator fall back to the observed sets alone,
which is how 64 generated sets became 21 invalid ones. A limitation of the tool's reach,
reported for what was a miscounted call.

A dot in the qualified name is the signal, in the AST and at runtime. "Contains a dot" was
the first rule and it was wrong too: a function nested inside another function has one,
so the test's own fixture - a `def plain(cls, ...)` written inside the test - was read as
a method. What separates them is the segment immediately before the name: `<locals>` means
nested in a function, a class name means a method, nothing means module level.

`_restore_curry` is still unprovable, and now says why: it calls `cls(func, *args,
**kwargs)`, so it needs an instance of toolz's own `curry` class, which no literal can
stand in for. That is the honest shape of the `no_input` bucket - it did not shrink, and
every entry in it now names the structured value it could not build. A parameter named
`args` or `kwargs` is offered tuples and dicts for the same reason the callable pool
exists, which fixed the first 53 of those 64 failures without reaching the rest.

## What this still does not measure

Whether the plants resemble the gaps that matter in real code. They are deliberately
ordinary — a loosened equality, a dropped assertion, a tautology — but they were chosen by
the same person who wrote the prover, and no planted-gap study escapes that. It is a
**floor** on sensitivity.

A rate of exactly 100% also says the plants did not discriminate: nothing in that target
was hard enough to separate a good prover from a lucky one. The script prints that warning
itself when the rate comes out at 1.000, and the number worth quoting is never a single
rate but the pair across a fix — **0 of 2 before, 2 of 2 after** on `toolz`, and again on
`boltons` — because the change between them is the only part that measures the prover
rather than the plants.

## Still missing

More targets still. Four is better than one and it is not a distribution; the plants are
also all in text and sequence utilities, where an argument is a string or an integer, and
nothing here plants a gap in a class-heavy or IO-heavy library. `--target`, `--plants`,
`--test-target` and `--python` take any clone, so what is missing remains the targets
rather than the harness.

**9 of toolz's 32 unproven survivors are still `no_input`.** The header closure fixed the
case `boltons` exposed, not the category: a function needing an instance of its own class,
or a value no generated literal can stand in for, still cannot be called. That bucket is
the honest size of what this tool cannot reach, and it is printed with every audit rather
than left out of the summary.
