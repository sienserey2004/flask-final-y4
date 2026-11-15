"""Download the real product photos once, so they can be committed to GitHub.

    python download_images.py

Photos are saved into seed_images/. On first start the app copies them into
static/uploads/. Products without an Unsplash id keep a generated picture.
See IMAGE_CREDITS.md for photo sources (Unsplash License: free to use).
"""
import os
import sys

import seed_data
from seed import SEED_IMAGES, download_unsplash

os.makedirs(SEED_IMAGES, exist_ok=True)
items = [(p.get("file"), p.get("unsplash"), p["name"]) for p in seed_data.PRODUCTS]
items += [(b["file"], b.get("unsplash"), b["file"]) for b in getattr(seed_data, "BANNERS", [])]
ok = fail = 0
for fname, pid, label in items:
    if not (fname and pid):
        continue
    dest = os.path.join(SEED_IMAGES, fname)
    if os.path.exists(dest):
        print(f"  skip  {fname} (already downloaded)")
        ok += 1
        continue
    try:
        download_unsplash(pid, dest)
        print(f"  ok    {fname}  <- unsplash.com/photos/{pid}")
        ok += 1
    except Exception as e:
        print(f"  FAIL  {fname}: {e}")
        fail += 1
print(f"\nDone: {ok} downloaded, {fail} failed. Delete instance/shop.db and restart the app to re-seed.")
sys.exit(1 if fail else 0)
