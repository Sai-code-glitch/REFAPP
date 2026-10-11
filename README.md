# REFAPP: QR inventory tracker

Track stock by scanning QR codes with your phone. Each product gets a printable QR label; scan it to add stock (deliveries) or remove stock (sales, use). Every change is recorded, and products below their alert level are flagged.

It runs as a mobile web app (open the site on your phone, no install) and exposes a JSON API for a native app.

## Pages

| Page | What it does |
|---|---|
| `/` | Stock list with search, low-stock filter, quick +1 / −1, add and delete products |
| `/scan` | Phone camera scanner. Pick **Stock in** or **Stock out**, set a quantity, and point at a label |
| `/labels` | Printable QR labels for every product |

The camera needs HTTPS (Render provides it). If the camera is unavailable you can type a SKU instead.

## API

| Method | Path | Body / query | Notes |
|---|---|---|---|
| GET | `/api/products` | `?q=` search, `?low=1` low stock only | |
| POST | `/api/products` | `{product_name, sku?, stock?, low_stock_threshold?}` | SKU is generated (`P00001`) if omitted |
| GET | `/api/products/<id>` | | Includes the last 20 stock movements |
| PATCH | `/api/products/<id>` | `{product_name?, stock?, low_stock_threshold?, note?}` | Stock edits are logged as adjustments |
| DELETE | `/api/products/<id>` | | |
| GET | `/api/products/<id>/qr.png` | | QR encodes `REFAPP:<sku>` |
| POST | `/api/scan` | `{code, action: "in" \| "out", quantity?, note?}` | `code` can be the QR text, a SKU or an id. Stock never goes below 0 (409) |
| GET | `/api/movements` | `?limit=50` | Newest first |
| GET | `/health` | | For uptime checks |

The original `GET /inventory` and `POST /scan {product_id}` endpoints still work.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python app.py            # http://localhost:5000
python -m pytest -q      # run the tests
```

Without `DATABASE_URL`, data is stored in a local SQLite file (`instance/inventory.db`).

## Deploy on Render

1. Create a free Postgres database (Neon, Supabase or Render) and copy its connection string.
2. In the Render web service, set environment variables:
   - `DATABASE_URL`: the connection string (`postgres://` or `postgresql://` both work)
   - `API_KEY` (recommended): any secret. Adding, editing and scanning then require it; the web app asks for it once per device.
3. Build command: `pip install -r requirements.txt`. Start command comes from the `Procfile`.

Tables are created automatically on first start.
