"""Move existing product images into the S3 bucket and store only their path in the database.

    python migrate_images.py

Safe to run more than once: every image in static/uploads/ is uploaded to "uploads/<file>"
in the bucket, and product rows holding a bare file name ("abc.jpg") are changed to the
path ("uploads/abc.jpg"). Local files are left in place.
"""
import os

import storage
from app import app
from models import Product, db


def main():
    if not storage.enabled():
        raise SystemExit("S3 is not configured: set BUCKET, AWS_ACCESS_KEY and AWS_SECRET_KEY in .env")
    folder = app.config["UPLOAD_FOLDER"]
    with app.app_context():
        for name in sorted(os.listdir(folder)):
            local = os.path.join(folder, name)
            if name.startswith(".") or not os.path.isfile(local):
                continue
            path = storage.key_for(name)
            if storage.exists(path):
                print(f"already in bucket  {path}")
            else:
                storage.save_file(local, path)
                print(f"uploaded           {path}")
        changed = 0
        for p in Product.query.filter(Product.image.isnot(None)).all():
            path = storage.key_for(p.image)
            if path != p.image:
                p.image = path
                changed += 1
        db.session.commit()
        print(f"updated {changed} product rows to store the file path")


if __name__ == "__main__":
    main()
