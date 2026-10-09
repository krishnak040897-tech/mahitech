"""
migrate_to_cloudinary.py — one-time helper.
Uploads existing local uploads (thumbnails/pdfs/videos) to Cloudinary,
updates the database, and removes the local file after success.

Run:  python migrate_to_cloudinary.py
"""
import os

import models
import storage
from config import Config


def main():
    if not storage.cloudinary_enabled():
        print("❌ Cloudinary is not ready. Fix credentials / install the SDK first.")
        print("   Health:", storage.health())
        return

    print(f"✅ Cloudinary ready (cloud: {Config.CLOUDINARY_CLOUD_NAME}). Migrating...\n")
    folders = {"thumbnail": Config.THUMBNAIL_FOLDER,
               "pdf": Config.PDF_FOLDER,
               "video": Config.VIDEO_FOLDER}

    migrated = failed = 0
    for p in models.get_products():
        for col, kind in (("thumbnail", "thumbnail"),
                          ("pdf_file", "pdf"),
                          ("video_file", "video")):
            ref = p[col]
            if not ref or ref.startswith("http"):
                continue                      # already cloud / empty
            path = os.path.join(folders[kind], ref)
            if not os.path.exists(path):
                print(f"⚠ skip  product #{p['id']} {col}: file missing ({path})")
                continue
            print(f"→ uploading product #{p['id']} {col}: {ref} ...", end=" ")
            url = storage.upload_path(path, kind)
            if url:
                models.set_product_file(p["id"], col, url)
                try:
                    os.remove(path)
                except OSError:
                    pass
                print("OK")
                migrated += 1
            else:
                print("FAILED")
                print("   ", storage.get_last_error())
                failed += 1

    print(f"\nDone. Migrated: {migrated}, failed: {failed}")
    if failed:
        print("Re-run this script after fixing the errors — it skips already-migrated files.")


if __name__ == "__main__":
    main()