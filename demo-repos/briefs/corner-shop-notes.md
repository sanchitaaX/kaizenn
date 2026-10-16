# Corner Shop: notes for SecureFix

Rough notes from the shop team about the order-desk toolkit (repository: securefix-demo-shop).

## Who uses it
- Order-desk staff look customers up by their username when someone phones in.
- Customers download their invoices from the website by the invoice file name, e.g. INV-1001.txt.
- The warehouse terminal asks for a stock line for one SKU before packing an order.

## How the parts work
- shop/db.py: customer records live in SQLite (table customers: id, username, email, tier).
  find_customer(conn, username) must match the username exactly ("ali" must not find "alice").
- shop/files.py: invoices are plain text files in shop/invoices/. read_invoice(name) returns the text,
  list_invoices() returns the .txt names sorted. Customers may only ever get files from that folder.
  A missing invoice must still raise an error (the website shows "invoice not found").
- shop/tools.py: stock_report(sku) runs the stock script (python -m shop.stock <sku>) and returns one line
  like "MUG-01: 14 in stock". An unknown SKU reports 0 in stock. SKUs come from a barcode scanner,
  so they are normally like MUG-01, TEE-02, CAP-03.

## Must keep working
- Exact username lookups, including for gold and standard customers.
- The invoice list shown on the account page (INV-1001.txt, INV-1002.txt).
- The stock line format "<SKU>: <n> in stock"; the warehouse screen parses it.

## Not in scope
- No sign-in in this toolkit; the website in front of it handles accounts.
