"""Numbers for the admin dashboard (kept out of app.py so the routes stay short).

Dates are stored in UTC (datetime.utcnow). "Today" and the per-day chart use the shop's
local time: UTC + TZ_OFFSET_HOURS (Cambodia = +7, no daylight saving).

Revenue = orders whose status is in REVENUE_STATUSES (paid, shipped, delivered),
dated by when the order was placed.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal

from flask import current_app
from sqlalchemy import func

from models import (ORDER_STATUSES, REVENUE_STATUSES, STATUS_BADGE, Category, Order,
                    OrderItem, Product, db)

RANGES = {"7d": 7, "30d": 30, "90d": 90, "all": None}
RANGE_LABELS = {"7d": "7 days", "30d": "30 days", "90d": "90 days", "all": "All time"}
BADGE_HEX = {"warning": "#f59e0b", "info": "#0ea5e9", "primary": "#2563eb",
             "success": "#16a34a", "secondary": "#9ca3af"}
PENDING_ALERT_HOURS = 24  # pending this long -> "payment not checked"
SHIP_ALERT_HOURS = 48     # paid this long without shipping -> "not shipped yet"


def _offset():
    return timedelta(hours=current_app.config["TZ_OFFSET_HOURS"])


def local_date(dt):
    """Shop-local calendar date of a UTC datetime."""
    return (dt + _offset()).date()


def period_bounds(rng):
    """(start, days) for a range key. `start` is local midnight `days-1` days ago, in UTC.
    'all' starts on the day of the first order."""
    days = RANGES[rng]
    today = local_date(datetime.utcnow())
    if days is None:
        first = db.session.query(func.min(Order.created_at)).scalar()
        days = (today - local_date(first)).days + 1 if first else 1
    start = datetime.combine(today - timedelta(days=days - 1), time.min) - _offset()
    return start, days


def _between(q, start, end=None):
    q = q.filter(Order.created_at >= start)
    return q.filter(Order.created_at < end) if end else q


def _sales(start, end=None):
    """(revenue, number of paid orders, number of non-cancelled orders) in a period."""
    revenue, paid = _between(
        db.session.query(func.coalesce(func.sum(Order.total), 0), func.count(Order.id))
        .filter(Order.status.in_(REVENUE_STATUSES)), start, end).one()
    placed = _between(Order.query.filter(Order.status != "cancelled"), start, end).count()
    return Decimal(revenue), paid, placed


def pct_change(cur, prev):
    """Percent change, or None when there is nothing to compare with."""
    if not prev:
        return None
    return round((float(cur) - float(prev)) / float(prev) * 100, 1)


def _daily(start, days, end=None):
    """Revenue and order count per local day: two lists of length `days`."""
    first = local_date(start)
    revenue, orders = [Decimal(0)] * days, [0] * days
    rows = _between(db.session.query(Order.created_at, Order.total, Order.status)
                    .filter(Order.status != "cancelled"), start, end)
    for created, total, status in rows:
        i = (local_date(created) - first).days
        if 0 <= i < days:
            orders[i] += 1
            if status in REVENUE_STATUSES:
                revenue[i] += total
    return revenue, orders


def _waited(since, now):
    h = int((now - since).total_seconds() // 3600)
    return f"{h // 24} d" if h >= 48 else f"{h} h"


def _needs_action(now, limit=8):
    """Orders that have been waiting on the admin for too long, oldest first."""
    late_pending = Order.query.filter(
        Order.status == "pending",
        Order.created_at <= now - timedelta(hours=PENDING_ALERT_HOURS)).all()
    late_paid = Order.query.filter(
        Order.status == "paid",
        Order.updated_at <= now - timedelta(hours=SHIP_ALERT_HOURS)).all()
    rows = ([{"order": o, "reason": "Payment not checked", "since": o.created_at} for o in late_pending]
            + [{"order": o, "reason": "Paid, not shipped", "since": o.updated_at} for o in late_paid])
    rows.sort(key=lambda r: r["since"])
    for r in rows:
        r["waited"] = _waited(r["since"], now)
    return rows[:limit], len(rows)


def dashboard_stats(rng):
    now = datetime.utcnow()
    start, days = period_bounds(rng)
    has_prev = RANGES[rng] is not None
    prev_start = start - timedelta(days=days)

    revenue, paid, placed = _sales(start)
    revenue_prev, paid_prev, placed_prev = _sales(prev_start, start) if has_prev else (0, 0, 0)
    aov = revenue / paid if paid else Decimal(0)
    aov_prev = revenue_prev / paid_prev if paid_prev else 0

    # orders waiting on the admin right now (not limited to the range)
    queue = dict(db.session.query(Order.status, func.count(Order.id))
                 .filter(Order.status.in_(["pending", "paid"])).group_by(Order.status).all())
    needs_action, needs_action_total = _needs_action(now)

    threshold = current_app.config["LOW_STOCK_THRESHOLD"]
    low_q = Product.query.filter(Product.status == "active", Product.stock <= threshold)
    low_stock = low_q.order_by(Product.stock, Product.name).limit(8).all()

    item_revenue = func.sum(OrderItem.unit_price * OrderItem.quantity).label("revenue")
    units = func.sum(OrderItem.quantity).label("units")
    def sold(*cols):  # order items of paid/shipped/delivered orders placed in the range
        return _between(db.session.query(*cols).select_from(OrderItem)
                        .join(Order, OrderItem.order_id == Order.id)
                        .filter(Order.status.in_(REVENUE_STATUSES)), start)

    top_products = (sold(OrderItem.product_name, units, item_revenue)
                    .group_by(OrderItem.product_name).order_by(item_revenue.desc()).limit(5).all())
    # deleted products keep their order items (product_id = NULL) -> "Deleted products"
    cat_name = func.coalesce(Category.name, "Deleted products").label("name")
    by_category = (sold(cat_name, units, item_revenue)
                   .outerjoin(Product, OrderItem.product_id == Product.id)
                   .outerjoin(Category, Product.category_id == Category.id)
                   .group_by(cat_name).order_by(item_revenue.desc()).all())
    cat_total = sum((r.revenue for r in by_category), Decimal(0))

    status_counts = dict(_between(db.session.query(Order.status, func.count(Order.id)), start)
                         .group_by(Order.status).all())

    day_revenue, day_orders = _daily(start, days)
    prev_revenue = _daily(prev_start, days, start)[0] if has_prev else None
    first = local_date(start)
    chart = {
        "labels": [(first + timedelta(days=i)).strftime("%b %d") for i in range(days)],
        "revenue": [float(v) for v in day_revenue],
        "orders": day_orders,
        "prev_revenue": [float(v) for v in prev_revenue] if prev_revenue else None,
        "status": {"labels": [s.capitalize() for s in ORDER_STATUSES],
                   "counts": [status_counts.get(s, 0) for s in ORDER_STATUSES],
                   "colors": [BADGE_HEX[STATUS_BADGE[s]] for s in ORDER_STATUSES]},
    }

    return {
        "range": rng, "ranges": RANGE_LABELS, "days": days, "has_prev": has_prev,
        "period_from": first, "period_to": local_date(now),
        "revenue": revenue, "revenue_change": pct_change(revenue, revenue_prev) if has_prev else None,
        "placed": placed, "placed_change": pct_change(placed, placed_prev) if has_prev else None,
        "paid": paid, "aov": aov, "aov_change": pct_change(aov, aov_prev) if has_prev else None,
        "pending_count": queue.get("pending", 0), "to_ship_count": queue.get("paid", 0),
        "needs_action": needs_action, "needs_action_total": needs_action_total,
        "low_stock": low_stock, "low_stock_total": low_q.count(), "low_stock_threshold": threshold,
        "top_products": top_products, "by_category": by_category, "cat_total": cat_total,
        "recent_orders": Order.query.order_by(Order.created_at.desc()).limit(8).all(),
        "status_counts": status_counts, "chart": chart,
    }
