from shop.tools import stock_report


def test_stock_for_known_sku():
    assert stock_report("MUG-01") == "MUG-01: 14 in stock"


def test_out_of_stock_sku():
    assert stock_report("CAP-03") == "CAP-03: 0 in stock"


def test_unknown_sku_reports_zero():
    assert stock_report("NOPE-99") == "NOPE-99: 0 in stock"
