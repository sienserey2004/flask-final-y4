import io
import os
import re
import secrets
import time
import uuid
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from functools import wraps

import qrcode
from bakong_khqr import KHQR
from flask import (Flask, abort, flash, redirect, render_template, request,
                   send_file, session, url_for)
from sqlalchemy import func, update
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

from config import Config
from dashboard import RANGES, dashboard_stats
from models import (NEXT_STATUS, ORDER_STATUSES, REVENUE_STATUSES, AdminUser, Category,
                    Order, OrderItem, Payment, Product, db)


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(os.path.join(app.root_path, "instance"), exist_ok=True)
    db.init_app(app)

    with app.app_context():
        db.create_all()
        from seed import seed_if_empty
        seed_if_empty(app)
        ensure_admin(app)

    register_helpers(app)
    register_public(app)
    register_admin(app)
    return app


def ensure_admin(app):
    """Create the first admin account (from env vars) if none exists."""
    if AdminUser.query.count() == 0:
        u = AdminUser(username=app.config["ADMIN_USERNAME"])
        u.set_password(app.config["ADMIN_PASSWORD"])
        db.session.add(u)
        db.session.commit()


# --------------------------------------------------------------------------- helpers
def register_helpers(app):
    @app.before_request
    def csrf_protect():
        if request.method == "POST":
            token = session.get("_csrf")
            if not token or token != request.form.get("_csrf"):
                abort(400, "Invalid or missing CSRF token")

    @app.context_processor
    def inject_globals():
        if "_csrf" not in session:
            session["_csrf"] = secrets.token_hex(16)
        cart = session.get("cart", {})
        return {
            "csrf_token": session["_csrf"],
            "shop_name": app.config["SHOP_NAME"],
            "brand_color": app.config["BRAND_COLOR"],
            "theme_key": app.config["THEME_KEY"],
            # (name, active product count) for categories that have active products
            "categories": db.session.query(Category.name, func.count(Product.id))
                .join(Product, Product.category_id == Category.id)
                .filter(Product.status == "active")
                .group_by(Category.id, Category.name).order_by(Category.name).all(),
            "cart_count": sum(cart.values()),
            "is_admin": bool(session.get("admin_id")),
            "admin_name": session.get("admin_name"),
        }

    @app.template_filter("cat_icon")
    def cat_icon(name):
        return app.config["CATEGORY_ICONS"].get(name, "bi-tag")

    @app.template_filter("money")
    def money(v):
        return "${:,.2f}".format(Decimal(v or 0))

    @app.errorhandler(404)
    def not_found(e):
        return render_template("error.html", code=404, message="Page not found"), 404

    @app.errorhandler(400)
    def bad_request(e):
        return render_template("error.html", code=400, message=e.description), 400

    @app.errorhandler(413)
    def too_large(e):
        return render_template("error.html", code=413, message="Image is too large (max 4 MB)"), 413


def cart_lines():
    """Return (lines, total) from the session cart, dropping invalid products."""
    cart = session.get("cart", {})
    lines, total = [], Decimal("0")
    changed = False
    for pid, qty in list(cart.items()):
        p = db.session.get(Product, int(pid))
        if not p or not p.is_active or p.stock <= 0:
            cart.pop(pid)
            changed = True
            continue
        if qty > p.stock:
            qty = cart[pid] = p.stock
            changed = True
        sub = p.price * qty
        total += sub
        lines.append({"product": p, "qty": qty, "subtotal": sub})
    if changed:
        session["cart"] = cart
    return lines, total


def get_or_create_category(name):
    """Find a category case-insensitively (so 'audio' and 'Audio' are the same)."""
    name = re.sub(r"\s+", " ", (name or "").strip())[:60] or "General"
    cat = Category.query.filter(func.lower(Category.name) == name.lower()).first()
    if not cat:
        cat = Category(name=name)
        db.session.add(cat)
        db.session.flush()
    return cat


def allowed_file(name):
    return "." in name and name.rsplit(".", 1)[1].lower() in Config.ALLOWED_EXT


def save_image(file_storage):
    if not file_storage or not file_storage.filename:
        return None
    if not allowed_file(file_storage.filename):
        raise ValueError("Image must be png, jpg, jpeg, gif or webp")
    ext = file_storage.filename.rsplit(".", 1)[1].lower()
    name = f"{uuid.uuid4().hex}.{ext}"
    file_storage.save(os.path.join(Config.UPLOAD_FOLDER, secure_filename(name)))
    return name


def delete_image(name):
    if name and not name.startswith("seed_"):
        try:
            os.remove(os.path.join(Config.UPLOAD_FOLDER, name))
        except OSError:
            pass


def build_khqr(app, amount, bill_number):
    khqr = KHQR(app.config["BAKONG_TOKEN"]) if app.config["BAKONG_TOKEN"] else KHQR()
    res = khqr.create_qr(
        amount=float(amount),
        account_id=app.config["KHQR_ACCOUNT_ID"],
        merchant_name=app.config["KHQR_MERCHANT_NAME"],
        merchant_city=app.config["KHQR_MERCHANT_CITY"],
        currency=app.config["KHQR_CURRENCY"],
        store_label=app.config["KHQR_MERCHANT_NAME"],
        phone_number=app.config["KHQR_PHONE"],
        bill_number=bill_number,
        terminal_label="Web",
        return_dict=True,
    )
    if res.get("responseCode") != 0:
        raise RuntimeError(res.get("responseMessage", "KHQR error"))
    return res["data"]["qr"], res["data"]["md5"]


def check_payment(app, md5):
    """'paid' / 'unpaid' when a Bakong token is set; 'unverified' in demo mode."""
    if not app.config["BAKONG_TOKEN"]:
        return "unverified"
    try:
        status = KHQR(app.config["BAKONG_TOKEN"]).check_payment(md5)
        return "paid" if (status == "PAID" or status is True) else "unpaid"
    except Exception:
        return "unpaid"


def set_order_status(order, new_status):
    """Move an order to a new status, keeping payment and stock consistent."""
    if new_status not in NEXT_STATUS.get(order.status, []):
        raise ValueError(f"Cannot change an order from {order.status} to {new_status}.")
    if new_status == "paid" and order.payment:
        order.payment.status = "paid"
        order.payment.paid_at = order.payment.paid_at or datetime.utcnow()
    if new_status == "cancelled":
        for item in order.items:  # put the stock back
            if item.product is not None:
                item.product.stock += item.quantity
        if order.payment:
            order.payment.status = "refunded" if order.payment.status == "paid" else "void"
    order.status = new_status


# --------------------------------------------------------------------------- public
_failed_tracks = defaultdict(list)  # ip -> [timestamps] of wrong "track my order" lookups
TRACK_MAX_FAILS, TRACK_WINDOW = 10, 600


def normalize_phone(v):
    """Digits only, without the Cambodian +855 prefix or a leading 0: '+855 12 345 678' == '012345678'."""
    d = re.sub(r"\D", "", v or "")
    if d.startswith("855"):
        d = d[3:]
    return d.lstrip("0")


def register_public(app):
    @app.route("/")
    def home():
        active = Product.query.filter_by(status="active").order_by(Product.id.desc()).all()
        deals = sorted([p for p in active if p.discount_pct], key=lambda p: -p.discount_pct)[:8]
        featured = [p for p in active if p.featured][:8] or active[:8]
        # one picture per category (for image category tiles)
        cat_images = {}
        for p in reversed(active):
            if p.image:
                cat_images.setdefault(p.category, p.image)
        banner = os.path.join(app.config["UPLOAD_FOLDER"], "seed_banner_hero.jpg")
        return render_template("home.html", new_arrivals=active[:8], deals=deals,
                               featured=featured, hero=(featured[0] if featured else None),
                               total_products=len(active), cat_images=cat_images,
                               hero_banner="seed_banner_hero.jpg" if os.path.exists(banner) else None)

    @app.route("/shop")
    def shop():
        q = request.args.get("q", "").strip()
        cat = request.args.get("category", "").strip()
        sort = request.args.get("sort", "new")
        deals = request.args.get("deals") == "1"
        query = Product.query.filter(Product.status == "active")
        if q:
            query = query.filter(Product.name.ilike(f"%{q}%") | Product.description.ilike(f"%{q}%"))
        if cat:
            query = query.join(Category).filter(Category.name == cat)
        if deals:
            query = query.filter(Product.old_price.isnot(None), Product.old_price > Product.price)
        order = {"price_asc": Product.price.asc(), "price_desc": Product.price.desc(),
                 "name": Product.name.asc()}.get(sort, Product.id.desc())
        return render_template("shop.html", products=query.order_by(order).all(),
                               q=q, cat=cat, sort=sort, deals=deals)

    @app.route("/product/<int:pid>")
    def product_detail(pid):
        p = db.get_or_404(Product, pid)
        if not p.is_active:
            abort(404)
        link = url_for("product_detail", pid=p.id, _external=True)
        related = (Product.query.filter(Product.status == "active", Product.id != p.id,
                                        Product.category_id == p.category_id)
                   .order_by(Product.id.desc()).limit(4).all())
        return render_template("product_detail.html", p=p, link=link, related=related)

    @app.post("/cart/add/<int:pid>")
    def cart_add(pid):
        p = db.get_or_404(Product, pid)
        try:
            qty = max(1, int(request.form.get("quantity", 1)))
        except ValueError:
            qty = 1
        if not p.is_active or p.stock <= 0:
            flash("Sorry, that product is not available.", "danger")
            return redirect(request.referrer or url_for("home"))
        cart = session.get("cart", {})
        cart[str(pid)] = min(cart.get(str(pid), 0) + qty, p.stock)
        session["cart"] = cart
        flash(f"Added {p.name} to your cart.", "success")
        if request.form.get("next") == "cart":
            return redirect(url_for("cart"))
        return redirect(request.referrer or url_for("home"))

    @app.route("/cart")
    def cart():
        lines, total = cart_lines()
        return render_template("cart.html", lines=lines, total=total)

    @app.post("/cart/update")
    def cart_update():
        cart = session.get("cart", {})
        for key, val in request.form.items():
            if not key.startswith("qty_"):
                continue
            pid = key[4:]
            p = db.session.get(Product, int(pid)) if pid.isdigit() else None
            try:
                qty = int(val)
            except ValueError:
                continue
            if qty <= 0 or not p:
                cart.pop(pid, None)
            else:
                cart[pid] = min(qty, p.stock)
        session["cart"] = cart
        flash("Cart updated.", "success")
        return redirect(url_for("cart"))

    @app.post("/cart/remove/<int:pid>")
    def cart_remove(pid):
        cart = session.get("cart", {})
        cart.pop(str(pid), None)
        session["cart"] = cart
        flash("Item removed.", "info")
        return redirect(url_for("cart"))

    @app.route("/checkout", methods=["GET", "POST"])
    def checkout():
        lines, total = cart_lines()
        if not lines:
            flash("Your cart is empty.", "info")
            return redirect(url_for("cart"))

        ref = session.setdefault("checkout_ref", "ORD" + secrets.token_hex(5).upper())
        _, md5 = build_khqr(app, total, ref)

        form = {k: request.form.get(k, "").strip()
                for k in ("full_name", "email", "phone", "address")}
        errors = {}
        if request.method == "POST":
            if not form["full_name"]:
                errors["full_name"] = "Full name is required."
            if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", form["email"]):
                errors["email"] = "Enter a valid email address."
            if not re.match(r"^[+\d][\d\s\-]{6,18}$", form["phone"]):
                errors["phone"] = "Enter a valid phone number."
            if not form["address"]:
                errors["address"] = "Shipping address is required."

            if not errors:
                pay_status = check_payment(app, md5)
                if pay_status == "unpaid":
                    flash("We could not confirm your KHQR payment yet. "
                          "Please complete the payment and try again.", "warning")
                else:
                    # Reserve stock atomically: the UPDATE only succeeds if enough stock
                    # is left, so two buyers can never both get the last item.
                    for ln in lines:
                        p, qty = ln["product"], ln["qty"]
                        res = db.session.execute(
                            update(Product)
                            .where(Product.id == p.id, Product.stock >= qty,
                                   Product.status == "active")
                            .values(stock=Product.stock - qty)
                            .execution_options(synchronize_session=False))
                        if res.rowcount != 1:
                            db.session.rollback()
                            flash(f"Sorry, “{p.name}” just sold out or has less stock "
                                  "than you ordered. Please review your cart.", "danger")
                            return redirect(url_for("cart"))
                    paid = pay_status == "paid"
                    order = Order(customer_name=form["full_name"], email=form["email"],
                                  phone=form["phone"], address=form["address"],
                                  total=total, status="paid" if paid else "pending")
                    for ln in lines:
                        p = ln["product"]
                        order.items.append(OrderItem(
                            product_id=p.id, product_name=p.name,
                            unit_price=p.price, quantity=ln["qty"]))
                    order.payment = Payment(
                        method="KHQR", amount=total, currency=app.config["KHQR_CURRENCY"],
                        bill_number=ref, khqr_md5=md5,
                        status="paid" if paid else "unverified",
                        paid_at=datetime.utcnow() if paid else None)
                    db.session.add(order)
                    db.session.commit()
                    session.pop("cart", None)
                    session.pop("checkout_ref", None)
                    session["last_order"] = order.id
                    session["my_orders"] = (session.get("my_orders", []) + [order.id])[-50:]
                    return redirect(url_for("order_confirmation", oid=order.id))

        return render_template("checkout.html", lines=lines, total=total,
                               form=form, errors=errors, ref=ref,
                               demo_mode=not app.config["BAKONG_TOKEN"])

    @app.route("/khqr.png")
    def khqr_png():
        _, total = cart_lines()
        ref = session.get("checkout_ref")
        if total <= 0 or not ref:
            abort(404)
        qr_string, _ = build_khqr(app, total, ref)
        img = qrcode.make(qr_string, box_size=8, border=2)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)
        resp = send_file(buf, mimetype="image/png")
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.route("/order/<int:oid>/confirmation")
    def order_confirmation(oid):
        if session.get("last_order") != oid and not session.get("admin_id"):
            abort(404)
        order = db.get_or_404(Order, oid)
        return render_template("confirmation.html", order=order)

    def my_order_ids():
        ids = list(session.get("my_orders", []))
        if session.get("last_order") and session["last_order"] not in ids:
            ids.append(session["last_order"])
        return ids

    @app.route("/track", methods=["GET", "POST"])
    def track_order():
        form = {"order_id": request.form.get("order_id", "").strip(),
                "phone": request.form.get("phone", "").strip()}
        status = 200
        if request.method == "POST":
            ip = request.remote_addr or "?"
            now = time.time()
            _failed_tracks[ip] = [t for t in _failed_tracks[ip] if now - t < TRACK_WINDOW]
            oid = form["order_id"].lstrip("#").strip()
            order = db.session.get(Order, int(oid)) if oid.isdigit() else None
            phone = normalize_phone(form["phone"])
            if len(_failed_tracks[ip]) >= TRACK_MAX_FAILS:
                flash("Too many attempts. Please wait a few minutes and try again.", "danger")
                status = 429
            elif order and phone and normalize_phone(order.phone) == phone:
                # Proven owner of this phone number: show every order placed with it.
                same_phone = [i for i, ph in db.session.query(Order.id, Order.phone)
                              if normalize_phone(ph) == phone]
                session["my_orders"] = sorted(set(my_order_ids()) | set(same_phone))[-50:]
                flash(f"Found {len(same_phone)} order(s) for this phone number.", "success")
                return redirect(url_for("track_order"))
            else:
                _failed_tracks[ip].append(now)
                flash("We couldn't find an order with that order number and phone number.", "warning")
        ids = my_order_ids()
        orders = (Order.query.filter(Order.id.in_(ids)).order_by(Order.created_at.desc()).all()
                  if ids else [])
        return render_template("track.html", form=form, orders=orders), status

    @app.post("/track/forget")
    def track_forget():
        session.pop("my_orders", None)
        session.pop("last_order", None)
        flash("Your orders were removed from this browser.", "info")
        return redirect(url_for("track_order"))

    @app.route("/track/<int:oid>")
    def track_result(oid):
        if oid not in my_order_ids() and not session.get("admin_id"):
            return redirect(url_for("track_order"))
        order = db.get_or_404(Order, oid)
        return render_template("track.html", order=order, form={})


# --------------------------------------------------------------------------- admin
_failed_logins = defaultdict(list)  # ip -> [timestamps]; simple in-memory rate limit
MAX_FAILS, WINDOW = 5, 600


def admin_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not session.get("admin_id") or not db.session.get(AdminUser, session["admin_id"]):
            session.pop("admin_id", None)
            return redirect(url_for("admin_login", next=request.path))
        return f(*a, **kw)
    return wrapper


def register_admin(app):
    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        if request.method == "POST":
            ip = request.remote_addr or "?"
            now = time.time()
            _failed_logins[ip] = [t for t in _failed_logins[ip] if now - t < WINDOW]
            if len(_failed_logins[ip]) >= MAX_FAILS:
                wait = int((WINDOW - (now - _failed_logins[ip][0])) // 60) + 1
                flash(f"Too many failed attempts. Try again in {wait} minute(s).", "danger")
                return render_template("admin/login.html"), 429
            user = AdminUser.query.filter_by(username=request.form.get("username", "").strip()).first()
            if user and user.check_password(request.form.get("password", "")):
                _failed_logins.pop(ip, None)
                session.clear()
                session["admin_id"] = user.id
                session["admin_name"] = user.username
                user.last_login = datetime.utcnow()
                db.session.commit()
                nxt = request.args.get("next", "")
                return redirect(nxt if nxt.startswith("/admin") else url_for("admin_dashboard"))
            _failed_logins[ip].append(now)
            flash("Invalid username or password.", "danger")
        return render_template("admin/login.html")

    @app.post("/admin/logout")
    def admin_logout():
        session.clear()
        return redirect(url_for("home"))

    @app.route("/admin/password", methods=["GET", "POST"])
    @admin_required
    def admin_password():
        if request.method == "POST":
            user = db.session.get(AdminUser, session["admin_id"])
            cur, new, confirm = (request.form.get(k, "") for k in ("current", "new", "confirm"))
            if not user.check_password(cur):
                flash("Current password is incorrect.", "danger")
            elif len(new) < 8:
                flash("New password must be at least 8 characters.", "danger")
            elif new != confirm:
                flash("New passwords do not match.", "danger")
            else:
                user.set_password(new)
                db.session.commit()
                flash("Password changed.", "success")
                return redirect(url_for("admin_dashboard"))
        return render_template("admin/password.html")

    @app.route("/admin")
    @admin_required
    def admin_index():
        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/dashboard")
    @admin_required
    def admin_dashboard():
        rng = request.args.get("range", "30d")
        if rng not in RANGES:
            rng = "30d"
        return render_template("admin/dashboard.html", s=dashboard_stats(rng))

    # ---- products
    @app.route("/admin/products")
    @admin_required
    def admin_products():
        products = Product.query.order_by(Product.id).all()
        return render_template("admin/products.html", products=products)

    def parse_product_form():
        f = request.form
        errors = []
        data = {"name": f.get("name", "").strip(),
                "description": f.get("description", "").strip(),
                "status": f.get("status", "active"),
                "featured": f.get("featured") == "on",
                "old_price": None}
        cat_name = f.get("category", "").strip()
        if not data["name"]:
            errors.append("Name is required.")
        if not cat_name:
            errors.append("Category is required.")
        try:
            data["price"] = Decimal(f.get("price", "")).quantize(Decimal("0.01"))
            if data["price"] < 0:
                raise ValueError
        except Exception:
            errors.append("Price must be a valid non-negative number.")
        if f.get("old_price", "").strip():
            try:
                data["old_price"] = Decimal(f["old_price"]).quantize(Decimal("0.01"))
                if data["old_price"] < 0:
                    raise ValueError
            except Exception:
                errors.append("Old price must be a valid number.")
        try:
            data["stock"] = int(f.get("stock", ""))
            if data["stock"] < 0:
                raise ValueError
        except ValueError:
            errors.append("Stock must be a whole number, 0 or more.")
        if data["status"] not in ("active", "inactive"):
            data["status"] = "active"
        return data, cat_name, errors

    def product_form_page(p, form):
        return render_template("admin/product_form.html", p=p, form=form,
                               all_categories=Category.query.order_by(Category.name).all())

    @app.route("/admin/products/new", methods=["GET", "POST"])
    @admin_required
    def admin_product_new():
        if request.method == "POST":
            data, cat_name, errors = parse_product_form()
            image = None
            if not errors:
                try:
                    image = save_image(request.files.get("image"))
                except ValueError as e:
                    errors.append(str(e))
            if errors:
                for e in errors:
                    flash(e, "danger")
                return product_form_page(None, request.form)
            p = Product(image=image, category_ref=get_or_create_category(cat_name), **data)
            db.session.add(p)
            db.session.commit()
            flash("Product created.", "success")
            return redirect(url_for("admin_products"))
        return product_form_page(None, {})

    @app.route("/admin/products/<int:pid>/edit", methods=["GET", "POST"])
    @admin_required
    def admin_product_edit(pid):
        p = db.get_or_404(Product, pid)
        if request.method == "POST":
            data, cat_name, errors = parse_product_form()
            new_image = None
            if not errors:
                try:
                    new_image = save_image(request.files.get("image"))
                except ValueError as e:
                    errors.append(str(e))
            if errors:
                for e in errors:
                    flash(e, "danger")
                return product_form_page(p, request.form)
            for k, v in data.items():
                setattr(p, k, v)
            p.category_ref = get_or_create_category(cat_name)
            if new_image:
                delete_image(p.image)
                p.image = new_image
            db.session.commit()
            flash("Product updated.", "success")
            return redirect(url_for("admin_products"))
        return product_form_page(p, {})

    @app.post("/admin/products/<int:pid>/delete")
    @admin_required
    def admin_product_delete(pid):
        p = db.get_or_404(Product, pid)
        name = p.name
        delete_image(p.image)
        db.session.delete(p)  # order items keep their name/price snapshot; product_id -> NULL
        db.session.commit()
        flash(f"Deleted “{name}”.", "success")
        return redirect(url_for("admin_products"))

    # ---- categories
    @app.route("/admin/categories", methods=["GET", "POST"])
    @admin_required
    def admin_categories():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            if not name:
                flash("Category name is required.", "danger")
            elif Category.query.filter(func.lower(Category.name) == name.lower()).first():
                flash(f"Category “{name}” already exists.", "warning")
            else:
                get_or_create_category(name)
                db.session.commit()
                flash(f"Category “{name}” added.", "success")
            return redirect(url_for("admin_categories"))
        rows = (db.session.query(Category, func.count(Product.id))
                .outerjoin(Product, Product.category_id == Category.id)
                .group_by(Category.id).order_by(Category.name).all())
        return render_template("admin/categories.html", rows=rows)

    @app.post("/admin/categories/<int:cid>/rename")
    @admin_required
    def admin_category_rename(cid):
        c = db.get_or_404(Category, cid)
        name = re.sub(r"\s+", " ", request.form.get("name", "").strip())[:60]
        clash = Category.query.filter(func.lower(Category.name) == name.lower(), Category.id != cid).first()
        if not name:
            flash("Category name is required.", "danger")
        elif clash:
            flash(f"Another category is already called “{name}”.", "warning")
        else:
            c.name = name
            db.session.commit()
            flash("Category renamed.", "success")
        return redirect(url_for("admin_categories"))

    @app.post("/admin/categories/<int:cid>/delete")
    @admin_required
    def admin_category_delete(cid):
        c = db.get_or_404(Category, cid)
        if c.products:
            flash(f"“{c.name}” still has {len(c.products)} product(s). Move or delete them first.", "warning")
        else:
            db.session.delete(c)
            db.session.commit()
            flash(f"Category “{c.name}” deleted.", "success")
        return redirect(url_for("admin_categories"))

    # ---- orders
    @app.route("/admin/orders")
    @admin_required
    def admin_orders():
        status = request.args.get("status", "")
        q = Order.query
        if status in ORDER_STATUSES:
            q = q.filter_by(status=status)
        orders = q.order_by(Order.created_at.desc()).all()
        counts = dict(db.session.query(Order.status, func.count(Order.id)).group_by(Order.status).all())
        revenue = (db.session.query(func.coalesce(func.sum(Order.total), 0))
                   .filter(Order.status.in_(REVENUE_STATUSES)).scalar())
        return render_template("admin/orders.html", orders=orders, status=status,
                               counts=counts, revenue=revenue, statuses=ORDER_STATUSES)

    @app.route("/admin/orders/<int:oid>")
    @admin_required
    def admin_order_detail(oid):
        order = db.get_or_404(Order, oid)
        return render_template("admin/order_detail.html", order=order)

    @app.post("/admin/orders/<int:oid>/status")
    @admin_required
    def admin_order_status(oid):
        order = db.get_or_404(Order, oid)
        try:
            set_order_status(order, request.form.get("status", ""))
            db.session.commit()
            flash(f"Order #{order.id} is now {order.status}.", "success")
        except ValueError as e:
            flash(str(e), "danger")
        return redirect(url_for("admin_order_detail", oid=oid))


app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
