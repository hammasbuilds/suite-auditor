import pytest

from pricing.rules import discount, shipping, with_tax


# Thorough: every branch and both sides of every boundary.
@pytest.mark.parametrize(
    "total,code,expected",
    [
        (19, "SAVE10", 0),
        (20, "SAVE10", 10),
        (50, "SAVE25", 0),
        (99, "SAVE25", 0),
        (100, "SAVE25", 25),
        (100, "NOPE", 0),
    ],
)
def test_discount(total, code, expected):
    assert discount(total, code) == expected


# Looks fine, checks one point on each side - never the boundary itself.
def test_shipping():
    assert shipping(1) == 5
    assert shipping(5) == 11


# Runs the code and checks almost nothing.
def test_with_tax():
    assert with_tax(10) > 10
