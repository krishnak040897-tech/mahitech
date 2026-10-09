"""
storage.py — MahiTech file-storage layer.

Primary store:  Cloudinary (thumbnails=image, pdfs=raw, videos=video).
Fallback store: local disk folders from Config.

PDF delivery:
  - Cloudinary blocks UNSIGNED delivery of PDF/ZIP files by default, and a
    signed URL is only valid if its signature covers the asset's REAL version.
    _parse_cloudinary_url() therefore extracts the version from the stored URL,
    and signed_url() builds s--sig--/v<real-version>/... URLs that Cloudinary
    accepts. The Flask backend streams PDFs to authorized users, so the
    API secret and CDN URLs are never exposed to the client.
"""
import logging
import os
import tempfile
import uuid

from config import Config

try:
    import cloudinary
    import cloudinary.uploader
    import cloudinary.api
    _SDK = True
except ImportError:
    _SDK = False

log = logging.getLogger("mahitech.storage")

LAST_ERROR = None


def _mask(s):
    """Mask a credential for safe printing: 'ClrjK...LU80' / 'EMPTY'."""
    if not s:
        return "EMPTY"
    return s[:4] + "..." + s[-4:] if len(s) > 8 else "SET"


_cloud_ready = bool(
    _SDK and Config.CLOUDINARY_CLOUD_NAME and Config.CLOUDINARY_API_KEY
    and Config.CLOUDINARY_API_SECRET
)

if _cloud_ready:
    cloudinary.config(
        cloud_name=Config.CLOUDINARY_CLOUD_NAME,
        api_key=Config.CLOUDINARY_API_KEY,
        api_secret=Config.CLOUDINARY_API_SECRET,
        secure=True,
    )
    # ---- startup diagnostic: show exactly what this process loaded ----
    # NOTE: module scope — LAST_ERROR is already global here, so a plain
    # assignment is correct (a 'global' statement here would be a SyntaxError).
    print(f"[MahiTech] Storage cfg: cloud={Config.CLOUDINARY_CLOUD_NAME!r} "
          f"key={_mask(Config.CLOUDINARY_API_KEY)} "
          f"secret={_mask(Config.CLOUDINARY_API_SECRET)}")
    try:
        cloudinary.api.ping()
        print(f"[MahiTech] Cloudinary ping OK (cloud: {Config.CLOUDINARY_CLOUD_NAME})")
    except Exception as exc:
        LAST_ERROR = f"Cloudinary ping failed: {exc}"
        print(f"\n[MahiTech] ⚠ CLOUDINARY PROBLEM: {exc}\n"
              f"[MahiTech] ⚠ Check your API key/secret and cloud name.\n"
              f"[MahiTech] ⚠ Uploads will fall back to LOCAL storage.\n")
        _cloud_ready = False   # don't attempt uploads with bad credentials
elif not _SDK:
    print("[MahiTech] ⚠ cloudinary package NOT installed (pip install cloudinary) "
          "-> local storage mode.")
else:
    print("[MahiTech] ⚠ Cloudinary keys not configured -> local storage mode. "
          f"(cloud={Config.CLOUDINARY_CLOUD_NAME!r} "
          f"key={_mask(Config.CLOUDINARY_API_KEY)} "
          f"secret={_mask(Config.CLOUDINARY_API_SECRET)})")


_DIRS = {
    "thumbnail": Config.THUMBNAIL_FOLDER,
    "pdf": Config.PDF_FOLDER,
    "video": Config.VIDEO_FOLDER,
}
_FOLDERS = {
    "thumbnail": "mahitech/thumbnails",
    "pdf": "mahitech/pdfs",
    "video": "mahitech/videos",
}
_RESOURCE = {"thumbnail": "image", "pdf": "raw", "video": "video"}


def cloudinary_enabled():
    return _cloud_ready


def get_last_error():
    return LAST_ERROR


def health():
    return {
        "cloudinary_enabled": _cloud_ready,
        "cloud_name": Config.CLOUDINARY_CLOUD_NAME if _cloud_ready else None,
        "sdk_installed": _SDK,
        "last_error": LAST_ERROR,
    }


def _uniq_name(filename):
    from werkzeug.utils import secure_filename
    return f"{uuid.uuid4().hex[:10]}_{secure_filename(filename or 'file')}"


def _parse_cloudinary_url(url):
    """
    Split a Cloudinary delivery URL into its parts.
    Returns (resource_type, public_id, version_or_None).
    The public_id KEEPS its extension (required for raw files such as PDFs).
    """
    parts = url.split("/")
    if "upload" not in parts:
        return None, None, None
    i = parts.index("upload")
    rtype = parts[i - 1] if i and parts[i - 1] in ("image", "raw", "video") else "image"
    tail = parts[i + 1:]
    version = None
    if tail and tail[0].startswith("v") and tail[0][1:].isdigit():
        version = tail.pop(0)[1:]        # keep the REAL version (e.g. 1791520282)
    return rtype, "/".join(tail), version


def signed_url(reference, download=False):
    """
    Build a SIGNED Cloudinary URL for a stored reference.

    CRITICAL: the signature MUST cover the asset's real version. Passing the
    version extracted from the stored URL (instead of letting the SDK default
    to v1) is what makes the signature valid — a mismatched version is what
    produced the 401 'deny or ACL failure' error.

    download=True adds fl_attachment (forces browser download).
    Local filenames (non-http references) are returned unchanged.
    """
    if not reference or not reference.startswith("http") or not _cloud_ready:
        return reference
    rtype, public_id, version = _parse_cloudinary_url(reference)
    if not public_id:
        return reference
    try:
        import cloudinary.utils
        opts = {
            "resource_type": rtype or "raw",
            "type": "upload",
            "sign_url": True,
            "secure": True,
            "force_version": False,          # never inject a fake v1
        }
        if version:
            opts["version"] = version        # sign over the REAL version
        if download:
            opts["attachment"] = True        # fl_attachment
        url, _unused = cloudinary.utils.cloudinary_url(public_id, **opts)
        return url
    except Exception as exc:
        log.warning("Signed URL build failed for %s: %s", reference, exc)
        return reference


def upload_path(path, kind):
    """
    Upload a file already on disk. Returns the secure URL, or None on failure.
    kind: 'thumbnail' | 'pdf' | 'video'
    """
    global LAST_ERROR
    if not _cloud_ready:
        return None
    try:
        if kind == "thumbnail":
            res = cloudinary.uploader.upload(
                path, folder=_FOLDERS[kind], resource_type="image")
        else:
            res = cloudinary.uploader.upload_large(
                path, folder=_FOLDERS[kind], resource_type=_RESOURCE[kind])
        url = res.get("secure_url")
        if url:
            log.info("Uploaded %s -> %s", path, url)
            LAST_ERROR = None
            return url
        LAST_ERROR = "Cloudinary returned no URL."
        log.warning(LAST_ERROR)
        return None
    except Exception as exc:
        LAST_ERROR = f"Cloudinary upload failed: {exc}"
        log.warning(LAST_ERROR)
        print(f"[MahiTech] ⚠ {LAST_ERROR}")
        return None


def save_file(fs, kind):
    """
    Store an uploaded FileStorage.  kind: 'thumbnail' | 'pdf' | 'video'.
    Returns the DB reference (Cloudinary URL or local filename), or None.
    Falls back to local disk if Cloudinary upload fails
    (storage.get_last_error() explains why — flash it to the admin).
    """
    global LAST_ERROR
    if fs is None or not fs.filename:
        return None
    LAST_ERROR = None

    if _cloud_ready:
        # Stream through a temp file so chunked upload_large works for big
        # PDFs/videos and the FileStorage cursor position never matters.
        suffix = "_" + _uniq_name(fs.filename)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp_path = tmp.name
        tmp.close()
        try:
            fs.save(tmp_path)
            url = upload_path(tmp_path, kind)
            if url:
                return url
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    # ---- local fallback (also the path when Cloudinary is off) ----
    os.makedirs(_DIRS[kind], exist_ok=True)
    name = _uniq_name(fs.filename)
    fs.save(os.path.join(_DIRS[kind], name))
    return name


def delete_file(kind, reference):
    """
    Delete a stored file (Cloudinary URL or local filename). Never raises.
    kind: 'thumbnail' | 'pdf' | 'video'
    """
    if not reference:
        return
    if reference.startswith("http"):
        if not _cloud_ready:
            return
        rtype, public_id, _version = _parse_cloudinary_url(reference)
        if not public_id:
            return
        try:
            cloudinary.uploader.destroy(
                public_id, resource_type=rtype or "image", invalidate=True)
        except Exception as exc:
            log.warning("Cloudinary delete failed for %s: %s", reference, exc)
        return
    # ---- local file ----
    path = os.path.join(_DIRS[kind], reference)
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass