"""Database models.

ERD (see docs/ERD.md):
    Category 1 ── * Product
    Product  1 ── * OrderItem   (product_id is nullable: deleting a product keeps order history)
    Order    1 ── * OrderItem
    Order    1 ── 1 Payment
    AdminUser       (stand-alone: admin accounts with hashed passwords)
"""
from datetime import datetime

from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()

# order life-cycle and the moves an admin may make from each state
ORDER_STATUSES = ["pending", "paid", "shipped", "delivered", "cancelled"]
NEXT_STATUS = {
    "pending": ["paid", "cancelled"],
    "paid": ["shipped", "cancelled"],
    "shipped": ["delivered"],
    "delivered": [],
    "cancelled": [],
}
STATUS_BADGE = {"pending": "warning", "paid": "info", "shipped": "primary",
                "delivered": "success", "cancelled": "secondary"}
PAYMENT_STATUSES = ["unverified", "paid", "refunded", "void"]


class Category(db.Model):
    __tablename__ = "category"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    products = db.relationship("Product", back_populates="category_ref")


class Product(db.Model):
    __tablename__ = "product"
    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey("category.id"), nullable=False, index=True)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    price = db.Column(db.Numeric(10, 2), nullable=False)
    old_price = db.Column(db.Numeric(10, 2))  # previous price -> shows a discount
    stock = db.Column(db.Integer, nullable=False, default=0)
    image = db.Column(db.String(255))
    status = db.Column(db.String(10), nullable=False, default="active")  # active / inactive
    featured = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    category_ref = db.relationship("Category", back_populates="products")
    order_items = db.relationship("OrderItem", back_populates="product")

    @property
    def category(self):
        """Category name (templates use p.category as text)."""
        return self.category_ref.name if self.category_ref else "General"

    @property
    def is_active(self):
        return self.status == "active"

    @property
    def in_stock(self):
        return self.stock > 0

    @property
    def discount_pct(self):
        if self.old_price and self.old_price > self.price:
            return int(round((1 - self.price / self.old_price) * 100))
        return 0


class Order(db.Model):
    __tablename__ = "order"
    id = db.Column(db.Integer, primary_key=True)
    customer_name = db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(150), nullable=False)
    phone = db.Column(db.String(40), nullable=False)
    address = db.Column(db.Text, nullable=False)
    total = db.Column(db.Numeric(10, 2), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    items = db.relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    payment = db.relationship("Payment", back_populates="order", uselist=False,
                              cascade="all, delete-orphan")

    @property
    def status_badge(self):
        return STATUS_BADGE.get(self.status, "secondary")

    @property
    def next_statuses(self):
        return NEXT_STATUS.get(self.status, [])


class OrderItem(db.Model):
    __tablename__ = "order_item"
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id", ondelete="SET NULL"),
                           nullable=True, index=True)
    product_name = db.Column(db.String(150), nullable=False)  # snapshot at time of purchase
    unit_price = db.Column(db.Numeric(10, 2), nullable=False)  # snapshot at time of purchase
    quantity = db.Column(db.Integer, nullable=False)

    order = db.relationship("Order", back_populates="items")
    product = db.relationship("Product", back_populates="order_items")

    @property
    def subtotal(self):
        return self.unit_price * self.quantity


class Payment(db.Model):
    __tablename__ = "payment"
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False, unique=True)
    method = db.Column(db.String(20), nullable=False, default="KHQR")
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    currency = db.Column(db.String(3), nullable=False, default="USD")
    bill_number = db.Column(db.String(40))
    khqr_md5 = db.Column(db.String(64))
    status = db.Column(db.String(20), nullable=False, default="unverified")
    paid_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    order = db.relationship("Order", back_populates="payment")


class AdminUser(db.Model):
    __tablename__ = "admin_user"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)
