from datetime import date

from newsintel.core.dates import find_dates, fiscal_label
from newsintel.core.fingerprints import exact_fingerprint, round_amount, structural_fingerprint
from newsintel.core.money import find_all_money, format_money, parse_money


def test_parse_money_western_and_indian():
    assert parse_money("valued at approximately $1.1 billion").amount == 1_100_000_000
    assert parse_money("worth ₹5,000 crore").amount == 50_000_000_000
    assert parse_money("Rs. 250 crore").currency == "INR"
    assert parse_money("£450m").amount == 450_000_000
    assert parse_money("US$ 2.5bn").amount == 2_500_000_000
    assert parse_money("1,20,000 INR").amount == 120_000
    assert parse_money("no money here") is None


def test_money_formatting():
    assert format_money(1_100_000_000, "USD") == "$1.1 billion"
    assert format_money(120_000_000, "USD") == "$120 million"
    assert format_money(50_000_000_000, "INR") == "₹5000 crore"
    assert format_money(450_000_000, "GBP") == "£450 million"


def test_find_all_money_order():
    found = find_all_money("Revenue of $4.2 billion and net income of $512 million.")
    assert [m.amount for m in found] == [4_200_000_000, 512_000_000]


def test_dates_and_fiscal_labels():
    ds = find_dates("the quarter ended September 30, 2026 and 2026-10-01")
    assert ds[0].value == date(2026, 9, 30) and ds[1].value == date(2026, 10, 1)
    assert fiscal_label(date(2026, 3, 31), 4).startswith("fiscal Q4 FY26")
    assert fiscal_label(date(2026, 9, 30), 1).startswith("fiscal Q3 FY26")


def test_fingerprints():
    assert exact_fingerprint(b"a  b\n c") == exact_fingerprint(b"a b c")
    assert round_amount(1_098_000_000) == round_amount(1_100_000_000)
    assert structural_fingerprint("contract_award", "e1", 1_100_000_000, None) == structural_fingerprint("contract_award", "e1", 1_080_000_000, None)
    assert structural_fingerprint("earnings", "e1", 214_000_000, date(2026, 9, 30)) != structural_fingerprint("earnings", "e1", 241_000_000, date(2026, 9, 30))
