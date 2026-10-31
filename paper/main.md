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

Within the 112, median kill rate is 0.85 (IQR 0.73–0.91), but kill rate is measured over
covered functions only; the unconditional **caught share** — the fraction of a package's
functions both reached and checked — has a median of **0.71** (IQR 0.50–0.82), and
**45.5% [36.6%, 54.8%] carry at least one gap we can
prove** — a surviving mutant with a witness: an argument set on
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

**The denominator, and a claim this paper used to make about it.** An earlier draft said
that no study reports how many projects it could *not* audit, or why. That is false, and
it is the third absence-based claim in this author's related work to fail on a second
literature pass; the other two were a novelty claim about executing dependency updates to
detect behavioural breakage, and one about measuring in-domain against out-of-domain
language identification. Teralizer
[@teralizer] reports exactly such a funnel: of 632 RepoReapers projects **1.7% completed
its pipeline**, with the attrition attributed per stage, and its stage percentages are
given against both the full pool and the stage input — the two readings this paper also
reports. Build-attrition studies make the same move outside mutation testing, one
reporting 38% of snapshots buildable and another 62% of commits per project not
compilable.

The honest gap is narrower. Those funnels are Java, and they are reported in service of
another contribution — a generalisation pipeline, a build-reproducibility study — rather
than as the result. What this paper adds is the funnel **as the finding**, over a sample
drawn by PyPI download rank rather than by GitHub stars, with a Wilson interval at every
stage, and with the kill rates read back through it so the selection bias the funnel
creates is attached to the numbers it biases (§5). The number itself is the point: most
of the top 1,000 cannot be audited at all.

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
| kill rate (over covered functions) | 0.850 | 0.733–0.908 | 0.000 | 1.000 |
| covered fraction | 0.861 | 0.682–0.957 | 0.019 | 1.000 |
| **caught share** (their product) | **0.710** | 0.500–0.817 | 0.000 | 1.000 |
| mutants scored | 100 | 36–183 | 1 | 1,669 |
| proven gaps | 0 | 0–3 | 0 | 33 |

**Kill rate and covered fraction are not siblings and must not be read as a pair.** Only
functions a passing test reaches are mutated, so kill rate is measured over the covered
subset and is *conditional on* the covered fraction. The two share no denominator and
neither bounds the other. Two packages in this sample make the point concretely:
`pyasn1-modules` and `jaraco-context` both score a **kill rate of 1.000** on covered
fractions of **0.042** and **0.048**, so a perfect mutation score is reported while 96%
of each package is never examined. Six packages cover under 20% of their functions, and
five of those have a caught share below 0.125.

The unconditional quantity is their product, the **caught share** — the fraction of a
package's functions that are both reached by a test and checked by one. Its median is
**0.710** against a median kill rate of 0.850, and its lower quartile is 0.500 against
0.733. Reporting kill rate alone overstates how much of a package is actually audited by
roughly fourteen points at the median and twenty-three at the lower quartile. Figure 3
plots the two axes with iso-contours of their product, which is the only comparison
between them that is defined.

**45.5% [36.6%, 54.8%] of audited packages carry at least one proven gap.** The median
package has none, so the distribution is the finding: proven gaps are concentrated, as the
mutants-scored range (1 to 1,669) suggests they would be.

## 3.1 Figures

![Figure 1](../research/figures/fig1_funnel.png)

**Figure 1.** From PyPI's 1,000 most-downloaded packages to the 112 that could be audited,
one row per filter, each giving what the filter removed over what reached it.

![Figure 2](../research/figures/fig2_kill_rate_ecdf.png)

**Figure 2.** Empirical distribution of kill rate over the 112 audited packages, with
quartiles marked. An ECDF rather than a histogram: the bin width of a histogram would be a
free parameter doing argumentative work at the left tail, where the interesting packages
are.

![Figure 3](../research/figures/fig3_coverage_vs_kill_rate.png)

**Figure 3.** Covered fraction against kill rate with dotted iso-contours of their
product. Distance from the origin, not distance from a diagonal, is the thing to read: the
axes are not comparable to each other because kill rate is conditional on coverage.

![Figure 4](../research/figures/fig4_proven_gaps.png)

**Figure 4.** Proven gaps per audited package, counts of 11 and above collected in the last
bar. The median package has none and the maximum is 33, so the mean of 1.96 describes no
package in the sample.

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
- [@teralizer] Glock, Bauer & Pinzger. *Teralizer: Semantics-Based Test Generalization
  from Conventional Unit Tests to Property-Based Tests.* arXiv:2512.14475, December 2025.
  Cited for its eligibility funnel; quoted from the abstract, which states the 632
  projects and the 1.7% completion rate.

> **Citation status.** The references were located by literature search and quoted from
> search summaries, not read in full. The one claim flagged here for a confirmatory pass
> — that no comparable study reports its excluded denominator — was given that pass and
> **did not survive it**: Teralizer [@teralizer] reports a per-stage funnel over 632
> projects against both denominators, and §1 now cites it and states a narrower gap. That
> makes three absence-based claims across these papers that a second search overturned
> and none that it confirmed, which is a fact about the method rather than about any one
> claim: an absence found by literature search is not a result here, and the remaining
> references still need reading in full before submission.
