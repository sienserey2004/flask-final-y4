"""Seed the database with sample categories and products on first run.

Product pictures are resolved in this order:
  1. seed_images/<file>        (pre-downloaded photos committed to the repo)
  2. Unsplash download          (if the product has an "unsplash" photo id and internet works)
  3. a generated placeholder    (so the app always works offline)
"""
import io
import os
import tempfile
import urllib.request
from decimal import Decimal

from PIL import Image, ImageDraw, ImageFont

import storage
from models import Category, Product, db
import seed_data

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
SEED_IMAGES = os.path.join(BASE_DIR, "seed_images")


def _font(size):
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "C:/Windows/Fonts/arialbd.ttf", "/Library/Fonts/Arial Bold.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make_placeholder(path, title, color):
    """Simple generated picture (replace via Admin -> Edit product)."""
    w, h = 800, 800
    img = Image.new("RGB", (w, h), color)
    px = img.load()
    for y in range(h):
        row = tuple(int(c * (1 - 0.45 * y / h)) for c in color)
        for x in range(w):
            px[x, y] = row
    d = ImageDraw.Draw(img, "RGBA")
    d.ellipse([-120, -120, 420, 420], fill=(255, 255, 255, 38))
    d.ellipse([430, 380, 960, 910], fill=(255, 255, 255, 30))
    d.rounded_rectangle([200, 190, 600, 520], 40, fill=(255, 255, 255, 60), outline=(255, 255, 255, 140), width=4)
    d.ellipse([330, 260, 470, 400], fill=(255, 255, 255, 190))
    font = _font(46)
    lines, cur = [], ""
    for wd in title.split():
        t = (cur + " " + wd).strip()
        if d.textlength(t, font=font) > 640 and cur:
            lines.append(cur); cur = wd
        else:
            cur = t
    lines.append(cur)
    y = 570
    for ln in lines[:2]:
        d.text((400 - d.textlength(ln, font=font) / 2, y), ln, fill=(255, 255, 255, 255), font=font)
        y += 62
    img.save(path, "JPEG", quality=86)


def download_unsplash(photo_id, dest, size=1000, timeout=30):
    """Download a free Unsplash photo by id, crop to a square-ish product shot and save it."""
    cdn = getattr(seed_data, "UNSPLASH_CDN", {}).get(photo_id)
    if cdn:
        url = f"https://images.unsplash.com/{cdn}?fm=jpg&q=85&w=1600&fit=max"
    else:
        url = f"https://unsplash.com/photos/{photo_id}/download?force=true&w=1600"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (shop-seed)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img.thumbnail((size * 2, size * 2))
    img.save(dest, "JPEG", quality=85, optimize=True)
    return True


def resolve_image(fname, title, color, unsplash_id=None):
    """Upload a picture for `fname` to image storage and return its path (None if there is none).

    Sources are tried in order: seed_images/, Unsplash download, generated placeholder.
    """
    path = storage.key_for(fname)
    if storage.exists(path):
        return path
    src = os.path.join(SEED_IMAGES, fname)
    if os.path.exists(src):
        return storage.save_file(src, path)
    with tempfile.TemporaryDirectory() as tmp:
        dest = os.path.join(tmp, fname)
        if unsplash_id and os.environ.get("SEED_DOWNLOAD_IMAGES", "1") == "1":
            try:
                download_unsplash(unsplash_id, dest)
                return storage.save_file(dest, path)
            except Exception as e:  # offline, blocked, rate-limited...
                print(f"[seed] could not download {unsplash_id}: {e}")
        if title:
            make_placeholder(dest, title, tuple(color))
            return storage.save_file(dest, path)
    return None


def seed_if_empty(app):
    if Product.query.count():
        return
    cats = {}
    for i, p in enumerate(seed_data.PRODUCTS, 1):
        cname = p["category"]
        if cname not in cats:
            cats[cname] = Category.query.filter_by(name=cname).first() or Category(name=cname)
            db.session.add(cats[cname])
        fname = p.get("file") or f"seed_{i}.jpg"
        image = resolve_image(fname, p["name"], p.get("color", (120, 120, 120)), p.get("unsplash"))
        db.session.add(Product(
            name=p["name"], description=p["description"], price=Decimal(p["price"]),
            old_price=Decimal(p["old_price"]) if p.get("old_price") else None,
            stock=p["stock"], category_ref=cats[cname], featured=p.get("featured", False),
            image=image, status="active"))
    for b in getattr(seed_data, "BANNERS", []):
        resolve_image(b["file"], None, None, b.get("unsplash"))
    db.session.commit()
