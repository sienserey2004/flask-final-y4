"""Image storage: an S3 bucket when configured, otherwise static/uploads/ on disk.

The database only keeps the file path (the S3 key), e.g. "uploads/3f2a...c1.jpg".
`url(path)` turns that path into a link the browser can load.
"""
import mimetypes
import os
import shutil
import time

from flask import url_for

from config import Config

PREFIX = "uploads/"
_client = None
_exists_cache = {}


def enabled():
    return bool(Config.S3_BUCKET and Config.AWS_ACCESS_KEY and Config.AWS_SECRET_KEY)


def _s3():
    global _client
    if _client is None:
        import boto3
        from botocore.config import Config as BotoConfig
        _client = boto3.client(
            "s3", region_name=Config.S3_REGION,
            endpoint_url=f"https://s3.{Config.S3_REGION}.amazonaws.com",
            aws_access_key_id=Config.AWS_ACCESS_KEY, aws_secret_access_key=Config.AWS_SECRET_KEY,
            config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "virtual"}))
    return _client


def key_for(name):
    """'abc.jpg' -> 'uploads/abc.jpg' (paths that already have a folder are kept)."""
    return name if "/" in name else PREFIX + name


def _local_path(path):
    return os.path.join(Config.UPLOAD_FOLDER, path[len(PREFIX):] if path.startswith(PREFIX) else path)


def _content_type(path):
    return mimetypes.guess_type(path)[0] or "application/octet-stream"


def save(fileobj, path):
    """Store a file-like object at `path` and return the path (what goes in the database)."""
    if enabled():
        _s3().upload_fileobj(fileobj, Config.S3_BUCKET, path, ExtraArgs={
            "ContentType": _content_type(path), "CacheControl": "public, max-age=31536000"})
    else:
        with open(_local_path(path), "wb") as f:
            shutil.copyfileobj(fileobj, f)
    _exists_cache[path] = True
    return path


def save_file(local_file, path):
    with open(local_file, "rb") as f:
        return save(f, path)


def delete(path):
    if not path:
        return
    _exists_cache.pop(path, None)
    try:
        if enabled():
            _s3().delete_object(Bucket=Config.S3_BUCKET, Key=path)
        else:
            os.remove(_local_path(path))
    except Exception:
        pass


def exists(path):
    if path not in _exists_cache:
        if enabled():
            try:
                _s3().head_object(Bucket=Config.S3_BUCKET, Key=path)
                _exists_cache[path] = True
            except Exception:
                _exists_cache[path] = False
        else:
            _exists_cache[path] = os.path.exists(_local_path(path))
    return _exists_cache[path]


SIGNED_TTL = 7 * 24 * 3600  # longest a signed S3 link can live
_signed_cache = {}  # path -> (link, time to make a new one)


def _signed_url(path):
    """The bucket is private, so browsers get a signed link that works for 7 days.
    The same link is reused for 6 days so the browser can cache the picture."""
    now = time.time()
    hit = _signed_cache.get(path)
    if hit and hit[1] > now:
        return hit[0]
    link = _s3().generate_presigned_url(
        "get_object", Params={"Bucket": Config.S3_BUCKET, "Key": path}, ExpiresIn=SIGNED_TTL)
    _signed_cache[path] = (link, now + SIGNED_TTL - 24 * 3600)
    return link


def url(path, external=False):
    """Link the browser can load for a stored path."""
    if not path:
        return ""
    if path.startswith(("http://", "https://")):
        return path
    path = key_for(path)
    if enabled():
        if Config.S3_PUBLIC_READ:
            return f"{Config.S3_PUBLIC_URL}/{path}"
        return _signed_url(path)
    return url_for("static", filename=path, _external=external)
