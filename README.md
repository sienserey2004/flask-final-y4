# Angkor Wellness — Mini E-commerce (Final Project ADV PP)

Flask + SQLAlchemy web store: browse products, cart, guest checkout with **KHQR** payment,
and an admin panel for products, categories and orders.

## Run locally
```bash
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
python download_images.py                          # optional: real product photos (if this store has them)
python app.py                                      # http://127.0.0.1:5000
```
Optional demo data for the admin dashboard: `python seed_orders.py` adds 80 random orders over the last 60 days
(`python seed_orders.py --clear` removes them again and puts their stock back).
On first run the database, the categories, the sample products and the first admin account are created automatically.
To start over, delete `instance/shop.db` and restart.

## Admin
- URL: `/admin/login` — default login `admin` / `admin123`
- Set `ADMIN_USERNAME` / `ADMIN_PASSWORD` **before the first run** on your server, or change it in **Admin → Change password**.
  Passwords are stored as a hash, and login is rate-limited (5 failed tries per 10 minutes).

## Features
**Customer:** home page with hero, categories, deals, featured and new arrivals · shop page with category filter, search, sort and "hot deals" ·
product detail with stock, quantity, related products, Facebook/Telegram share · cart (update/remove) · guest checkout with KHQR · order confirmation · **My orders** (order history with status timeline and item details; on a new device, one order number + phone shows all orders for that phone).

**Admin:** dashboard (revenue, orders and average order value vs the previous period, revenue-per-day and status charts,
recent orders, overdue orders that need action, low stock, top products, sales by category; 7 / 30 / 90 days or all time) ·
orders list (revenue, counts, filter by status) · order detail with payment info and status actions
(*Mark payment received → Mark as shipped → Mark as delivered*, or *Cancel* which returns stock) ·
products (add/edit/delete, image upload, category, old price for discounts, featured, active/inactive) ·
categories (add/rename/delete) · change password.

## Database
6 tables — `category`, `product`, `order`, `order_item`, `payment`, `admin_user`. See **[docs/ERD.md](docs/ERD.md)** for the diagram and relationships.

## Configuration (environment variables)
| Variable | Purpose | Default |
|---|---|---|
| `SHOP_NAME` | Store name shown in the UI | Angkor Wellness |
| `SECRET_KEY` | Session signing key | dev value (set your own!) |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | First admin account (created on first run) | admin / admin123 |
| `KHQR_ACCOUNT_ID` | Your Bakong account, e.g. `name@bank` | placeholder — **change it** |
| `KHQR_MERCHANT_NAME` / `KHQR_MERCHANT_CITY` | Shown in the payer's bank app | store name / Phnom Penh |
| `KHQR_CURRENCY` | `USD` or `KHR` | USD |
| `BAKONG_TOKEN` | Optional Bakong API token — verifies payments automatically | empty (demo mode) |
| `TEST_PAYMENTS` | `1` shows a **Test payment** button at checkout that places the order as paid without scanning (testing only, never on a real shop) | 0 |
| `DATABASE_URL` | Optional (e.g. Postgres). Defaults to SQLite in `instance/` | SQLite |
| `LOW_STOCK_THRESHOLD` | Dashboard lists active products with this many or fewer in stock | 5 |
| `TZ_OFFSET_HOURS` | Shop's time zone vs UTC, used for "per day" numbers on the dashboard | 7 (Cambodia) |
| `SEED_DOWNLOAD_IMAGES` | `0` to skip downloading photos on first run | 1 |
| `BUCKET` / `REGION` | S3 bucket for product images. The database stores only the file path (e.g. `uploads/abc.jpg`) | empty (images in `static/uploads/`) |
| `AWS_ACCESS_KEY` / `AWS_SECRET_KEY` | Credentials that can put/delete objects in the bucket | empty |
| `ENDPOINT_STORAGE_URL` | Public address of the bucket (used when `S3_PUBLIC_READ=1`) | `<BUCKET>.s3.<REGION>.amazonaws.com` |
| `S3_PUBLIC_READ` | `1` if the bucket allows public read (plain links); `0` gives signed links that work with a private bucket | 0 |

**Images on S3:** with `BUCKET` and the AWS keys set, uploads go to the bucket under `uploads/` and seed photos are
uploaded on first run. The bucket can stay private: pages use signed links (valid 7 days, renewed automatically).
To move an existing install's images into the bucket, run `python migrate_images.py` once.

**KHQR:** set `KHQR_ACCOUNT_ID` to your real Bakong account so the QR pays you. Without `BAKONG_TOKEN` the app runs in
*demo mode*: orders start as **pending** and the admin confirms the payment manually.

## Deploy (Render)
1. Push to GitHub. 2. Render → New → Blueprint (uses `render.yaml`) or a Web Service with start command `gunicorn app:app --preload`.
3. Set `ADMIN_PASSWORD` and `KHQR_ACCOUNT_ID`.
Note: free hosts have an ephemeral disk, so SQLite data resets on redeploy (and uploaded images too, unless they are on S3). Use a persistent disk or `DATABASE_URL` (Postgres) for permanent data.
# flask-final-y4
# flask-final-y4
