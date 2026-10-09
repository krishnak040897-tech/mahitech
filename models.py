"""models.py — SQLite3 data layer."""
import sqlite3

from config import Config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT    UNIQUE NOT NULL,
    email       TEXT    UNIQUE NOT NULL,
    password    TEXT    NOT NULL,
    created_at  TEXT    DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS products (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT    NOT NULL,
    description  TEXT    DEFAULT '',
    category     TEXT    NOT NULL,
    price        REAL    NOT NULL DEFAULT 0,
    content_type TEXT    NOT NULL DEFAULT 'pdf',   -- pdf | video | both
    thumbnail    TEXT,                              -- Cloudinary URL or local filename
    pdf_file     TEXT,                              -- Cloudinary URL or local filename
    video_file   TEXT,                              -- Cloudinary URL or local filename
    created_at   TEXT    DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS purchases (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id)    ON DELETE CASCADE,
    product_id    INTEGER          REFERENCES products(id) ON DELETE SET NULL,
    product_title TEXT    NOT NULL DEFAULT '',
    amount        REAL    NOT NULL DEFAULT 0,
    payment_id    TEXT,
    order_id      TEXT,
    status        TEXT    NOT NULL DEFAULT 'paid',
    created_at    TEXT    DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS exams (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    title            TEXT    NOT NULL,
    description      TEXT    DEFAULT '',
    price            REAL    NOT NULL DEFAULT 0,     -- standalone purchasable paper
    duration_minutes INTEGER NOT NULL DEFAULT 15,
    pass_percent     INTEGER NOT NULL DEFAULT 40,
    created_at       TEXT    DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS exam_purchases (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    exam_id    INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    amount     REAL    NOT NULL DEFAULT 0,
    payment_id TEXT,
    order_id   TEXT,
    status     TEXT    NOT NULL DEFAULT 'paid',
    created_at TEXT    DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS exam_questions (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_id INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    opt_a   TEXT NOT NULL,
    opt_b   TEXT NOT NULL,
    opt_c   TEXT NOT NULL,
    opt_d   TEXT NOT NULL,
    correct TEXT NOT NULL CHECK (correct IN ('a','b','c','d'))
);
CREATE TABLE IF NOT EXISTS exam_attempts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    exam_id      INTEGER NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
    user_id      INTEGER NOT NULL REFERENCES users(id)  ON DELETE CASCADE,
    score        INTEGER NOT NULL DEFAULT 0,
    total        INTEGER NOT NULL DEFAULT 0,
    answers      TEXT    DEFAULT '',                  -- "12:a;13:c;..."
    status       TEXT    NOT NULL DEFAULT 'in_progress',  -- in_progress | submitted
    started_at   TEXT    DEFAULT (datetime('now', 'localtime')),
    submitted_at TEXT
);
"""

DEMO_PRODUCTS = [
    ("SSC CGL 2025 Complete Study Kit",
     "Chapter-wise notes, 5000+ practice questions and previous-year papers.",
     "Government Exams", 499, "pdf"),
    ("Bank PO Banking Awareness Crasher",
     "Banking awareness notes with static GK and 20 mock sets.",
     "Competitive Exams", 299, "pdf"),
    ("NEET UG Physics Formula Handbook",
     "Every formula and shortcut for NEET Physics, with solved examples.",
     "Entrance Exams", 199, "both"),
    ("Class 12 Mathematics Chapterwise Notes",
     "NCERT-aligned notes with solved board questions and MCQs.",
     "Academic Preparation", 249, "pdf"),
    ("Placement Prep Video Bundle",
     "Aptitude, reasoning and interview video sessions.",
     "Career Preparation", 349, "video"),
]


def get_db():
    conn = sqlite3.connect(Config.DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(seed=False):
    conn = get_db()
    # ---- one-time migration: exams were product-linked before; they are now
    # standalone purchasable papers. Reset the old exam tables if detected. ----
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(exams)").fetchall()]
        if cols and "product_id" in cols:
            conn.execute("DROP TABLE IF EXISTS exam_attempts")
            conn.execute("DROP TABLE IF EXISTS exam_questions")
            conn.execute("DROP TABLE IF EXISTS exam_purchases")
            conn.execute("DROP TABLE IF EXISTS exams")
            print("[MahiTech] Migrated exam schema: exams are now standalone "
                  "purchasable papers (old product-linked exam data was reset).")
    except sqlite3.OperationalError:
        pass   # table doesn't exist yet — nothing to migrate
    conn.executescript(SCHEMA)
    conn.commit()
    if seed and conn.execute("SELECT COUNT(*) AS c FROM products").fetchone()["c"] == 0:
        conn.executemany(
            "INSERT INTO products (title, description, category, price, content_type) "
            "VALUES (?, ?, ?, ?, ?)", DEMO_PRODUCTS)
        conn.commit()
    conn.close()


# ---------------- USERS ----------------
def create_user(username, email, password_hash):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO users (username, email, password) VALUES (?, ?, ?)",
        (username, email, password_hash))
    conn.commit()
    uid = cur.lastrowid
    conn.close()
    return uid


def get_user_by_id(user_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return row


def get_user_by_username(username):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    return row


def get_user_by_email(email):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    return row


def get_user_by_username_or_email(identifier):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM users WHERE username = ? OR LOWER(email) = LOWER(?)",
        (identifier, identifier)).fetchone()
    conn.close()
    return row


def get_all_users():
    conn = get_db()
    rows = conn.execute(
        """SELECT u.*, COUNT(p.id) AS purchase_count,
                  COALESCE(SUM(p.amount), 0) AS total_spent
           FROM users u LEFT JOIN purchases p ON p.user_id = u.id
           GROUP BY u.id ORDER BY u.id DESC""").fetchall()
    conn.close()
    return rows


def delete_user(user_id):
    conn = get_db()
    conn.execute("DELETE FROM purchases WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


def count_users():
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    conn.close()
    return n


# ---------------- PRODUCTS ----------------
def create_product(title, description, category, price, content_type,
                   thumbnail=None, pdf_file=None, video_file=None):
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO products
           (title, description, category, price, content_type, thumbnail, pdf_file, video_file)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (title, description, category, price, content_type,
         thumbnail, pdf_file, video_file))
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return pid


def update_product(product_id, title, description, category, price, content_type,
                   thumbnail=None, pdf_file=None, video_file=None):
    conn = get_db()
    conn.execute(
        """UPDATE products SET title=?, description=?, category=?, price=?,
           content_type=?, thumbnail=?, pdf_file=?, video_file=? WHERE id=?""",
        (title, description, category, price, content_type,
         thumbnail, pdf_file, video_file, product_id))
    conn.commit()
    conn.close()


def get_product_by_id(product_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    conn.close()
    return row


def get_products(search=None, category=None, limit=None):
    sql, params = "SELECT * FROM products WHERE 1=1", []
    if search:
        sql += " AND (LOWER(title) LIKE ? OR LOWER(description) LIKE ?)"
        params += [f"%{search.lower()}%"] * 2
    if category:
        sql += " AND category = ?"
        params.append(category)
    sql += " ORDER BY id DESC"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    conn = get_db()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


def set_product_file(product_id, column, value):
    """Point one file column at a new reference (used by migrate_to_cloudinary.py)."""
    if column not in ("thumbnail", "pdf_file", "video_file"):
        raise ValueError("bad column")
    conn = get_db()
    conn.execute(f"UPDATE products SET {column}=? WHERE id=?", (value, product_id))
    conn.commit()
    conn.close()


def delete_product(product_id):
    conn = get_db()
    conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
    conn.commit()
    conn.close()


def count_products():
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) AS c FROM products").fetchone()["c"]
    conn.close()
    return n


# ---------------- PURCHASES (PRODUCTS) ----------------
def create_purchase(user_id, product_id, product_title, amount,
                    payment_id, order_id, status="paid"):
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO purchases
           (user_id, product_id, product_title, amount, payment_id, order_id, status)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (user_id, product_id, product_title, amount, payment_id, order_id, status))
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return pid


def has_purchased(user_id, product_id):
    conn = get_db()
    row = conn.execute(
        "SELECT 1 FROM purchases WHERE user_id = ? AND product_id = ? LIMIT 1",
        (user_id, product_id)).fetchone()
    conn.close()
    return row is not None


def get_user_purchases(user_id):
    conn = get_db()
    rows = conn.execute(
        """SELECT pu.*, pr.category, pr.content_type, pr.thumbnail
           FROM purchases pu LEFT JOIN products pr ON pr.id = pu.product_id
           WHERE pu.user_id = ? ORDER BY pu.id DESC""", (user_id,)).fetchall()
    conn.close()
    return rows


def get_all_purchases(limit=None):
    sql = """SELECT pu.*, u.username, u.email FROM purchases pu
             LEFT JOIN users u ON u.id = pu.user_id ORDER BY pu.id DESC"""
    params = []
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    conn = get_db()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


def delete_purchase(purchase_id):
    conn = get_db()
    conn.execute("DELETE FROM purchases WHERE id = ?", (purchase_id,))
    conn.commit()
    conn.close()


def count_purchases():
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) AS c FROM purchases").fetchone()["c"]
    conn.close()
    return n


def total_revenue():
    conn = get_db()
    n = conn.execute("SELECT COALESCE(SUM(amount),0) AS s FROM purchases").fetchone()["s"]
    conn.close()
    return n


# ---------------- EXAMS (standalone purchasable papers) ----------------
def create_exam(title, description, price, duration_minutes, pass_percent):
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO exams (title, description, price, duration_minutes, pass_percent)
           VALUES (?, ?, ?, ?, ?)""",
        (title, description, price, duration_minutes, pass_percent))
    conn.commit()
    eid = cur.lastrowid
    conn.close()
    return eid


def update_exam(exam_id, title, description, price, duration_minutes, pass_percent):
    conn = get_db()
    conn.execute(
        """UPDATE exams SET title=?, description=?, price=?,
           duration_minutes=?, pass_percent=? WHERE id=?""",
        (title, description, price, duration_minutes, pass_percent, exam_id))
    conn.commit()
    conn.close()


def delete_exam(exam_id):
    conn = get_db()
    conn.execute("DELETE FROM exams WHERE id = ?", (exam_id,))
    conn.commit()
    conn.close()


def get_exam_by_id(exam_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM exams WHERE id = ?", (exam_id,)).fetchone()
    conn.close()
    return row


def get_exams():
    """All exam papers (public browse + admin), with question counts."""
    conn = get_db()
    rows = conn.execute(
        """SELECT e.*,
                  (SELECT COUNT(*) FROM exam_questions q WHERE q.exam_id = e.id) AS q_count
           FROM exams e ORDER BY e.id DESC""").fetchall()
    conn.close()
    return rows


def count_exams():
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) AS c FROM exams").fetchone()["c"]
    conn.close()
    return n


# ---------------- EXAM PURCHASES ----------------
def create_exam_purchase(user_id, exam_id, amount, payment_id, order_id, status="paid"):
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO exam_purchases
           (user_id, exam_id, amount, payment_id, order_id, status)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, exam_id, amount, payment_id, order_id, status))
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return pid


def has_purchased_exam(user_id, exam_id):
    conn = get_db()
    row = conn.execute(
        "SELECT 1 FROM exam_purchases WHERE user_id = ? AND exam_id = ? LIMIT 1",
        (user_id, exam_id)).fetchone()
    conn.close()
    return row is not None


def get_owned_exam_ids(user_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT exam_id FROM exam_purchases WHERE user_id = ?", (user_id,)).fetchall()
    conn.close()
    return {r["exam_id"] for r in rows}


def get_purchased_exams(user_id):
    """Exams the user bought, with best submitted score and active-attempt flag."""
    conn = get_db()
    rows = conn.execute(
        """SELECT e.*,
                  (SELECT COUNT(*) FROM exam_questions q WHERE q.exam_id = e.id) AS q_count,
                  (SELECT MAX(a.score) FROM exam_attempts a
                    WHERE a.exam_id = e.id AND a.user_id = ? AND a.status = 'submitted'
                  ) AS best_score,
                  (SELECT MAX(a.total) FROM exam_attempts a
                    WHERE a.exam_id = e.id AND a.user_id = ? AND a.status = 'submitted'
                  ) AS best_total,
                  (SELECT COUNT(*) FROM exam_attempts a
                    WHERE a.exam_id = e.id AND a.user_id = ? AND a.status = 'in_progress'
                  ) AS has_active
           FROM exams e
           JOIN exam_purchases ep ON ep.exam_id = e.id AND ep.user_id = ?
           GROUP BY e.id ORDER BY e.id DESC""",
        (user_id, user_id, user_id, user_id)).fetchall()
    conn.close()
    return rows


# ---------------- EXAM QUESTIONS ----------------
def add_question(exam_id, question, a, b, c, d, correct):
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO exam_questions (exam_id, question, opt_a, opt_b, opt_c, opt_d, correct)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (exam_id, question, a, b, c, d, correct))
    conn.commit()
    qid = cur.lastrowid
    conn.close()
    return qid


def update_question(qid, question, a, b, c, d, correct):
    conn = get_db()
    conn.execute(
        """UPDATE exam_questions SET question=?, opt_a=?, opt_b=?, opt_c=?, opt_d=?, correct=?
           WHERE id=?""", (question, a, b, c, d, correct, qid))
    conn.commit()
    conn.close()


def delete_question(qid):
    conn = get_db()
    conn.execute("DELETE FROM exam_questions WHERE id = ?", (qid,))
    conn.commit()
    conn.close()


def get_question_by_id(qid):
    conn = get_db()
    row = conn.execute("SELECT * FROM exam_questions WHERE id = ?", (qid,)).fetchone()
    conn.close()
    return row


def get_questions(exam_id):
    """Full rows (includes 'correct') — server-side grading only, never send to client."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM exam_questions WHERE exam_id = ? ORDER BY id", (exam_id,)).fetchall()
    conn.close()
    return rows


def get_questions_public(exam_id):
    """Client-safe rows for the exam-taking page (no correct answer)."""
    conn = get_db()
    rows = conn.execute(
        """SELECT id, exam_id, question, opt_a, opt_b, opt_c, opt_d
           FROM exam_questions WHERE exam_id = ? ORDER BY id""", (exam_id,)).fetchall()
    conn.close()
    return rows


def count_questions(exam_id):
    conn = get_db()
    n = conn.execute(
        "SELECT COUNT(*) AS c FROM exam_questions WHERE exam_id = ?", (exam_id,)).fetchone()["c"]
    conn.close()
    return n


# ---------------- EXAM ATTEMPTS ----------------
def create_attempt(exam_id, user_id):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO exam_attempts (exam_id, user_id) VALUES (?, ?)", (exam_id, user_id))
    conn.commit()
    aid = cur.lastrowid
    conn.close()
    return aid


def get_attempt_by_id(attempt_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM exam_attempts WHERE id = ?", (attempt_id,)).fetchone()
    conn.close()
    return row


def get_active_attempt(exam_id, user_id):
    conn = get_db()
    row = conn.execute(
        """SELECT * FROM exam_attempts WHERE exam_id = ? AND user_id = ?
           AND status = 'in_progress' ORDER BY id DESC LIMIT 1""",
        (exam_id, user_id)).fetchone()
    conn.close()
    return row


def finish_attempt(attempt_id, score, total, answers):
    conn = get_db()
    conn.execute(
        """UPDATE exam_attempts SET score=?, total=?, answers=?, status='submitted',
           submitted_at=datetime('now','localtime') WHERE id=?""",
        (score, total, answers, attempt_id))
    conn.commit()
    conn.close()


def get_user_attempts(exam_id, user_id):
    conn = get_db()
    rows = conn.execute(
        """SELECT * FROM exam_attempts WHERE exam_id = ? AND user_id = ?
           AND status = 'submitted' ORDER BY id DESC""",
        (exam_id, user_id)).fetchall()
    conn.close()
    return rows