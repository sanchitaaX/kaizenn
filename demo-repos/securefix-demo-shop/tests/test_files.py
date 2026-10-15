import pytest

from shop.files import list_invoices, read_invoice


def test_lists_invoices():
    assert list_invoices() == ["INV-1001.txt", "INV-1002.txt"]


def test_reads_an_invoice():
    text = read_invoice("INV-1001.txt")
    assert text.startswith("Invoice INV-1001") and "Customer: alice" in text


def test_missing_invoice_raises():
    with pytest.raises(OSError):
        read_invoice("INV-9999.txt")
