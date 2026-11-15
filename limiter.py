"""Rate limiting (Flask-Limiter): caps how many requests one client IP can make.

Defaults apply to every page; stricter limits are set on routes that are worth
abusing (login, checkout, order tracking...). Settings live in config.py:
RATELIMIT_DEFAULT, RATELIMIT_STORAGE_URI, RATELIMIT_ENABLED.
"""
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# The client IP. Behind Render's proxy this is correct because app.py uses ProxyFix.
limiter = Limiter(key_func=get_remote_address)

# Route-specific limits (used as @limiter.limit(...) in app.py)
LOGIN_LIMIT = "5 per minute;30 per hour"     # admin login: stops password guessing
PASSWORD_LIMIT = "5 per minute"              # admin change password
CHECKOUT_LIMIT = "10 per minute"             # placing an order (each one asks Bakong)
TRACK_LIMIT = "10 per minute"                # order lookup: stops guessing order ids
CART_LIMIT = "60 per minute"                 # add / update / remove cart items
