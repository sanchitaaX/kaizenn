"""Stock levels, printed by `python -m shop.stock <sku>`."""

import sys

STOCK = {"MUG-01": 14, "TEE-02": 3, "CAP-03": 0}


def main(argv):
    sku = argv[0] if argv else ""
    print(f"{sku}: {STOCK.get(sku, 0)} in stock")


if __name__ == "__main__":
    main(sys.argv[1:])
