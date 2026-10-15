"""Back-office helpers that call the stock script, as the warehouse terminal does."""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def stock_report(sku):
    """The stock line for one SKU, e.g. 'MUG-01: 14 in stock'."""
    cmd = f'"{sys.executable}" -m shop.stock {sku}'
    out = subprocess.run(cmd, shell=True, cwd=ROOT, capture_output=True, text=True, timeout=30)
    return out.stdout.strip()
