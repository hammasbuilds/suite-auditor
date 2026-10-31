---
title: "Most Python Packages Cannot Be Audited: A Funnel Over PyPI's Top 1,000, and Proof-Backed Mutation Gaps"
author: "Muhammad Hammas, Independent Researcher"
date: 2026-10-31
---

# Abstract

Mutation testing is a mature field with a practical problem: at corpus scale it is, in the
words of one observational study of large Java projects, *computationally intractable*.
Python work is correspondingly small — a recent fault-driven approach is evaluated on 13
applications, and the available tool comparisons run on two programs each.

We attempted to audit the **1,000 most-downloaded packages on PyPI** and report the funnel
rather than only the survivors. **407 ship no test directory. 299 ship tests that collect
nothing. 112 could be audited** — 11.2%, 95% CI [9.4%, 13.3%], or 20.5% [17.3%, 24.1%] of
the 547 that published an sdist containing tests. That funnel is the paper's first result:
the headline obstacle to large-scale mutation study of Python is not compute, it is that
most distributions do not ship a runnable suite.

Within the 112, median kill rate is 0.85 (IQR 0.73–0.91) and **45.5% [36.6%, 54.8%] carry
at least one gap we can prove** — a surviving mutant with a witness: an argument set on
which the mutated function returns a different value, verified by hand before publication.
Mutants that survive without such a witness are **refused with a reason** rather than
reported, which is the design decision the comparison below turns on.

Against `cosmic-ray` on `xmltodict` 1.0.4, that discipline found a gap `cosmic-ray` cannot
express: `drop_return` on `_process_namespace`, a mutant under which **all 119 tests pass**.
`cosmic-ray` generated 11 mutants inside the same function and killed all eleven, and
across 413 mutants used 64 distinct operators **none of which removes or replaces a
`return`**.

# 1. Introduction

A test suite's coverage says which lines ran, not whether running them would have caught
anything. Mutation testing answers the second question and is expensive, so studies are
small or ecosystem-narrow.

**Related work.** A recent Python-specific fault-driven mutation approach is evaluated on
13 open-source applications, drawing its operators from a catalogue of bug and fix patterns
mined from over 1,000 projects [@pytation]. A comparison of five Python mutation tools —
MutPy, Mutmut, Mutatest, Poodle, Cosmic Ray — applies them to two programs. An
observational study of large Java projects relates mutation score to coverage and records
that scoring every file in a corpus is intractable [@mindthegap]. A GitHub mining study
finds mutation testing in practice diverging from the tools in the literature, with
adoption driven by industry.

What is missing is the denominator. Studies report on the projects they could audit; none
reports how many they could not, or why. That number turns out to be most of them.

**Contributions.**

1. The **auditability funnel** over PyPI's top 1,000, with Wilson intervals at each stage
   and both unconditional and conditional readings (§3).
2. **Proof-backed gap reporting** — a surviving mutant is a finding only with a witness,
   and refusals carry reasons (§2).
3. A comparison with `cosmic-ray` locating a gap outside its operator set, and the
   observation that operator-set differences, not suite quality, dominate disagreement
   between mutation tools (§4).
4. An explicit statement of the **selection bias** the funnel creates, and why the audited
   kill rates are an upper bound rather than an estimate (§5).

# 2. Method

Each package's sdist is built in its own virtual environment and its suite must pass on its
own before anything is mutated — a suite that fails unmutated cannot be used to judge a
mutant. Mutants are generated within the distribution's **declared package scope**, read
from `pyproject.toml`'s wheel configuration, so vendored third-party code inside a
distribution is not counted as the package's own: before this rule, one audit attributed
222 vendored functions to its host.

A surviving mutant becomes a reported gap only when the tool can produce a **witness** — an
argument set on which original and mutant return different values, both calls completing.
Everything else is a refusal with a reason: the mutant could not be reached, no argument
set could be constructed, the function needs an instance the tool cannot build, or the
difference appeared only where one version rejected a generated argument. The audit
publishes both counts, so a reader can see how much of a surviving-mutant total is a claim
and how much is an absence of evidence.

# 3. The funnel

| Stage | Excluded | Of all 1,000 | Of those reaching it |
|---|---:|---|---|
| no sdist on PyPI | 46 | 0.046 [0.035, 0.061] | 0.046 of 1,000 |
| **ships no tests** | **407** | **0.407 [0.377, 0.438]** | 0.427 of 954 |
| **tests collect nothing** | **299** | **0.299 [0.271, 0.328]** | 0.547 of 547 |
| suite did not finish in 120 s | 25 | 0.025 [0.017, 0.037] | 0.101 of 248 |
| audit budget exceeded (300 s) | 67 | 0.067 [0.053, 0.084] | 0.300 of 223 |
| no mutable targets | 30 | 0.030 [0.021, 0.043] | 0.192 of 156 |
| other | 14 | 0.014 [0.008, 0.023] | 0.111 of 126 |
| **audited** | **112** | **0.112 [0.094, 0.133]** | — |

Both readings are given because they are different claims. "11.2% of the top 1,000 are
auditable" is true; so is "20.5% [17.3%, 24.1%] of those shipping an sdist with tests are
auditable", and a paper that quotes one without the other is choosing a number.

Within the 112:

| | median | IQR | min | max |
|---|---:|---|---:|---:|
| kill rate | 0.850 | 0.733–0.908 | 0.000 | 1.000 |
| covered fraction | 0.861 | 0.682–0.957 | 0.019 | 1.000 |
| mutants scored | 100 | 36–183 | 1 | 1,669 |
| proven gaps | 0 | 0–3 | 0 | 33 |

**45.5% [36.6%, 54.8%] of audited packages carry at least one proven gap.** The median
package has none, so the distribution is the finding: proven gaps are concentrated, as the
mutants-scored range (1 to 1,669) suggests they would be.

# 4. Comparison with `cosmic-ray`

Same machine, same virtualenv, `xmltodict` 1.0.4 (19 functions, 119 tests):

| | `cosmic-ray` 8.4.1 | this tool |
|---|---:|---:|
| mutants generated | 413 | 89 |
| killed | 345 | 81 (91.0%) |
| survivors | 68 (16.5%) | 8 |
| …proven to behave differently | — | **1** |
| …refused, with a reason | — | 7 |
| wall clock | 351 s | 28 s |

**The rates are not comparable, and the reason is the finding.** `cosmic-ray`'s 68
survivors sit at 33 source positions, and 22 of them are one family at **two** positions —
every binary substitution of a single `*`. A survival rate over a mutant set built that way
measures the operator set as much as the suite.

The single proven gap is `drop_return` on `_process_namespace`: `return name` replaced by
`return None` in its non-string guard, witness `_process_namespace(1, None, ':')` → `1` vs
`None`, 6 of 64 argument sets disagreeing. **Verified by hand: with that mutant in place,
all 119 tests pass.** `cosmic-ray` generated 11 mutants inside that same function and
killed all eleven, and across 413 mutants used 64 distinct operators, **not one of which
removes or replaces a `return`**. The defect is outside its operator set on this target.

Stated as narrowly as it deserves: `cosmic-ray`'s 68 survivors were not individually
examined, so some are very likely real gaps this tool never generated — the reverse case. A
fair recall comparison needs a shared operator set, which neither tool offers.

# 5. Threats to validity

**Selection bias, and its direction.** The 112 are the packages that ship a suite, whose
suite passes alone, and that fit a 300-second budget. Every one of those filters plausibly
correlates with test quality, so the audited sample is biased **towards better-tested
packages** and the kill rates are an **upper bound** on the population, not an estimate of
it. There is nothing to adjust with: the excluded packages have no kill rate by
construction. Only the 67 budget-exceeded and 25 did-not-finish packages are recoverable,
and recovering them needs roughly an order of magnitude more compute.

**A kill rate depends on the operator set** (§4), so the median 0.85 is this tool's
operator set on these packages and is not comparable to a figure from another tool.

**Proven gaps are a lower bound.** The tool refuses what it cannot witness, so the 45.5%
counts only provable gaps; the true share of packages with a real gap is higher by an
unknown amount. This is the intended direction of error.

**One interpreter, one platform, one time budget.** The 300-second budget excluded 67
packages and is the largest single recoverable exclusion.

# 6. Reproducibility

`docs/census-rows.json.gz` (156 KB) holds one row per package with every aggregate the
summary reads and no mutated source. `python scripts/census_summary.py --census
docs/census-rows.json.gz` reproduces the committed summary, which a test checks, so every
figure here rebuilds from the repository without the 19 MB raw census.

# References

- [@pytation] *PyTation: fault-driven mutation for Python.* arXiv:2601.19088.
- [@mindthegap] *Mind the Gap: The Difference Between Coverage and Mutation Score Can Guide Testing Efforts.* arXiv:2309.02395.
- *Static and Dynamic Comparison of Mutation Testing Tools for Python.* SBQS.
- *cosmic-ray* — mutation testing for Python. <https://github.com/sixty-north/cosmic-ray>

> **Citation status.** The references were located by literature search and quoted from
> search summaries, not read in full. In particular the claim in §1 that no comparable
> study reports its excluded denominator rests on the absence of such a study in that
> search and needs one confirmatory pass before submission.
