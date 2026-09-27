"""A small pricing module with a suite that looks thorough and is not, evenly."""


def discount(total, code):
    """Percentage off for a promo code; orders under 20 get nothing."""
    if total < 20:
        return 0
    if code == "SAVE10":
        return 10
    if code == "SAVE25" and total >= 100:
        return 25
    return 0


def shipping(weight_kg):
    """Flat 5 up to 2 kg, then 2 per extra kilo."""
    if weight_kg <= 2:
        return 5
    return 5 + (weight_kg - 2) * 2


def with_tax(amount, rate=0.2):
    return round(amount * (1 + rate), 2)


def format_price(cents):
    return f"${cents // 100}.{cents % 100:02d}"
