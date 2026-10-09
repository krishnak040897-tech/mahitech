"""
app.py — MahiTech | Simple educational marketplace
Run:  python app.py   ->   http://127.0.0.1:5000  (or http://<your-ip>:5000 from other devices)

Payments: Razorpay (server-side order creation + signature verification) for
          BOTH products and standalone exam papers.
Storage:  Cloudinary (cloud) with automatic local-disk fallback (see storage.py).
Exams:    Standalone purchasable MCQ papers — browse all at /exams, buy each
          paper, then take it (timed, server-side grading, retakes allowed).
"""
import hashlib
import hmac
import os
import uuid
from datetime import datetime
from functools import wraps

import requests
import razorpay
from flask import (
    Flask, Response, abort, flash, jsonify, redirect, render_template, request,
    send_from_directory, session, stream_with_context, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

import models
import storage
from config import Config

app = Flask(__name__)
app.config.from_object(Config)

for _f in (Config.UPLOAD_FOLDER, Config.THUMBNAIL_FOLDER,
           Config.PDF_FOLDER, Config.VIDEO_FOLDER):
    os.makedirs(_f, exist_ok=True)

models.init_db(seed=Config.SEED_DEMO_DATA)

# ---------------- Razorpay client ----------------
razorpay_client = None
if not Config.DEMO_MODE:
    razorpay_client = razorpay.Client(
        auth=(Config.RAZORPAY_KEY_ID, Config.RAZORPAY_KEY_SECRET))
    print(f"[MahiTech] Razorpay ACTIVE "
          f"({'TEST' if Config.RAZORPAY_TEST_MODE else 'LIVE'} mode, key {Config.RAZORPAY_KEY_ID})")
else:
    print("[MahiTech] Razorpay secret not set -> DEMO checkout mode. "
          "Set RAZORPAY_KEY_SECRET to enable real payments.")

# ---------------- Storage mode (Cloudinary ping happens in storage.py) ----------------
if storage.cloudinary_enabled():
    print(f"[MahiTech] Cloudinary storage ACTIVE (cloud: {Config.CLOUDINARY_CLOUD_NAME})")
else:
    print("[MahiTech] Cloudinary not ready -> local storage mode. "
          f"Reason: {storage.get_last_error() or 'not configured / SDK missing'}")

# ---- startup template check ----
REQUIRED_TEMPLATES = [
    "base.html", "navbar.html", "home.html", "browse.html", "product.html",
    "login.html", "register.html", "profile.html", "my_purchases.html",
    "exams.html", "my_exams.html",
    "exam/start.html", "exam/take.html", "exam/result.html",
    "admin/profile.html", "admin/products.html", "admin/product_form.html",
    "admin/users.html", "admin/purchases.html",
    "admin/exams.html", "admin/exam_form.html",
]
_missing = [t for t in REQUIRED_TEMPLATES
            if not os.path.exists(os.path.join(app.root_path, "templates", *t.split("/")))]
if _missing:
    print("\n[MahiTech] MISSING TEMPLATES:", ", ".join(_missing), "\n")
else:
    print(f"[MahiTech] All {len(REQUIRED_TEMPLATES)} templates found.")


# ---------------- helpers ----------------
def is_admin():
    return bool(session.get("is_admin"))


def login_required(f):
    """For user-only pages. Admin sessions are sent to the admin panel."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if is_admin() and not session.get("user_id"):
            flash("You are logged in as admin — manage the store from the Admin panel.", "info")
            return redirect(url_for("admin_profile"))
        if not session.get("user_id"):
            flash("Please log in to continue.", "error")
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return wrapper


def api_login_required(f):
    """For JSON payment endpoints: return JSON errors instead of redirects."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            msg = ("Admins cannot purchase. Log in with a user account."
                   if is_admin() else "Please log in to continue.")
            return jsonify(error=msg), 401
        return f(*args, **kwargs)
    return wrapper


def media_login_required(f):
    """Allows EITHER a logged-in user OR the admin session to reach media routes."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if session.get("user_id") or is_admin():
            return f(*args, **kwargs)
        flash("Please log in to access this material.", "error")
        return redirect(url_for("login", next=request.path))
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not is_admin():
            flash("Admin access required.", "error")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def _safe_next(target):
    return target if target and target.startswith("/") and not target.startswith("//") else None


def allowed_file(filename, exts):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in exts


def _exam_allowed(exam):
    """Exam access: admin preview, or the user bought this paper."""
    return is_admin() or (session.get("user_id")
                          and models.has_purchased_exam(session["user_id"], exam["id"]))


# ---- template helpers ----
@app.template_filter("thumb_url")
def thumb_url(value):
    """
    Build the display URL for a stored thumbnail.
      - Cloudinary URL -> optimized delivery URL (f_auto,q_auto)
      - local filename -> /static/uploads/thumbnails/<name>
    """
    if not value:
        return ""
    if value.startswith("http"):
        if "/image/upload/" in value and "f_auto" not in value:
            return value.replace("/image/upload/", "/image/upload/f_auto,q_auto/")
        return value
    return url_for("static", filename=f"uploads/thumbnails/{value}")


@app.context_processor
def inject_globals():
    return {
        "categories": Config.CATEGORIES,
        "is_admin": is_admin(),
        "demo_mode": Config.DEMO_MODE,
    }


@app.template_filter("inr")
def inr(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return value
    return f"{int(v):,}" if v == int(v) else f"{v:,.2f}"


# ================= PUBLIC =================
@app.route("/")
def home():
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    products = models.get_products(search=q or None, category=category or None)
    return render_template("home.html", products=products, q=q,
                           selected_category=category)


@app.route("/browse")
def browse():
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    products = models.get_products(search=q or None, category=category or None)
    return render_template("browse.html", products=products,
                           q=q, selected_category=category)


@app.route("/product/<int:product_id>")
def product(product_id):
    product = models.get_product_by_id(product_id)
    if not product:
        abort(404)
    purchased = (models.has_purchased(session["user_id"], product_id)
                 if session.get("user_id") else False)
    can_access = purchased or is_admin()   # admin opens everything
    return render_template(
        "product.html", product=product, purchased=purchased, can_access=can_access,
        payment_mode="demo" if Config.DEMO_MODE else "direct",
        razorpay_test=Config.RAZORPAY_TEST_MODE)


# ================= EXAM PAPERS (public browse) =================
@app.route("/exams")
def exams_browse():
    """All exam papers — visible to everyone; each paper is bought separately."""
    exams = models.get_exams()
    owned_ids = (models.get_owned_exam_ids(session["user_id"])
                 if session.get("user_id") else set())
    return render_template("exams.html", exams=exams, owned_ids=owned_ids)


# ================= AUTH =================
@app.route("/register", methods=["GET", "POST"])
def register():
    if session.get("user_id") or is_admin():
        return redirect(url_for("home"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if not username or not email or not password:
            flash("All fields are required.", "error")
        elif username.lower() == Config.ADMIN_USERNAME.lower():
            flash("That username is reserved.", "error")
        elif models.get_user_by_username(username):
            flash("Username already taken.", "error")
        elif models.get_user_by_email(email):
            flash("Email already registered.", "error")
        elif len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
        elif password != confirm:
            flash("Passwords do not match.", "error")
        else:
            models.create_user(username, email, generate_password_hash(password))
            flash("Registered! Please log in.", "success")
            return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    # Already-logged-in admin should never see the login form again
    if request.method == "GET" and is_admin() and not session.get("user_id"):
        return redirect(url_for("admin_profile"))

    if request.method == "POST":
        identifier = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        nxt = _safe_next(request.form.get("next") or request.args.get("next"))

        if identifier == Config.ADMIN_USERNAME and password == Config.ADMIN_PASSWORD:
            session.clear()
            session["is_admin"] = True
            session.permanent = True
            flash("Welcome back, Admin Mahesh!", "success")
            admin_next = nxt if nxt and (nxt.startswith("/admin") or
                                         nxt.startswith("/product/") or
                                         nxt.startswith("/browse") or
                                         nxt.startswith("/exam") or
                                         nxt == "/") else None
            return redirect(admin_next or url_for("admin_profile"))

        user = models.get_user_by_username_or_email(identifier)
        if user and check_password_hash(user["password"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["user_name"] = user["username"]
            return redirect(nxt or url_for("profile"))
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


# ================= USER PROFILE =================
@app.route("/profile")
@login_required
def profile():
    user = models.get_user_by_id(session["user_id"])
    purchases = models.get_user_purchases(session["user_id"])
    return render_template("profile.html", user=user, purchases=purchases,
                           total_spent=sum(p["amount"] for p in purchases))


@app.route("/dashboard")
def dashboard_legacy():
    """Old URL — forwards to the profile page."""
    return redirect(url_for("profile"))


@app.route("/my-purchases")
@login_required
def my_purchases():
    return render_template("my_purchases.html",
                           purchases=models.get_user_purchases(session["user_id"]))


# ================= PAYMENTS: PRODUCTS (RAZORPAY) =================
@app.route("/api/create-order/<int:product_id>", methods=["POST"])
@api_login_required
def create_order(product_id):
    product = models.get_product_by_id(product_id)
    if not product:
        return jsonify(error="Product not found."), 404
    user_id = session["user_id"]
    if models.has_purchased(user_id, product_id):
        return jsonify(error="You already own this product."), 400

    amount = int(round(float(product["price"]) * 100))   # paise

    if amount <= 0:
        models.create_purchase(user_id, product_id, product["title"], 0, "free", "free")
        return jsonify(success=True,
                       redirect_url=url_for("product", product_id=product_id))

    if Config.DEMO_MODE or razorpay_client is None:
        order_id = "order_demo" + uuid.uuid4().hex
        demo = session.get("demo_orders", {})
        demo[order_id] = product_id
        session["demo_orders"] = demo
        return jsonify(demo=True, order_id=order_id, amount=amount,
                       currency="INR", description=product["title"])

    try:
        order = razorpay_client.order.create({
            "amount": amount,
            "currency": "INR",
            "receipt": f"rcpt_p{product_id}_u{user_id}_{uuid.uuid4().hex[:6]}",
            "notes": {"product_id": str(product_id), "user_id": str(user_id)},
        })
    except Exception as exc:
        app.logger.error("Razorpay order creation failed: %s", exc)
        return jsonify(error="Payment gateway error. Check your Razorpay keys and try again."), 502

    user = models.get_user_by_id(user_id)
    return jsonify(demo=False, key=Config.RAZORPAY_KEY_ID, order_id=order["id"],
                   amount=amount, currency="INR", description=product["title"],
                   prefill={"name": user["username"], "email": user["email"]})


@app.route("/api/verify-payment", methods=["POST"])
@api_login_required
def verify_payment():
    data = request.get_json(silent=True) or {}
    user_id = session["user_id"]

    if data.get("demo"):
        if not Config.DEMO_MODE:
            return jsonify(error="Demo payments are disabled."), 400
        demo = session.get("demo_orders", {})
        pid = demo.pop(data.get("order_id", ""), None)
        session["demo_orders"] = demo
        product = models.get_product_by_id(pid) if pid else None
        if not product:
            return jsonify(error="Invalid demo order."), 400
        if not models.has_purchased(user_id, product["id"]):
            models.create_purchase(user_id, product["id"], product["title"],
                                   product["price"], "pay_demo_" + uuid.uuid4().hex[:12],
                                   data.get("order_id", ""))
        return jsonify(success=True,
                       redirect_url=url_for("product", product_id=product["id"]))

    if razorpay_client is None:
        return jsonify(error="Payment gateway is not configured."), 400

    rp_order = data.get("razorpay_order_id", "")
    rp_payment = data.get("razorpay_payment_id", "")
    rp_signature = data.get("razorpay_signature", "")
    if not (rp_order and rp_payment and rp_signature):
        return jsonify(error="Missing payment details."), 400

    expected = hmac.new(Config.RAZORPAY_KEY_SECRET.encode(),
                        f"{rp_order}|{rp_payment}".encode(),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, rp_signature):
        return jsonify(error="Payment verification failed."), 400

    try:
        order = razorpay_client.order.fetch(rp_order)
    except Exception as exc:
        app.logger.error("Razorpay order fetch failed: %s", exc)
        return jsonify(error="Could not confirm the order. Contact support."), 502

    notes = order.get("notes") or {}
    if str(notes.get("user_id")) != str(user_id):
        return jsonify(error="This order belongs to a different account."), 403

    try:
        product = models.get_product_by_id(int(notes.get("product_id", 0)))
    except (TypeError, ValueError):
        product = None
    if not product:
        return jsonify(error="Product not found."), 404

    if int(order.get("amount", 0)) != int(round(float(product["price"]) * 100)):
        return jsonify(error="Amount mismatch. Contact support."), 400

    if not models.has_purchased(user_id, product["id"]):
        models.create_purchase(user_id, product["id"], product["title"],
                               product["price"], rp_payment, rp_order)
    return jsonify(success=True,
                   redirect_url=url_for("product", product_id=product["id"]))


# ================= PAYMENTS: EXAM PAPERS (RAZORPAY) =================
@app.route("/api/create-exam-order/<int:exam_id>", methods=["POST"])
@api_login_required
def create_exam_order(exam_id):
    exam = models.get_exam_by_id(exam_id)
    if not exam:
        return jsonify(error="Exam not found."), 404
    user_id = session["user_id"]
    if models.has_purchased_exam(user_id, exam_id):
        return jsonify(error="You already own this exam paper."), 400

    amount = int(round(float(exam["price"]) * 100))   # paise

    if amount <= 0:
        models.create_exam_purchase(user_id, exam_id, 0, "free", "free")
        return jsonify(success=True,
                       redirect_url=url_for("exam_start", exam_id=exam_id))

    if Config.DEMO_MODE or razorpay_client is None:
        order_id = "order_demo" + uuid.uuid4().hex
        demo = session.get("demo_exam_orders", {})
        demo[order_id] = exam_id
        session["demo_exam_orders"] = demo
        return jsonify(demo=True, order_id=order_id, amount=amount,
                       currency="INR", description=exam["title"])

    try:
        order = razorpay_client.order.create({
            "amount": amount,
            "currency": "INR",
            "receipt": f"rcpt_e{exam_id}_u{user_id}_{uuid.uuid4().hex[:6]}",
            "notes": {"exam_id": str(exam_id), "user_id": str(user_id)},
        })
    except Exception as exc:
        app.logger.error("Razorpay exam order creation failed: %s", exc)
        return jsonify(error="Payment gateway error. Check your Razorpay keys and try again."), 502

    user = models.get_user_by_id(user_id)
    return jsonify(demo=False, key=Config.RAZORPAY_KEY_ID, order_id=order["id"],
                   amount=amount, currency="INR", description=exam["title"],
                   prefill={"name": user["username"], "email": user["email"]})


@app.route("/api/verify-exam-payment", methods=["POST"])
@api_login_required
def verify_exam_payment():
    data = request.get_json(silent=True) or {}
    user_id = session["user_id"]

    if data.get("demo"):
        if not Config.DEMO_MODE:
            return jsonify(error="Demo payments are disabled."), 400
        demo = session.get("demo_exam_orders", {})
        eid = demo.pop(data.get("order_id", ""), None)
        session["demo_exam_orders"] = demo
        exam = models.get_exam_by_id(eid) if eid else None
        if not exam:
            return jsonify(error="Invalid demo order."), 400
        if not models.has_purchased_exam(user_id, exam["id"]):
            models.create_exam_purchase(user_id, exam["id"], exam["price"],
                                        "pay_demo_" + uuid.uuid4().hex[:12],
                                        data.get("order_id", ""))
        return jsonify(success=True,
                       redirect_url=url_for("exam_start", exam_id=exam["id"]))

    if razorpay_client is None:
        return jsonify(error="Payment gateway is not configured."), 400

    rp_order = data.get("razorpay_order_id", "")
    rp_payment = data.get("razorpay_payment_id", "")
    rp_signature = data.get("razorpay_signature", "")
    if not (rp_order and rp_payment and rp_signature):
        return jsonify(error="Missing payment details."), 400

    expected = hmac.new(Config.RAZORPAY_KEY_SECRET.encode(),
                        f"{rp_order}|{rp_payment}".encode(),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, rp_signature):
        return jsonify(error="Payment verification failed."), 400

    try:
        order = razorpay_client.order.fetch(rp_order)
    except Exception as exc:
        app.logger.error("Razorpay exam order fetch failed: %s", exc)
        return jsonify(error="Could not confirm the order. Contact support."), 502

    notes = order.get("notes") or {}
    if str(notes.get("user_id")) != str(user_id):
        return jsonify(error="This order belongs to a different account."), 403

    try:
        exam = models.get_exam_by_id(int(notes.get("exam_id", 0)))
    except (TypeError, ValueError):
        exam = None
    if not exam:
        return jsonify(error="Exam not found."), 404

    if int(order.get("amount", 0)) != int(round(float(exam["price"]) * 100)):
        return jsonify(error="Amount mismatch. Contact support."), 400

    if not models.has_purchased_exam(user_id, exam["id"]):
        models.create_exam_purchase(user_id, exam["id"], exam["price"],
                                    rp_payment, rp_order)
    return jsonify(success=True,
                   redirect_url=url_for("exam_start", exam_id=exam["id"]))


# ================= PROTECTED MEDIA =================
def _pdf_candidates(reference):
    """URLs to try, best first: correctly-versioned signed URL, then original."""
    candidates = []
    signed = storage.signed_url(reference, download=False)
    if signed and signed != reference:
        candidates.append(("signed", signed))
    candidates.append(("unsigned", reference))
    return candidates


def _stream_pdf_from_cloudinary(reference, download, title, product_id):
    """
    Secure backend proxy: fetch the PDF from Cloudinary server-side and stream
    it to the (already authorized) client. API secret stays on the server and
    the client never sees a CDN URL.
    """
    for label, url in _pdf_candidates(reference):
        try:
            upstream = requests.get(url, timeout=(10, 60), stream=True,
                                    allow_redirects=True)
        except requests.RequestException as exc:
            app.logger.error("PDF %s attempt (product %s) network error: %s",
                             label, product_id, exc)
            continue

        if upstream.status_code != 200:
            try:
                snippet = (upstream.content or b"")[:300].decode("utf-8", "replace")
            except Exception:
                snippet = ""
            upstream.close()
            app.logger.warning("PDF %s attempt (product %s) -> HTTP %s | %s",
                               label, product_id, upstream.status_code, snippet)
            continue

        ctype = (upstream.headers.get("Content-Type") or "").lower()
        if "pdf" not in ctype:
            body_head = (upstream.content or b"")[:300].decode("utf-8", "replace")
            upstream.close()
            app.logger.warning("PDF %s attempt (product %s) returned non-PDF "
                               "(%s): %s", label, product_id, ctype, body_head)
            continue

        safe_title = (title or "material").replace('"', "").replace("\r", "").replace("\n", "")
        disposition = "attachment" if download else "inline"
        headers = {
            "Content-Type": upstream.headers.get("Content-Type", "application/pdf"),
            "Content-Disposition": f'{disposition}; filename="{safe_title}.pdf"',
        }
        if upstream.headers.get("Content-Length"):
            headers["Content-Length"] = upstream.headers["Content-Length"]

        return Response(stream_with_context(upstream.iter_content(chunk_size=64 * 1024)),
                        status=200, headers=headers)

    app.logger.error("PDF delivery failed for product %s (all attempts denied). "
                     "If every attempt is HTTP 401, enable 'Allow delivery of PDF "
                     "and ZIP files' in Cloudinary -> Settings -> Security.",
                     product_id)
    abort(502)


@app.route("/media/pdf/<int:product_id>")
@media_login_required
def serve_pdf(product_id):
    product = models.get_product_by_id(product_id)
    if not product or not product["pdf_file"]:
        abort(404)
    if not is_admin() and not models.has_purchased(session["user_id"], product_id):
        abort(403)
    ref = product["pdf_file"]
    download = request.args.get("download") == "1"

    if ref.startswith("http"):
        return _stream_pdf_from_cloudinary(ref, download, product["title"], product_id)

    return send_from_directory(
        Config.PDF_FOLDER, ref, mimetype="application/pdf",
        as_attachment=download, conditional=True,
        download_name=f"{product['title']}.pdf" if download else None)


@app.route("/media/video/<int:product_id>")
@media_login_required
def serve_video(product_id):
    product = models.get_product_by_id(product_id)
    if not product or not product["video_file"]:
        abort(404)
    if not is_admin() and not models.has_purchased(session["user_id"], product_id):
        abort(403)
    ref = product["video_file"]

    if ref.startswith("http"):
        return redirect(ref)

    return send_from_directory(Config.VIDEO_FOLDER, ref, conditional=True)


# ================= EXAMS: USER SIDE =================
@app.route("/my-exams")
@login_required
def my_exams():
    if is_admin() and not session.get("user_id"):
        flash("Admins take exams with a user account. Manage exams from the Admin panel.", "info")
        return redirect(url_for("admin_exams"))
    exams = models.get_purchased_exams(session["user_id"])
    return render_template("my_exams.html", exams=exams)


@app.route("/exam/<int:exam_id>")
@login_required
def exam_start(exam_id):
    exam = models.get_exam_by_id(exam_id)
    if not exam:
        abort(404)
    if not _exam_allowed(exam):
        flash("Buy this exam paper to take it.", "error")
        return redirect(url_for("exams_browse"))
    questions = models.get_questions_public(exam_id)
    attempts = (models.get_user_attempts(exam_id, session["user_id"])
                if session.get("user_id") else [])
    return render_template("exam/start.html", exam=exam,
                           q_count=len(questions), attempts=attempts)


@app.route("/exam/<int:exam_id>/start", methods=["POST"])
@login_required
def exam_begin(exam_id):
    exam = models.get_exam_by_id(exam_id)
    if not exam:
        abort(404)
    if not _exam_allowed(exam):
        flash("Buy this exam paper to take it.", "error")
        return redirect(url_for("exams_browse"))
    if models.count_questions(exam_id) == 0:
        flash("This exam has no questions yet. Please check back later.", "error")
        return redirect(url_for("exam_start", exam_id=exam_id))

    active = models.get_active_attempt(exam_id, session["user_id"])
    if active:
        return redirect(url_for("exam_take", attempt_id=active["id"]))
    attempt_id = models.create_attempt(exam_id, session["user_id"])
    return redirect(url_for("exam_take", attempt_id=attempt_id))


@app.route("/exam/take/<int:attempt_id>")
@login_required
def exam_take(attempt_id):
    attempt = models.get_attempt_by_id(attempt_id)
    if not attempt or attempt["user_id"] != session["user_id"]:
        abort(404)
    if attempt["status"] != "in_progress":
        return redirect(url_for("exam_result", attempt_id=attempt_id))

    exam = models.get_exam_by_id(attempt["exam_id"])
    if not exam or not _exam_allowed(exam):
        abort(403)

    # server-side timer: auto-submit when time is over
    started = datetime.strptime(attempt["started_at"], "%Y-%m-%d %H:%M:%S")
    remaining = int(exam["duration_minutes"] * 60 - (datetime.now() - started).total_seconds())
    if remaining <= 0:
        models.finish_attempt(attempt_id, 0, models.count_questions(exam["id"]), "")
        flash("Time is up — your attempt was submitted automatically.", "info")
        return redirect(url_for("exam_result", attempt_id=attempt_id))

    questions = models.get_questions_public(exam["id"])   # no correct answers sent
    return render_template("exam/take.html", exam=exam, attempt=attempt,
                           questions=questions, remaining=remaining)


@app.route("/exam/take/<int:attempt_id>/submit", methods=["POST"])
@login_required
def exam_submit(attempt_id):
    attempt = models.get_attempt_by_id(attempt_id)
    if not attempt or attempt["user_id"] != session["user_id"]:
        abort(404)
    if attempt["status"] != "in_progress":
        return redirect(url_for("exam_result", attempt_id=attempt_id))

    exam = models.get_exam_by_id(attempt["exam_id"])
    if not exam or not _exam_allowed(exam):
        abort(403)

    # collect client answers: form keys look like "q_<question_id>"
    submitted = {}
    for key, value in request.form.items():
        if key.startswith("q_") and value in ("a", "b", "c", "d"):
            submitted[key[2:]] = value

    # grade SERVER-SIDE (correct answers never leave the server)
    score = 0
    questions = models.get_questions(exam["id"])
    for q in questions:
        if submitted.get(str(q["id"])) == q["correct"]:
            score += 1

    answers = ";".join(f"{qid}:{opt}" for qid, opt in submitted.items())
    models.finish_attempt(attempt_id, score, len(questions), answers)
    flash("Exam submitted.", "success")
    return redirect(url_for("exam_result", attempt_id=attempt_id))


@app.route("/exam/result/<int:attempt_id>")
@login_required
def exam_result(attempt_id):
    attempt = models.get_attempt_by_id(attempt_id)
    if not attempt or attempt["user_id"] != session["user_id"]:
        abort(404)
    exam = models.get_exam_by_id(attempt["exam_id"])
    if not exam:
        abort(404)
    if not _exam_allowed(exam):
        abort(403)

    # build review data (your answer vs correct)
    answers = {}
    for pair in (attempt["answers"] or "").split(";"):
        if ":" in pair:
            qid, opt = pair.split(":", 1)
            answers[qid] = opt
    review = []
    for q in models.get_questions(exam["id"]):
        your = answers.get(str(q["id"]))
        review.append({
            "question": q["question"],
            "opts": {"a": q["opt_a"], "b": q["opt_b"], "c": q["opt_c"], "d": q["opt_d"]},
            "your": your,
            "correct": q["correct"],
            "ok": your == q["correct"],
        })
    percent = round(100 * attempt["score"] / attempt["total"]) if attempt["total"] else 0
    passed = percent >= exam["pass_percent"]
    return render_template("exam/result.html", exam=exam, attempt=attempt,
                           review=review, percent=percent, passed=passed)


# ================= ADMIN: PROFILE (HUB) =================
@app.route("/admin/profile")
@admin_required
def admin_profile():
    return render_template(
        "admin/profile.html",
        users=models.count_users(),
        products=models.count_products(),
        purchases=models.count_purchases(),
        exams=models.count_exams(),
        revenue=models.total_revenue(),
        recent=models.get_all_purchases(limit=6))


@app.route("/admin/dashboard")
@admin_required
def admin_dashboard_legacy():
    """Old URL — forwards to the admin profile page."""
    return redirect(url_for("admin_profile"))


# ================= ADMIN: EXAMS & MCQs =================
@app.route("/admin/exams")
@admin_required
def admin_exams():
    return render_template("admin/exams.html", exams=models.get_exams())


@app.route("/admin/exams/add", methods=["GET", "POST"])
@admin_required
def admin_add_exam():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        try:
            price = round(float(request.form.get("price", "0") or 0), 2)
            duration = max(1, int(request.form.get("duration_minutes", "15")))
            pass_percent = min(100, max(0, int(request.form.get("pass_percent", "40"))))
            if price < 0:
                raise ValueError
        except ValueError:
            flash("Enter a valid price / duration / pass percentage.", "error")
            return render_template("admin/exam_form.html", exam=None)
        if not title:
            flash("Exam title is required.", "error")
        else:
            eid = models.create_exam(title, description, price,
                                     duration, pass_percent)
            flash("Exam paper created — now add your questions.", "success")
            return redirect(url_for("admin_edit_exam", exam_id=eid))
    return render_template("admin/exam_form.html", exam=None)


@app.route("/admin/exams/edit/<int:exam_id>", methods=["GET", "POST"])
@admin_required
def admin_edit_exam(exam_id):
    exam = models.get_exam_by_id(exam_id)
    if not exam:
        abort(404)

    # adding / editing a question (single route: hidden question_id decides)
    if request.method == "POST" and request.form.get("action") == "save_question":
        qid = request.form.get("question_id", "").strip()
        question = request.form.get("question", "").strip()
        a = request.form.get("opt_a", "").strip()
        b = request.form.get("opt_b", "").strip()
        c = request.form.get("opt_c", "").strip()
        d = request.form.get("opt_d", "").strip()
        correct = request.form.get("correct", "")
        if not question or not a or not b or not c or not d:
            flash("Question and all four options are required.", "error")
        elif correct not in ("a", "b", "c", "d"):
            flash("Mark the correct option.", "error")
        else:
            if qid.isdigit() and models.get_question_by_id(int(qid)):
                models.update_question(int(qid), question, a, b, c, d, correct)
                flash("Question updated.", "success")
            else:
                models.add_question(exam_id, question, a, b, c, d, correct)
                flash("Question added.", "success")
            return redirect(url_for("admin_edit_exam", exam_id=exam_id))

    # exam details save
    if request.method == "POST" and request.form.get("action") == "save_exam":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        try:
            price = round(float(request.form.get("price", "0") or 0), 2)
            duration = max(1, int(request.form.get("duration_minutes", "15")))
            pass_percent = min(100, max(0, int(request.form.get("pass_percent", "40"))))
            if price < 0:
                raise ValueError
        except ValueError:
            flash("Enter a valid price / duration / pass percentage.", "error")
            return redirect(url_for("admin_edit_exam", exam_id=exam_id))
        if not title:
            flash("Title is required.", "error")
        else:
            models.update_exam(exam_id, title, description, price,
                               duration, pass_percent)
            flash("Exam updated.", "success")
            return redirect(url_for("admin_edit_exam", exam_id=exam_id))

    # prefill question form for editing (?qid=)
    edit_q = None
    qid = request.args.get("qid", "")
    if qid.isdigit():
        q = models.get_question_by_id(int(qid))
        if q and q["exam_id"] == exam_id:
            edit_q = q

    return render_template("admin/exam_form.html", exam=exam,
                           questions=models.get_questions(exam_id),
                           edit_q=edit_q)


@app.route("/admin/exams/delete/<int:exam_id>", methods=["POST"])
@admin_required
def admin_delete_exam(exam_id):
    models.delete_exam(exam_id)   # cascades purchases, questions + attempts
    flash("Exam deleted (with its questions and attempts).", "success")
    return redirect(url_for("admin_exams"))


@app.route("/admin/exams/questions/<int:qid>/delete", methods=["POST"])
@admin_required
def admin_delete_question(qid):
    q = models.get_question_by_id(qid)
    if q:
        models.delete_question(qid)
        flash("Question deleted.", "success")
        return redirect(url_for("admin_edit_exam", exam_id=q["exam_id"]))
    return redirect(url_for("admin_exams"))


@app.route("/admin/storage-status")
@admin_required
def admin_storage_status():
    """Quick health check: open /admin/storage-status while logged in as admin."""
    return jsonify(storage.health())


# ================= ADMIN: PRODUCT CRUD =================
@app.route("/admin/products")
@admin_required
def admin_products():
    return render_template("admin/products.html", products=models.get_products())


def _product_form(product):
    """Validate product form and store uploads. Returns (data, error)."""
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    category = request.form.get("category", "")
    content_type = request.form.get("content_type", "")
    try:
        price = round(float(request.form.get("price", "")), 2)
        if price < 0:
            raise ValueError
    except ValueError:
        return None, "Enter a valid price."

    if not title:
        return None, "Title is required."
    if category not in Config.CATEGORIES:
        return None, "Choose a valid category."
    if content_type not in ("pdf", "video", "both"):
        return None, "Choose a valid content type."

    thumb, pdf, video = (request.files.get(k) for k in ("thumbnail", "pdf_file", "video_file"))

    needs_pdf = content_type in ("pdf", "both")
    needs_video = content_type in ("video", "both")
    if needs_pdf and not (pdf and pdf.filename) and not (product and product["pdf_file"]):
        return None, "This content type needs a PDF file."
    if needs_video and not (video and video.filename) and not (product and product["video_file"]):
        return None, "This content type needs a video file."

    if thumb and thumb.filename:
        if not allowed_file(thumb.filename, Config.ALLOWED_IMAGE_EXTENSIONS):
            return None, "Thumbnail must be png/jpg/jpeg/webp."
    if pdf and pdf.filename:
        if not allowed_file(pdf.filename, Config.ALLOWED_PDF_EXTENSIONS):
            return None, "Only .pdf files allowed."
    if video and video.filename:
        if not allowed_file(video.filename, Config.ALLOWED_VIDEO_EXTENSIONS):
            return None, "Video must be mp4/webm/mov/mkv."

    thumb_n = storage.save_file(thumb, "thumbnail")
    pdf_n = storage.save_file(pdf, "pdf")
    video_n = storage.save_file(video, "video")

    if storage.cloudinary_enabled() and storage.get_last_error():
        flash("Cloud upload failed — file(s) saved locally instead. "
              "Reason: " + storage.get_last_error(), "error")

    return {
        "title": title, "description": description, "category": category,
        "price": price, "content_type": content_type,
        "thumbnail": thumb_n or (product["thumbnail"] if product else None),
        "pdf_file": pdf_n or (product["pdf_file"] if product else None),
        "video_file": video_n or (product["video_file"] if product else None),
    }, None


@app.route("/admin/products/add", methods=["GET", "POST"])
@admin_required
def admin_add_product():
    if request.method == "POST":
        data, error = _product_form(None)
        if error:
            flash(error, "error")
            return render_template("admin/product_form.html", product=None)
        models.create_product(**data)
        flash("Product added.", "success")
        return redirect(url_for("admin_products"))
    return render_template("admin/product_form.html", product=None)


@app.route("/admin/products/edit/<int:product_id>", methods=["GET", "POST"])
@admin_required
def admin_edit_product(product_id):
    product = models.get_product_by_id(product_id)
    if not product:
        abort(404)
    if request.method == "POST":
        data, error = _product_form(product)
        if error:
            flash(error, "error")
            return render_template("admin/product_form.html", product=product)
        models.update_product(product_id, **data)
        for key, kind in (("thumbnail", "thumbnail"),
                          ("pdf_file", "pdf"),
                          ("video_file", "video")):
            if data[key] != product[key]:
                storage.delete_file(kind, product[key])
        flash("Product updated.", "success")
        return redirect(url_for("admin_products"))
    return render_template("admin/product_form.html", product=product)


@app.route("/admin/products/delete/<int:product_id>", methods=["POST"])
@admin_required
def admin_delete_product(product_id):
    product = models.get_product_by_id(product_id)
    if product:
        storage.delete_file("thumbnail", product["thumbnail"])
        storage.delete_file("pdf", product["pdf_file"])
        storage.delete_file("video", product["video_file"])
        models.delete_product(product_id)
        flash("Product deleted.", "success")
    return redirect(url_for("admin_products"))


# ================= ADMIN: USERS & PURCHASE RECORDS =================
@app.route("/admin/users")
@admin_required
def admin_users():
    return render_template("admin/users.html", users=models.get_all_users())


@app.route("/admin/users/delete/<int:user_id>", methods=["POST"])
@admin_required
def admin_delete_user(user_id):
    models.delete_user(user_id)
    flash("User deleted.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/purchases")
@admin_required
def admin_purchases():
    purchases = models.get_all_purchases()
    return render_template("admin/purchases.html",
                           purchases=purchases,
                           total_revenue=sum(p["amount"] for p in purchases))


@app.route("/admin/orders")
@admin_required
def admin_orders_legacy():
    """Old URL — forwards to the purchase records page."""
    return redirect(url_for("admin_purchases"))


@app.route("/admin/purchases/delete/<int:purchase_id>", methods=["POST"])
@admin_required
def admin_delete_purchase(purchase_id):
    models.delete_purchase(purchase_id)
    flash("Purchase record removed.", "success")
    return redirect(url_for("admin_purchases"))


# ================= ERRORS =================
@app.errorhandler(404)
def nf(e):
    flash("Page not found.", "error")
    return redirect(url_for("home"))


@app.errorhandler(403)
def fbd(e):
    if request.path.startswith("/media/"):
        parts = request.path.strip("/").split("/")   # media/pdf/<id>
        if len(parts) >= 3 and parts[2].isdigit():
            flash("Please purchase this material to access its files.", "error")
            return redirect(url_for("product", product_id=int(parts[2])))
    if request.path.startswith("/api/"):
        return jsonify(error="Forbidden."), 403
    flash("You do not have access to this page.", "error")
    return redirect(url_for("home"))


@app.errorhandler(502)
def bgw(e):
    flash("Could not retrieve the file right now. Please try again or contact support.", "error")
    return redirect(request.referrer or url_for("home"))


@app.errorhandler(413)
def tl(e):
    flash("File too large (max 500 MB).", "error")
    return redirect(request.referrer or url_for("home"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)