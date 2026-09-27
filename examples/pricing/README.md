# pricing - an example target

Three functions with tests of very different quality, and one with none:

| function | its tests |
|---|---|
| `discount` | parametrized over every branch and both sides of each boundary |
| `shipping` | one point either side of the 2 kg boundary, never the boundary |
| `with_tax` | `assert with_tax(10) > 10` |
| `format_price` | none |

`python demo.py`, run from the root of the suite-auditor repository, audits it.
