# Corner Shop

A tiny order-desk toolkit for a small online store, written with the Python standard library only.

- `shop/db.py` keeps customer records in SQLite and looks customers up by username.
- `shop/files.py` serves invoice text files from `shop/invoices/` by name.
- `shop/tools.py` asks the stock script (`python -m shop.stock <sku>`) for a stock line, as the warehouse terminal does.

## Run the tests

No packages to install beyond pytest:

```
python -m pytest -q
```
