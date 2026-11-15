"""Create random demo orders so the admin dashboard has something to show.

    python seed_orders.py            # add 80 demo orders spread over the last 60 days
    python seed_orders.py 150        # add 150
    python seed_orders.py --clear    # remove all demo orders (their stock is put back)

Demo orders are real orders in the database: they take stock and move through the normal
status flow. They are marked by a payment bill number starting with "DEMO", so --clear
removes only them and never touches real customer orders.
"""
import random
import secrets
import sys
from datetime import datetime, timedelta
from decimal import Decimal

from app import app, set_order_status
from models import Order, OrderItem, Payment, Product, db

DAYS = 60
NAMES = ["Sok Dara", "Chan Sophea", "Keo Visal", "Lim Sreyneang", "Heng Rotha", "Meas Bopha",
         "Vann Piseth", "Ouk Sreymom", "Chea Kimheng", "Ly Sovann", "Nhem Chanthy", "Pich Vuthy",
         "Seng Molika", "Touch Ratanak", "Yim Sokunthea"]
STREETS = ["St. 271, Toul Tompoung", "St. 63, BKK1", "Russian Blvd, Toul Kork", "St. 2004, Sen Sok",
           "Monivong Blvd, Daun Penh", "St. 598, Chroy Changvar"]
# how a demo order's status depends on its age (status: weight)
STATUS_BY_AGE = [
    (1, {"pending": 50, "paid": 40, "cancelled": 10}),
    (3, {"pending": 15, "paid": 40, "shipped": 35, "cancelled": 10}),
    (7, {"paid": 10, "shipped": 40, "delivered": 40, "cancelled": 10}),
    (DAYS + 1, {"shipped": 5, "delivered": 85, "cancelled": 10}),
]
PATH = {"pending": [], "paid": ["paid"], "shipped": ["paid", "shipped"],
        "delivered": ["paid", "shipped", "delivered"]}


def pick_status(age_days):
    for max_age, weights in STATUS_BY_AGE:
        if age_days < max_age:
            return random.choices(list(weights), weights=list(weights.values()))[0]


def demo_orders():
    return Order.query.join(Payment).filter(Payment.bill_number.like("DEMO%")).all()


def clear():
    orders = demo_orders()
    for o in orders:
        if o.status != "cancelled":  # cancelled orders already gave their stock back
            for item in o.items:
                if item.product is not None:
                    item.product.stock += item.quantity
        db.session.delete(o)
    db.session.commit()
    print(f"Removed {len(orders)} demo orders.")


def make_order(products, now):
    # more orders on recent days than on old ones
    age = timedelta(days=DAYS * random.random() ** 1.4)
    created = now - age
    lines = []
    for p in random.sample(products, k=min(len(products), random.choice([1, 1, 1, 2, 2, 3]))):
        qty = random.choice([1, 1, 1, 2, 2, 3])
        if p.stock - qty >= 2:  # leave a little stock so the store stays usable
            lines.append((p, qty))
    if not lines:
        return None

    name = random.choice(NAMES)
    total = sum((p.price * q for p, q in lines), Decimal(0))
    order = Order(customer_name=name, email=f"demo.{secrets.token_hex(3)}@example.com",
                  phone="0" + str(random.choice([10, 12, 15, 17, 70, 77, 86, 96])) + str(random.randint(100000, 999999)),
                  address=f"#{random.randint(1, 200)}, {random.choice(STREETS)}, Phnom Penh",
                  total=total, status="pending", created_at=created, updated_at=created)
    for p, qty in lines:
        p.stock -= qty
        order.items.append(OrderItem(product=p, product_name=p.name, unit_price=p.price, quantity=qty))
    order.payment = Payment(method="KHQR", amount=total, currency=app.config["KHQR_CURRENCY"],
                            bill_number="DEMO" + secrets.token_hex(4).upper(), status="unverified",
                            created_at=created)

    status = pick_status(age.days)
    steps = PATH.get(status) or random.choice([["cancelled"], ["paid", "cancelled"]])
    t = created
    for step in steps:
        set_order_status(order, step)
        # a little time passes between steps (never past "now")
        t = min(t + timedelta(hours=random.uniform(0.2, 30)), now)
        if step == "paid":
            order.payment.paid_at = t
    order.updated_at = t
    return order


def seed(count):
    products = Product.query.filter_by(status="active").all()
    if not products:
        sys.exit("No active products. Start the app once so the sample products are created.")
    now = datetime.utcnow()
    made = 0
    for _ in range(count * 3):  # a few tries more, in case some products run low
        order = make_order(products, now)
        if order is not None:
            db.session.add(order)
            made += 1
            if made == count:
                break
    db.session.commit()
    print(f"Added {made} demo orders over the last {DAYS} days. Open /admin/dashboard to see them.")


if __name__ == "__main__":
    with app.app_context():
        if "--clear" in sys.argv:
            clear()
        else:
            seed(int(next((a for a in sys.argv[1:] if a.isdigit()), 80)))
