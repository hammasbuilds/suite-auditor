# Against cosmic-ray, on the same target

[<- back to README](https://github.com/hammasbuilds/suite-auditor#readme)

The README says this tool "does not replace mutmut or cosmic-ray", and the pitch is the
proof step and the grading. That is a disclaimer, not evidence. Here is the same target
through both.

`examples/pricing` — four functions, 27 lines, a suite that looks thorough and is not.
cosmic-ray 3.8.0, `suite-auditor audit --per-function 8`.

## What each one reports

| | cosmic-ray | suite-auditor |
|---|---:|---:|
| mutants generated | 143 | 21 |
| killed | 102 | 17 |
| **survivors** | **41 (28.7%)** | **4** |
| …of which proven to behave differently | — | **2** |
| …of which refused, with a reason | — | 2 |
| functions no test reaches, reported as such | — | 1 |

**The rates are not comparable and should not be compared.** cosmic-ray applies every
binary-operator substitution at every site — eleven mutants on one `+` — and has no
per-function cap. 143 against 21 is a difference in how much is generated, not in how much
is found. What is comparable is **what you are told about a survivor.**

## The 41 survivors

cosmic-ray gives a function and an operator name:

```
with_tax       core/ReplaceBinaryOperator_Add_Div
with_tax       core/ReplaceBinaryOperator_Mul_Add
format_price   core/ReplaceBinaryOperator_FloorDiv_Add
format_price   core/ReplaceBinaryOperator_FloorDiv_Sub
...
```

They are accurate. To act on one you have to work out what the mutation was, whether the
function can behave differently under it, and what input would show that — for each of 41.

**26 of the 41 are in `format_price`, which no test reaches at all.** They are not 26 facts
about the suite's thoroughness; they are one fact repeated 26 times, and it is the fact that
matters most. Mixed into a flat list of 41, it looks like 26 of 41.

## The 2 proven gaps

suite-auditor reports a gap only once it has a concrete call on which the original and the
mutant return different things:

```
pricing/rules.py::shipping  [const]
    call (3)          before ok: 7      after ok: 5     input: generated
pricing/rules.py::with_tax  [const]
    call (10, 0.2)    before ok: 12.0   after ok: 22.0  input: observed
```

The second is on a call the tests really made. Both are actionable without reading the
mutation.

The other two survivors are not called gaps, and the report says which kind each is: one
`disagreed only where both versions raised`, one `ran on real inputs and agreed`. And
`format_price` appears once, under "reached by no test" — a function nothing exercises
cannot be mutation-tested, so it is reported separately rather than counted as 26 survivors.

## The same pair on a real package: `xmltodict` 1.0.4

The section above is a 27-line example, and its own limitations say so. `xmltodict` is a
real package from the thousand-package census - one module, 19 functions, 119 tests, a
suite that runs in half a second - so the same comparison can be made where it means
something. Same machine, same virtualenv, same interpreter; cosmic-ray 8.4.1.

| | cosmic-ray | suite-auditor |
|---|---:|---:|
| mutants generated | 413 | 89 |
| …at distinct source positions | 227 | — |
| killed | 345 | 81 (91.0%) |
| **survivors** | **68 (16.5%)** | **8** |
| …proven to behave differently | — | **1** |
| …refused, with a reason | — | 7 |
| functions no test reaches | — | 0 of 19 |
| wall clock | 351s | 28s |

**Again, the rates are not comparable.** cosmic-ray's 68 survivors sit at 33 distinct
source positions, and 22 of them are one family at **two** positions: every binary
substitution of a single `*` - `Mul_Add`, `Mul_Sub`, `Mul_Div`, `Mul_FloorDiv`,
`Mul_Mod`, `Mul_Pow`, `Mul_RShift`, `Mul_LShift`, `Mul_BitOr`, `Mul_BitAnd`,
`Mul_BitXor`. Eighteen more are comparison substitutions at nine positions. A survival
rate over a mutant set built that way measures the operator set as much as the suite.

## The one gap, and why the other tool could not express it

suite-auditor's single proven gap is `drop_return` on `_process_namespace`: the `return
name` in its non-string guard replaced by `return None`.

```
xmltodict.py::_process_namespace   [drop_return]
  why it counts : both versions return a value, and the values differ
  call          : _process_namespace(1, None, ':')
  before        : ok: 1
  after         : ok: None
```

**Verified by hand before publishing this.** With that one line changed, all **119 tests
pass**. 6 of the 64 argument sets the prover tried disagree.

cosmic-ray generated **11 mutants inside that function** - two `ReplaceUnaryOperator`,
four `AddNot`, one `ExceptionReplacer`, four `NumberReplacer` - and **killed all eleven**.
It reports no gap there, and could not: across all 413 mutants it used **64 distinct
operators and not one of them removes or replaces a `return`**. The defect is outside its
operator set on this target.

That is the comparison, stated as narrowly as it deserves: on this package the other tool
generated 4.6 times as many mutants, took 12 times as long, reported 8.5 times as many
survivors with no verdict attached to any of them, and the one survivor that is
demonstrably a hole in the suite is a mutation it does not make.

**What this still does not show.** cosmic-ray's 68 survivors were not individually
examined, so some of them are very likely real gaps that suite-auditor never generated -
the reverse of the case above. A fair recall comparison needs the same operator set on
both sides, which neither tool offers, and nothing here is evidence about which tool
finds more. It is evidence about what you are told once something survives.

## What this does not show

- **cosmic-ray finds more, because it generates more.** Several of its 41 are mutants this
  tool never produced. A fair recall comparison needs the same operator set, which neither
  tool offers.
- **Two targets now**, the 27-line example above and `xmltodict` 1.0.4. See
  [SENSITIVITY.md](SENSITIVITY.md) for the same honesty problem and what was done about it.
- **mutmut is not here.** It does not run on native Windows
  ([boxed/mutmut#397](https://github.com/boxed/mutmut/issues/397)), which is where this was
  measured, so it is absent rather than represented by a guess.

## One note on method, because it nearly produced a wrong answer

The first cosmic-ray run reported **0% survival** — every one of 143 mutants killed. It was
not: a mutant I applied by hand (`amount * (1 + rate)` → `amount + (1 + rate)`) leaves all
8 tests passing. The `test-command` used a relative path, cosmic-ray runs it from its own
working directory, pytest could not find the tests, exited non-zero, and a non-zero exit is
how a mutant is recorded as killed. A clean sheet produced by a check that never ran.

That was my misconfiguration and not a defect in cosmic-ray — absolute paths in
`test-command` fix it. It is recorded because it is the same failure this tool exists to
find one level up, and because a 0% survival rate was believable enough to publish.
