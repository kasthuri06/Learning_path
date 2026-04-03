"""
AI Personalized Learning Platform - Flask application.

Features: email/password + Google OAuth, save/manage/archive paths,
dashboard with all paths, milestone notes, resource checkboxes,
popup AI chatbot (general, path_creation, research), 4-stage generation UI,
collapsible skills DB, username check, password strength, theme toggle.
"""

import json
import os
import sqlite3
import csv
import io
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from generator import generate_roadmap

# Optional Google OAuth
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
APP_DIR = Path(__file__).resolve().parent
DATABASE = APP_DIR / "database.db"
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"

# Load .env if present
_env_path = APP_DIR / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        if "=" in _line and not _line.startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

app = Flask(__name__, template_folder=str(APP_DIR / "templates"), static_folder=str(APP_DIR / "static"))
app.secret_key = SECRET_KEY
app.config["DATABASE"] = str(DATABASE)

# -----------------------------------------------------------------------------
# Database helpers
# -----------------------------------------------------------------------------


def get_db() -> sqlite3.Connection:
    """Return a DB connection. Creates the database file and tables if needed."""
    db = sqlite3.connect(app.config["DATABASE"])
    db.row_factory = sqlite3.Row
    return db


def _table_columns(conn, table):
    cur = conn.execute("PRAGMA table_info(%s)" % table)
    return [row[1] for row in cur.fetchall()]


def init_db() -> None:
    """Create and migrate tables: users (incl. google_id), progress (roadmap_id, notes), user_roadmap (name, target_role, archived), resource_progress, weekly_focus, topic_sessions, comments, topic_tags, resource_meta."""
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password TEXT,
                google_id TEXT UNIQUE
            );
            CREATE TABLE IF NOT EXISTS progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                roadmap_id INTEGER,
                topic TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'completed',
                date_completed DATE,
                notes TEXT,
                FOREIGN KEY (user_id) REFERENCES users (id)
            );
            CREATE TABLE IF NOT EXISTS user_roadmap (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                roadmap_json TEXT NOT NULL,
                domain TEXT,
                name TEXT,
                target_role TEXT,
                weekly_hours INTEGER,
                goal_date TEXT,
                archived INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users (id)
            );
            CREATE TABLE IF NOT EXISTS resource_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                roadmap_id INTEGER NOT NULL,
                topic TEXT NOT NULL,
                resource_url TEXT NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0,
                UNIQUE(user_id, roadmap_id, topic, resource_url)
            );
            CREATE TABLE IF NOT EXISTS review_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                roadmap_id INTEGER NOT NULL,
                topic TEXT NOT NULL,
                due_date TEXT NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0,
                completed_at TEXT,
                UNIQUE(user_id, roadmap_id, topic, due_date)
            );
            CREATE TABLE IF NOT EXISTS share_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                roadmap_id INTEGER NOT NULL,
                token TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0,
                UNIQUE(user_id, roadmap_id)
            );
            CREATE TABLE IF NOT EXISTS weekly_focus (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                roadmap_id INTEGER NOT NULL,
                topic TEXT NOT NULL,
                week_start TEXT NOT NULL,
                UNIQUE(user_id, roadmap_id, topic, week_start)
            );
            CREATE TABLE IF NOT EXISTS topic_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                roadmap_id INTEGER,
                topic TEXT NOT NULL,
                start_ts TEXT NOT NULL,
                end_ts TEXT
            );
            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                roadmap_id INTEGER NOT NULL,
                topic TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS topic_tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                roadmap_id INTEGER NOT NULL,
                topic TEXT NOT NULL,
                tag TEXT NOT NULL,
                UNIQUE(user_id, roadmap_id, topic, tag)
            );
            CREATE TABLE IF NOT EXISTS resource_meta (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                roadmap_id INTEGER NOT NULL,
                topic TEXT NOT NULL,
                resource_url TEXT NOT NULL,
                duration_min INTEGER,
                difficulty TEXT,
                UNIQUE(user_id, roadmap_id, topic, resource_url)
            );
        """)
        conn.commit()
        for table, col, sql in [
            ("users", "google_id", "ALTER TABLE users ADD COLUMN google_id TEXT"),
            ("user_roadmap", "name", "ALTER TABLE user_roadmap ADD COLUMN name TEXT"),
            ("user_roadmap", "target_role", "ALTER TABLE user_roadmap ADD COLUMN target_role TEXT"),
            ("user_roadmap", "weekly_hours", "ALTER TABLE user_roadmap ADD COLUMN weekly_hours INTEGER"),
            ("user_roadmap", "goal_date", "ALTER TABLE user_roadmap ADD COLUMN goal_date TEXT"),
            ("user_roadmap", "archived", "ALTER TABLE user_roadmap ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"),
            ("progress", "roadmap_id", "ALTER TABLE progress ADD COLUMN roadmap_id INTEGER"),
            ("progress", "notes", "ALTER TABLE progress ADD COLUMN notes TEXT"),
        ]:
            if sql and col not in _table_columns(conn, table):
                try:
                    conn.execute(sql)
                    conn.commit()
                except sqlite3.OperationalError:
                    pass
        # Indexes
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_progress_user_roadmap_status ON progress(user_id, roadmap_id, status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_review_tasks_user_due ON review_tasks(user_id, completed, due_date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_resource_progress_user ON resource_progress(user_id, roadmap_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_weekly_focus_user_week ON weekly_focus(user_id, week_start)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_topic_sessions_user ON topic_sessions(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_comments_roadmap ON comments(roadmap_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_topic_tags_user ON topic_tags(user_id, roadmap_id, topic)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_resource_meta_user ON resource_meta(user_id, roadmap_id, topic)")
            conn.commit()
        except sqlite3.OperationalError:
            pass


def get_available_domains() -> list:
    """Load domain names from the platform knowledge base JSON."""
    try:
        kb_path = APP_DIR / "knowledge_base.json"
        with kb_path.open("r", encoding="utf-8") as f:
            kb = json.load(f)
        return sorted(list(kb.keys()))
    except Exception:
        return ["AI", "Data Science", "Web Development", "Cloud Computing"]


# -----------------------------------------------------------------------------
# Auth helpers
# -----------------------------------------------------------------------------


def require_login(f):
    """Decorator: redirect to login if user not in session."""
    from functools import wraps

    @wraps(f)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return wrapped


# -----------------------------------------------------------------------------
# Progress & streak
# -----------------------------------------------------------------------------


def get_user_roadmap_topics(user_id: int, roadmap_id: int = None) -> list:
    """Return list of topic names from the user's most recent (or specified) saved roadmap."""
    with get_db() as conn:
        if roadmap_id:
            row = conn.execute(
                "SELECT roadmap_json FROM user_roadmap WHERE id = ? AND user_id = ?",
                (roadmap_id, user_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT roadmap_json FROM user_roadmap WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
    if not row:
        return []
    try:
        roadmap = json.loads(row["roadmap_json"])
        return [w.get("topic") or "" for w in roadmap if w.get("topic")]
    except (json.JSONDecodeError, TypeError):
        return []


def get_paths(user_id: int, archived: bool = False) -> list:
    """Return list of path dicts (id, name, domain, target_role, created_at, progress_pct, archived)."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, domain, target_role, created_at, roadmap_json, weekly_hours, goal_date, archived FROM user_roadmap WHERE user_id = ? AND archived = ? ORDER BY id DESC",
            (user_id, 1 if archived else 0),
        ).fetchall()
        out = []
        for r in rows:
            topics = []
            try:
                topics = [w.get("topic") or "" for w in json.loads(r["roadmap_json"]) if w.get("topic")]
            except (json.JSONDecodeError, TypeError):
                pass
            cur = conn.execute(
                "SELECT COUNT(DISTINCT topic) FROM progress WHERE user_id = ? AND roadmap_id = ? AND status = 'completed'",
                (user_id, r["id"]),
            )
            completed = cur.fetchone()[0] if r["id"] else 0
            total = len(topics)
            pct = round(100 * completed / total, 1) if total else 0
            weekly_hours = r["weekly_hours"] if r["weekly_hours"] is not None else 5
            goal_date = (r["goal_date"] or "").strip()
            goal_status = compute_goal_status(r["created_at"], weekly_hours, goal_date, total, completed)
            out.append({
                "id": r["id"],
                "name": r["name"] or ("Path " + str(r["id"])),
                "domain": r["domain"] or "",
                "target_role": r["target_role"] or "Learner",
                "created_at": r["created_at"],
                "progress_pct": pct,
                "total_topics": total,
                "completed_topics": completed,
                "weekly_hours": weekly_hours,
                "goal_date": goal_date,
                **goal_status,
            })
        return out


def get_roadmap_by_id(roadmap_id: int, user_id: int):
    """Return roadmap row (id, name, domain, target_role, roadmap_json) or None."""
    with get_db() as conn:
        return conn.execute(
            "SELECT id, name, domain, target_role, weekly_hours, goal_date, created_at, roadmap_json FROM user_roadmap WHERE id = ? AND user_id = ?",
            (roadmap_id, user_id),
        ).fetchone()


def get_completed_topics(user_id: int, roadmap_id: int = None) -> list:
    """Return list of completed topics (and notes) for this user, optionally for one roadmap."""
    with get_db() as conn:
        if roadmap_id:
            rows = conn.execute(
                "SELECT topic, date_completed, notes FROM progress WHERE user_id = ? AND roadmap_id = ? AND status = 'completed' ORDER BY date_completed DESC",
                (user_id, roadmap_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT topic, date_completed, notes FROM progress WHERE user_id = ? AND status = 'completed' ORDER BY date_completed DESC",
                (user_id,),
            ).fetchall()
    return [{"topic": r["topic"], "date_completed": r["date_completed"], "notes": r["notes"] or ""} for r in rows]


def get_milestone_notes(user_id: int, roadmap_id: int) -> dict:
    """Return dict topic -> notes for this roadmap."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT topic, notes FROM progress WHERE user_id = ? AND roadmap_id = ? AND (notes IS NOT NULL AND notes != '')",
            (user_id, roadmap_id),
        ).fetchall()
    return {r["topic"]: (r["notes"] or "") for r in rows}


def get_resource_progress(user_id: int, roadmap_id: int) -> set:
    """Return set of (topic, resource_url) tuples that are marked completed."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT topic, resource_url FROM resource_progress WHERE user_id = ? AND roadmap_id = ? AND completed = 1",
            (user_id, roadmap_id),
        ).fetchall()
    return {(r["topic"], r["resource_url"]) for r in rows}


def get_streak(user_id: int) -> int:
    """
    Compute current learning streak: consecutive days (ending today) with at least one completion.
    """
    with get_db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT date_completed FROM progress WHERE user_id = ? AND status = 'completed' AND date_completed IS NOT NULL ORDER BY date_completed DESC",
            (user_id,),
        ).fetchall()
    dates = [r["date_completed"] for r in rows]
    if not dates:
        return 0
    seen = set()
    for d in dates:
        if isinstance(d, datetime):
            seen.add(d.date().isoformat())
        else:
            seen.add(str(d)[:10])
    today = datetime.utcnow().date().isoformat()
    streak = 0
    expect = today
    while expect in seen:
        streak += 1
        expect = (datetime.fromisoformat(expect).date() - timedelta(days=1)).isoformat()
    return streak


def get_completions_per_week(user_id: int, weeks: int = 8) -> list:
    """Return counts of completions per week for the last `weeks` weeks (for Chart.js)."""
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT date_completed, COUNT(*) AS cnt
            FROM progress
            WHERE user_id = ? AND status = 'completed' AND date_completed IS NOT NULL
            AND date_completed >= date('now', ?)
            GROUP BY date(date_completed)
            ORDER BY date_completed
            """,
            (user_id, f"-{weeks} weeks"),
        ).fetchall()
    # Aggregate by week (Monday start)
    from collections import defaultdict
    week_counts = defaultdict(int)
    for r in rows:
        d = r["date_completed"]
        if isinstance(d, str):
            try:
                dt = datetime.fromisoformat(d[:10])
            except ValueError:
                continue
        else:
            dt = d
        # Week key: year and week number
        year, week, _ = dt.isocalendar()
        week_counts[f"{year}-W{week:02d}"] += r["cnt"]
    return [{"week": k, "count": v} for k, v in sorted(week_counts.items())]


def _week_start(date_obj=None) -> str:
    d = (date_obj or datetime.utcnow().date())
    monday = d - timedelta(days=d.weekday())
    return monday.isoformat()


def get_weekly_focus(user_id: int) -> list:
    ws = _week_start()
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT wf.roadmap_id, wf.topic, ur.name
            FROM weekly_focus wf
            JOIN user_roadmap ur ON ur.id = wf.roadmap_id
            WHERE wf.user_id = ? AND wf.week_start = ? AND ur.archived = 0
            ORDER BY wf.id DESC
            """,
            (user_id, ws),
        ).fetchall()
    return [{"roadmap_id": r["roadmap_id"], "topic": r["topic"], "path_name": (r["name"] or ("Path " + str(r["roadmap_id"])))} for r in rows]

def get_weekly_focus_for(user_id: int, roadmap_id: int) -> set:
    ws = _week_start()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT topic FROM weekly_focus WHERE user_id = ? AND roadmap_id = ? AND week_start = ?",
            (user_id, roadmap_id, ws),
        ).fetchall()
    return {r["topic"] for r in rows}

def get_topic_total_minutes(user_id: int, roadmap_id: int, topic: str) -> int:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT start_ts, end_ts FROM topic_sessions WHERE user_id = ? AND (roadmap_id IS NULL OR roadmap_id = ?) AND topic = ?",
            (user_id, roadmap_id or 0, topic),
        ).fetchall()
    total = 0
    for r in rows:
        try:
            start = datetime.fromisoformat((r["start_ts"] or "")[:19])
            end = datetime.fromisoformat((r["end_ts"] or "")[:19]) if r["end_ts"] else datetime.utcnow()
            total += int((end - start).total_seconds() // 60)
        except Exception:
            continue
    return total


def compute_goal_status(created_at: str, weekly_hours: int, goal_date: str, total_topics: int, completed_topics: int) -> dict:
    try:
        start = datetime.fromisoformat((created_at or "")[:19]).date()
    except Exception:
        start = datetime.utcnow().date()
    today = datetime.utcnow().date()
    days_elapsed = max(0, (today - start).days)
    weeks_elapsed = days_elapsed / 7.0
    hours_per_topic = 2.0
    weekly_hours = max(1, min(40, int(weekly_hours or 5)))
    topics_per_week = max(0.5, weekly_hours / hours_per_topic)
    expected = int(min(total_topics, round(weeks_elapsed * topics_per_week)))
    behind_by = max(0, expected - int(completed_topics or 0))
    days_left = None
    if goal_date:
        try:
            gd = datetime.fromisoformat(goal_date[:10]).date()
            days_left = (gd - today).days
        except Exception:
            days_left = None
    return {"expected_completed": expected, "behind_by": behind_by, "days_left": days_left}


def get_due_reviews(user_id: int, limit: int = 8) -> list:
    today = datetime.utcnow().date().isoformat()
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT rt.id, rt.roadmap_id, rt.topic, rt.due_date, ur.name
            FROM review_tasks rt
            JOIN user_roadmap ur ON ur.id = rt.roadmap_id
            WHERE rt.user_id = ? AND rt.completed = 0 AND rt.due_date <= ? AND ur.archived = 0
            ORDER BY rt.due_date ASC, rt.id ASC
            LIMIT ?
            """,
            (user_id, today, limit),
        ).fetchall()
    return [{"id": r["id"], "roadmap_id": r["roadmap_id"], "topic": r["topic"], "due_date": r["due_date"], "path_name": (r["name"] or ("Path " + str(r["roadmap_id"])))} for r in rows]


def get_next_best_topics(user_id: int, streak: int, limit: int = 3) -> list:
    paths = get_paths(user_id, archived=False)
    if not paths:
        return []
    active = paths[0]
    roadmap_id = active["id"]
    weekly_hours = int(active.get("weekly_hours") or 5)
    count = 1 if weekly_hours <= 3 else 2 if weekly_hours <= 8 else 3
    if streak >= 7:
        count = min(3, count + 1)
    count = min(limit, max(1, count))
    row = get_roadmap_by_id(roadmap_id, user_id)
    if not row:
        return []
    try:
        roadmap = json.loads(row["roadmap_json"]) if row["roadmap_json"] else []
    except (json.JSONDecodeError, TypeError):
        roadmap = []
    completed = get_completed_topics(user_id, roadmap_id)
    completed_set = {c["topic"] for c in completed}
    out = []
    for w in roadmap:
        topic = (w.get("topic") or "").strip()
        if not topic or topic in completed_set:
            continue
        out.append({"topic": topic, "roadmap_id": roadmap_id, "path_name": active.get("name") or ("Path " + str(roadmap_id))})
        if len(out) >= count:
            break
    return out


def get_or_create_share_token(user_id: int, roadmap_id: int) -> str:
    with get_db() as conn:
        row = conn.execute(
            "SELECT token FROM share_tokens WHERE user_id = ? AND roadmap_id = ? AND revoked = 0",
            (user_id, roadmap_id),
        ).fetchone()
        if row and row["token"]:
            return row["token"]
        token = secrets.token_urlsafe(24)
        conn.execute(
            "INSERT OR REPLACE INTO share_tokens (user_id, roadmap_id, token, created_at, revoked) VALUES (?, ?, ?, ?, 0)",
            (user_id, roadmap_id, token, datetime.utcnow().isoformat()),
        )
        conn.commit()
        return token


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------


@app.route("/")
def index():
    """Redirect to dashboard if logged in, else login."""
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    """Serve login form; on POST validate (username or email) and set session then redirect to dashboard."""
    if request.method == "GET":
        return render_template("login.html", google_oauth=bool(GOOGLE_CLIENT_ID and HAS_REQUESTS))
    username_or_email = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    if not username_or_email or not password:
        return render_template("login.html", error="Username/email and password are required.", google_oauth=bool(GOOGLE_CLIENT_ID and HAS_REQUESTS))
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT id, username, password FROM users WHERE (username = ? OR email = ?) AND password IS NOT NULL",
                (username_or_email, username_or_email),
            ).fetchone()
        if not row or not check_password_hash(row["password"], password):
            return render_template("login.html", error="Invalid username/email or password.", google_oauth=bool(GOOGLE_CLIENT_ID and HAS_REQUESTS))
        session["user_id"] = row["id"]
        session["username"] = row["username"]
        return redirect(url_for("dashboard"))
    except sqlite3.Error as e:
        return render_template("login.html", error="Database error: %s" % e, google_oauth=bool(GOOGLE_CLIENT_ID and HAS_REQUESTS))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    """Serve signup form; on POST create user and redirect to login."""
    if request.method == "GET":
        return render_template("signup.html")
    username = (request.form.get("username") or "").strip()
    email = (request.form.get("email") or "").strip()
    password = request.form.get("password") or ""
    if not username or not email or not password:
        return render_template("signup.html", error="Username, email, and password are required.")
    if len(password) < 4:
        return render_template("signup.html", error="Password must be at least 4 characters.")
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO users (username, email, password) VALUES (?, ?, ?)",
                (username, email, generate_password_hash(password)),
            )
            conn.commit()
        return redirect(url_for("login"))
    except sqlite3.IntegrityError:
        return render_template("signup.html", error="Username or email already in use.")
    except sqlite3.Error as e:
        return render_template("signup.html", error=f"Database error: {e}")


@app.route("/logout")
def logout():
    """Clear session and redirect to login."""
    session.clear()
    return redirect(url_for("login"))


@app.route("/api/check-username")
def api_check_username():
    """Return JSON { available: bool } for real-time username validation."""
    username = (request.args.get("username") or "").strip()
    if not username:
        return jsonify({"available": False})
    with get_db() as conn:
        row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    return jsonify({"available": row is None})


@app.route("/api/chat", methods=["POST"])
@require_login
def api_chat():
    """Rule-based chatbot. JSON body: { message, mode, context }. Returns { reply, context }."""
    try:
        data = request.get_json() or {}
        message = (data.get("message") or "").strip()
        mode = (data.get("mode") or "general").strip().lower()
        if mode not in ("general", "path_creation", "research"):
            mode = "general"
        context = data.get("context") or {}
        from chatbot import handle_chat
        result = handle_chat(message, mode, context)
        return jsonify(result)
    except Exception as e:
        return jsonify({"reply": "Sorry, something went wrong.", "context": {}}), 500


@app.route("/api/resource_toggle", methods=["POST"])
@require_login
def api_resource_toggle():
    """Toggle resource completion. JSON: roadmap_id, topic, resource_url, completed (bool)."""
    data = request.get_json() or request.form
    roadmap_id = data.get("roadmap_id")
    topic = (data.get("topic") or "").strip()
    resource_url = (data.get("resource_url") or "").strip()
    completed = data.get("completed")
    if completed in (True, "true", "1", 1):
        completed = 1
    else:
        completed = 0
    if not roadmap_id or not topic or not resource_url:
        return jsonify({"ok": False}), 400
    try:
        roadmap_id = int(roadmap_id)
    except (TypeError, ValueError):
        return jsonify({"ok": False}), 400
    user_id = session["user_id"]
    with get_db() as conn:
        cur = conn.execute(
            "SELECT id FROM resource_progress WHERE user_id = ? AND roadmap_id = ? AND topic = ? AND resource_url = ?",
            (user_id, roadmap_id, topic, resource_url),
        )
        row = cur.fetchone()
        if row:
            conn.execute(
                "UPDATE resource_progress SET completed = ? WHERE id = ?",
                (completed, row["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO resource_progress (user_id, roadmap_id, topic, resource_url, completed) VALUES (?, ?, ?, ?, ?)",
                (user_id, roadmap_id, topic, resource_url, completed),
            )
        conn.commit()
    return jsonify({"ok": True})


@app.route("/api/milestone_notes", methods=["POST"])
@require_login
def api_milestone_notes():
    """Save milestone notes. JSON or form: roadmap_id, topic, notes."""
    data = request.get_json() or request.form
    roadmap_id = data.get("roadmap_id")
    topic = (data.get("topic") or "").strip()
    notes = (data.get("notes") or "").strip()
    if not roadmap_id or not topic:
        return jsonify({"ok": False}), 400
    try:
        roadmap_id = int(roadmap_id)
    except (TypeError, ValueError):
        return jsonify({"ok": False}), 400
    user_id = session["user_id"]
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM progress WHERE user_id = ? AND roadmap_id = ? AND topic = ?",
            (user_id, roadmap_id, topic),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE progress SET notes = ? WHERE user_id = ? AND roadmap_id = ? AND topic = ?",
                (notes, user_id, roadmap_id, topic),
            )
        else:
            conn.execute(
                "INSERT INTO progress (user_id, roadmap_id, topic, status, date_completed, notes) VALUES (?, ?, ?, 'notes', date('now'), ?)",
                (user_id, roadmap_id, topic, notes),
            )
        conn.commit()
    return jsonify({"ok": True})


@app.route("/api/archive_path", methods=["POST"])
@require_login
def api_archive_path():
    """Archive or unarchive a path. JSON or form: roadmap_id, archived (1 or 0)."""
    data = request.get_json() or request.form
    roadmap_id = data.get("roadmap_id")
    archived = data.get("archived", 1)
    if archived in (True, "true", "1", 1):
        archived = 1
    else:
        archived = 0
    if not roadmap_id:
        return jsonify({"ok": False}), 400
    try:
        roadmap_id = int(roadmap_id)
    except (TypeError, ValueError):
        return jsonify({"ok": False}), 400
    user_id = session["user_id"]
    with get_db() as conn:
        conn.execute(
            "UPDATE user_roadmap SET archived = ? WHERE id = ? AND user_id = ?",
            (archived, roadmap_id, user_id),
        )
        conn.commit()
    return jsonify({"ok": True})


@app.route("/api/rename_path", methods=["POST"])
@require_login
def api_rename_path():
    data = request.get_json() or request.form
    roadmap_id = data.get("roadmap_id")
    name = (data.get("name") or "").strip()
    if not roadmap_id or not name:
        return jsonify({"ok": False}), 400
    try:
        roadmap_id = int(roadmap_id)
    except (TypeError, ValueError):
        return jsonify({"ok": False}), 400
    user_id = session["user_id"]
    with get_db() as conn:
        cur = conn.execute("UPDATE user_roadmap SET name = ? WHERE id = ? AND user_id = ?", (name[:80], roadmap_id, user_id))
        conn.commit()
    return jsonify({"ok": True})


@app.route("/api/clone_path", methods=["POST"])
@require_login
def api_clone_path():
    data = request.get_json() or request.form
    roadmap_id = data.get("roadmap_id")
    if not roadmap_id:
        return jsonify({"ok": False}), 400
    try:
        roadmap_id = int(roadmap_id)
    except (TypeError, ValueError):
        return jsonify({"ok": False}), 400
    user_id = session["user_id"]
    with get_db() as conn:
        row = conn.execute(
            "SELECT roadmap_json, domain, name, target_role, weekly_hours, goal_date FROM user_roadmap WHERE id = ? AND user_id = ?",
            (roadmap_id, user_id),
        ).fetchone()
        if not row:
            return jsonify({"ok": False}), 404
        base_name = (row["name"] or ("Path " + str(roadmap_id))).strip()
        new_name = (base_name + " (copy)")[:80]
        conn.execute(
            "INSERT INTO user_roadmap (user_id, created_at, roadmap_json, domain, name, target_role, weekly_hours, goal_date, archived) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (user_id, datetime.utcnow().isoformat(), row["roadmap_json"], row["domain"], new_name, row["target_role"], row["weekly_hours"], row["goal_date"]),
        )
        conn.commit()
        new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return jsonify({"ok": True, "roadmap_id": new_id})


@app.route("/api/update_goal", methods=["POST"])
@require_login
def api_update_goal():
    data = request.get_json() or request.form
    roadmap_id = data.get("roadmap_id")
    goal_date = (data.get("goal_date") or "").strip()
    weekly_hours = data.get("weekly_hours")
    if not roadmap_id:
        return jsonify({"ok": False}), 400
    try:
        roadmap_id = int(roadmap_id)
    except (TypeError, ValueError):
        return jsonify({"ok": False}), 400
    if weekly_hours is None or str(weekly_hours).strip() == "":
        weekly_hours = 5
    try:
        weekly_hours = max(1, min(40, int(weekly_hours)))
    except (TypeError, ValueError):
        weekly_hours = 5
    if goal_date:
        try:
            datetime.fromisoformat(goal_date[:10])
            goal_date = goal_date[:10]
        except Exception:
            goal_date = ""
    user_id = session["user_id"]
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE user_roadmap SET goal_date = ?, weekly_hours = ? WHERE id = ? AND user_id = ?",
            (goal_date or None, weekly_hours, roadmap_id, user_id),
        )
        conn.commit()
    return jsonify({"ok": True, "goal_date": goal_date, "weekly_hours": weekly_hours})


@app.route("/api/share_create", methods=["POST"])
@require_login
def api_share_create():
    data = request.get_json() or request.form
    roadmap_id = data.get("roadmap_id")
    if not roadmap_id:
        return jsonify({"ok": False}), 400
    try:
        roadmap_id = int(roadmap_id)
    except (TypeError, ValueError):
        return jsonify({"ok": False}), 400
    user_id = session["user_id"]
    row = get_roadmap_by_id(roadmap_id, user_id)
    if not row:
        return jsonify({"ok": False}), 404
    token = get_or_create_share_token(user_id, roadmap_id)
    return jsonify({"ok": True, "url": url_for("share_view", token=token, _external=True)})


@app.route("/api/review_complete", methods=["POST"])
@require_login
def api_review_complete():
    data = request.get_json() or request.form
    review_id = data.get("review_id")
    if not review_id:
        return jsonify({"ok": False}), 400
    try:
        review_id = int(review_id)
    except (TypeError, ValueError):
        return jsonify({"ok": False}), 400
    user_id = session["user_id"]
    with get_db() as conn:
        cur = conn.execute(
            "UPDATE review_tasks SET completed = 1, completed_at = ? WHERE id = ? AND user_id = ?",
            (datetime.utcnow().isoformat(), review_id, user_id),
        )
        conn.commit()
    return jsonify({"ok": True})


@app.route("/login/google")
def login_google():
    """Redirect to Google OAuth consent screen."""
    if not GOOGLE_CLIENT_ID or not HAS_REQUESTS:
        return redirect(url_for("login"))
    import urllib.parse
    redirect_uri = url_for("auth_google_callback", _external=True)
    params = {"client_id": GOOGLE_CLIENT_ID, "redirect_uri": redirect_uri, "response_type": "code", "scope": "openid email profile"}
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    return redirect(url)


@app.route("/auth/google/callback")
def auth_google_callback():
    """Handle Google OAuth callback."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET or not HAS_REQUESTS:
        return redirect(url_for("login"))
    code = request.args.get("code")
    if not code:
        return redirect(url_for("login"))
    import urllib.parse
    redirect_uri = url_for("auth_google_callback", _external=True)
    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={"code": code, "client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET, "redirect_uri": redirect_uri, "grant_type": "authorization_code"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if resp.status_code != 200:
        return redirect(url_for("login"))
    token = resp.json().get("access_token")
    if not token:
        return redirect(url_for("login"))
    user_resp = requests.get("https://www.googleapis.com/oauth2/v2/userinfo", headers={"Authorization": "Bearer %s" % token})
    if user_resp.status_code != 200:
        return redirect(url_for("login"))
    info = user_resp.json()
    google_id, email = info.get("id"), (info.get("email") or "").strip()
    name = (info.get("name") or "").strip() or (email.split("@")[0] if email else "user")
    if not google_id or not email:
        return redirect(url_for("login"))
    with get_db() as conn:
        row = conn.execute("SELECT id, username FROM users WHERE google_id = ? OR email = ?", (google_id, email)).fetchone()
        if row:
            conn.execute("UPDATE users SET google_id = ?, email = ? WHERE id = ?", (google_id, email, row["id"]))
            conn.commit()
            session["user_id"], session["username"] = row["id"], row["username"]
        else:
            base = name[:30] or "user"
            username, n = base, 0
            while conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
                n += 1
                username = "%s%d" % (base[:25], n)
            conn.execute("INSERT INTO users (username, email, password, google_id) VALUES (?, ?, NULL, ?)", (username, email, google_id))
            conn.commit()
            r = conn.execute("SELECT id, username FROM users WHERE google_id = ?", (google_id,)).fetchone()
            session["user_id"], session["username"] = r["id"], r["username"]
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
@require_login
def dashboard():
    """Dashboard: list all learning paths (active/archived), analytics, charts."""
    user_id = session["user_id"]
    paths = get_paths(user_id, archived=False)
    archived_paths = get_paths(user_id, archived=True)
    roadmap_topics = get_user_roadmap_topics(user_id)
    completed_list = get_completed_topics(user_id)
    completed_topics_set = {c["topic"] for c in completed_list}
    total_topics = len(roadmap_topics)
    completed_count = sum(1 for t in roadmap_topics if t in completed_topics_set)
    progress_pct = round(100 * completed_count / total_topics, 1) if total_topics else 0
    streak = get_streak(user_id)
    weekly_data = get_completions_per_week(user_id)
    activity_heatmap = get_activity_heatmap(user_id, days=84)
    next_topics = get_next_best_topics(user_id, streak=streak)
    due_reviews = get_due_reviews(user_id)
    weekly_focus = get_weekly_focus(user_id)
    # Simple XP: 10 per completed topic + 1 per 5 minutes tracked
    total_minutes = 0
    for t in roadmap_topics:
        total_minutes += get_topic_total_minutes(user_id, None, t)
    xp = completed_count * 10 + max(0, total_minutes // 5)
    badges = []
    if streak >= 3:
        badges.append("3-day streak")
    if streak >= 7:
        badges.append("7-day streak")
    if streak >= 30:
        badges.append("30-day streak")
    return render_template(
        "dashboard.html",
        username=session.get("username", "User"),
        paths=paths,
        archived_paths=archived_paths,
        total_topics=total_topics,
        completed_topics=completed_count,
        progress_pct=progress_pct,
        streak=streak,
        weekly_data=weekly_data,
        activity_heatmap=activity_heatmap,
        next_topics=next_topics,
        due_reviews=due_reviews,
        weekly_focus=weekly_focus,
        xp=xp,
        badges=badges,
    )


@app.route("/generate", methods=["GET", "POST"])
@require_login
def generate():
    """Generate Learning Path — conversational AI interface (GET) or direct POST fallback."""
    if request.method == "GET":
        return render_template("generate.html")
    # Legacy POST fallback (kept for compatibility)
    domain = request.form.get("domain", "AI").strip()
    current_level = request.form.get("current_level", "Beginner").strip()
    target_role = (request.form.get("target_role") or "").strip() or "Learner"
    learning_style = (request.form.get("learning_style") or "balanced").strip().lower()
    path_name = (request.form.get("path_name") or "").strip() or ("%s – %s" % (domain, target_role))
    weekly_hours = request.form.get("weekly_study_hours", "5")
    try:
        weekly_study_hours = max(1, min(40, int(weekly_hours)))
    except ValueError:
        weekly_study_hours = 5
    goal_date = (request.form.get("goal_date") or "").strip()
    known_skills_raw = request.form.get("known_skills") or ""
    known_skills = [s.strip() for s in known_skills_raw.split(",") if s.strip()]
    try:
        roadmap = generate_roadmap(
            domain=domain, current_level=current_level,
            weekly_study_hours=weekly_study_hours, known_skills=known_skills,
            target_role=target_role, learning_style=learning_style, goal_date=goal_date,
        )
    except Exception as e:
        return render_template("generate.html", error=str(e))
    with get_db() as conn:
        conn.execute(
            "INSERT INTO user_roadmap (user_id, created_at, roadmap_json, domain, name, target_role, weekly_hours, goal_date, archived) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (session["user_id"], datetime.utcnow().isoformat(), json.dumps(roadmap), domain, path_name, target_role, weekly_study_hours, goal_date or None),
        )
        conn.commit()
        roadmap_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return redirect(url_for("roadmap", roadmap_id=roadmap_id))


@app.route("/api/generate_chat", methods=["POST"])
@require_login
def api_generate_chat():
    """
    Conversational roadmap generation.
    The AI collects: domain, level, target_role, known_skills, weekly_hours, learning_style.
    When all info is collected it generates and saves the roadmap, returning roadmap_id.
    """
    data = request.get_json() or {}
    message = (data.get("message") or "").strip()
    context = data.get("context") or {}

    from generator import _groq_chat, generate_roadmap as gen_roadmap

    SYSTEM = """You are an AI learning path assistant. Your job is to collect information from the user through friendly conversation to generate a personalized learning roadmap.

You need to collect ALL of these (ask one or two at a time, naturally):
1. domain (e.g. AI, Data Science, Web Development, Cloud Computing, Cybersecurity, etc.)
2. current_level (Beginner / Intermediate / Advanced)
3. target_role (specific job title they want, e.g. "MLOps Engineer", "Full-Stack Developer")
4. known_skills (comma-separated skills they already know, can be "none")
5. weekly_study_hours (number 1-40)
6. learning_style (practical / visual / theoretical / balanced)

Rules:
- Be conversational and encouraging
- Ask 1-2 questions at a time max
- Once you have ALL 6 pieces of info, respond with ONLY this exact JSON (no other text):
  READY:{"domain":"...","current_level":"...","target_role":"...","known_skills":"...","weekly_study_hours":10,"learning_style":"..."}
- Do not generate the roadmap yourself — just collect the info and output the READY: JSON
- If the user gives vague answers, ask for clarification
- Keep responses short and friendly"""

    history = context.get("history", [])
    messages = [{"role": "system", "content": SYSTEM}]
    messages.extend(history[-10:])
    messages.append({"role": "user", "content": message})

    try:
        reply = _groq_chat(messages, temperature=0.7)
    except Exception as e:
        return jsonify({"reply": f"Sorry, AI error: {e}", "context": context, "done": False}), 200

    new_history = history + [
        {"role": "user", "content": message},
        {"role": "assistant", "content": reply},
    ]
    context["history"] = new_history[-14:]

    # Check if AI has collected all info
    if reply.strip().startswith("READY:"):
        try:
            info_str = reply.strip()[6:].strip()
            info = json.loads(info_str)
            domain = info.get("domain", "AI")
            current_level = info.get("current_level", "Beginner")
            target_role = info.get("target_role", "Learner")
            known_skills_raw = info.get("known_skills", "")
            known_skills = [s.strip() for s in known_skills_raw.split(",") if s.strip() and s.strip().lower() != "none"]
            weekly_study_hours = int(info.get("weekly_study_hours", 8))
            learning_style = info.get("learning_style", "balanced")
            path_name = f"{target_role} Roadmap"

            roadmap = gen_roadmap(
                domain=domain, current_level=current_level,
                weekly_study_hours=weekly_study_hours, known_skills=known_skills,
                target_role=target_role, learning_style=learning_style,
            )

            with get_db() as conn:
                conn.execute(
                    "INSERT INTO user_roadmap (user_id, created_at, roadmap_json, domain, name, target_role, weekly_hours, goal_date, archived) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
                    (session["user_id"], datetime.utcnow().isoformat(), json.dumps(roadmap), domain, path_name, target_role, weekly_study_hours, None),
                )
                conn.commit()
                roadmap_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            return jsonify({
                "reply": f"Your personalized roadmap is ready! Generating {len(roadmap)} weeks for **{target_role}**...",
                "context": context,
                "done": True,
                "roadmap_id": roadmap_id,
            })
        except Exception as e:
            return jsonify({"reply": f"I had trouble generating the roadmap: {e}. Let's try again.", "context": context, "done": False})

    return jsonify({"reply": reply, "context": context, "done": False})


@app.route("/roadmap")
@app.route("/roadmap/<int:roadmap_id>")
@require_login
def roadmap(roadmap_id=None):
    """Show a saved roadmap by id, or the last one from session."""
    user_id = session["user_id"]
    if roadmap_id:
        row = get_roadmap_by_id(roadmap_id, user_id)
        if not row:
            return redirect(url_for("dashboard"))
        roadmap_data = json.loads(row["roadmap_json"]) if row["roadmap_json"] else []
        target_role = row["target_role"] or "Learner"
        path_name = row["name"]
        weekly_hours = row["weekly_hours"] if row["weekly_hours"] is not None else 5
        goal_date = (row["goal_date"] or "").strip()
        goal_status = compute_goal_status(row["created_at"], weekly_hours, goal_date, len(roadmap_data), len(get_completed_topics(user_id, roadmap_id)))
    else:
        roadmap_data = session.get("roadmap") or []
        target_role = session.get("target_role") or "Learner"
        path_name = None
        weekly_hours = 5
        goal_date = ""
        goal_status = {"expected_completed": 0, "behind_by": 0, "days_left": None}
        if not roadmap_data:
            paths = get_paths(user_id, archived=False)
            if paths:
                return redirect(url_for("roadmap", roadmap_id=paths[0]["id"]))
    completed_list = get_completed_topics(user_id, roadmap_id) if roadmap_id else get_completed_topics(user_id)
    completed_set = {c["topic"] for c in completed_list}
    resource_done = get_resource_progress(user_id, roadmap_id) if roadmap_id else set()
    milestone_notes = get_milestone_notes(user_id, roadmap_id) if roadmap_id else {}
    wf_set = get_weekly_focus_for(user_id, roadmap_id) if roadmap_id else set()
    return render_template(
        "roadmap.html",
        roadmap=roadmap_data,
        roadmap_id=roadmap_id,
        path_name=path_name,
        target_role=target_role,
        completed_set=completed_set,
        resource_done=resource_done,
        milestone_notes=milestone_notes,
        weekly_hours=weekly_hours,
        goal_date=goal_date,
        goal_status=goal_status,
        weekly_focus_set=wf_set,
    )


@app.route("/compare")
@require_login
def compare():
    user_id = session["user_id"]
    paths = get_paths(user_id, archived=False)
    a = request.args.get("a")
    b = request.args.get("b")
    if not a or not b:
        return render_template("compare.html", paths=paths, a=None, b=None, result=None)
    try:
        a_id = int(a)
        b_id = int(b)
    except (TypeError, ValueError):
        return render_template("compare.html", paths=paths, a=None, b=None, result=None)
    if a_id == b_id:
        return render_template("compare.html", paths=paths, a=a_id, b=b_id, result=None)
    a_row = get_roadmap_by_id(a_id, user_id)
    b_row = get_roadmap_by_id(b_id, user_id)
    if not a_row or not b_row:
        return render_template("compare.html", paths=paths, a=a_id, b=b_id, result=None)
    try:
        a_rm = json.loads(a_row["roadmap_json"]) if a_row["roadmap_json"] else []
    except (json.JSONDecodeError, TypeError):
        a_rm = []
    try:
        b_rm = json.loads(b_row["roadmap_json"]) if b_row["roadmap_json"] else []
    except (json.JSONDecodeError, TypeError):
        b_rm = []
    a_topics = [w.get("topic") for w in a_rm if (w.get("topic") or "").strip()]
    b_topics = [w.get("topic") for w in b_rm if (w.get("topic") or "").strip()]
    a_set = set(a_topics)
    b_set = set(b_topics)
    only_a = sorted(list(a_set - b_set))
    only_b = sorted(list(b_set - a_set))
    common = sorted(list(a_set & b_set))
    a_completed = {c["topic"] for c in get_completed_topics(user_id, a_id)}
    b_completed = {c["topic"] for c in get_completed_topics(user_id, b_id)}
    result = {
        "a": {"id": a_id, "name": a_row["name"] or ("Path " + str(a_id)), "total": len(a_set), "completed": len(a_set & a_completed),
              "progress_pct": round(100 * len(a_set & a_completed) / len(a_set), 1) if a_set else 0},
        "b": {"id": b_id, "name": b_row["name"] or ("Path " + str(b_id)), "total": len(b_set), "completed": len(b_set & b_completed),
              "progress_pct": round(100 * len(b_set & b_completed) / len(b_set), 1) if b_set else 0},
        "only_a": only_a,
        "only_b": only_b,
        "common": common,
    }
    return render_template("compare.html", paths=paths, a=a_id, b=b_id, result=result)


@app.route("/export/roadmap/<int:roadmap_id>.csv")
@require_login
def export_roadmap_csv(roadmap_id: int):
    user_id = session["user_id"]
    row = get_roadmap_by_id(roadmap_id, user_id)
    if not row:
        abort(404)
    try:
        roadmap = json.loads(row["roadmap_json"]) if row["roadmap_json"] else []
    except (json.JSONDecodeError, TypeError):
        roadmap = []
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["week", "topic", "tasks", "resources"])
    for item in roadmap:
        week = item.get("week") or ""
        topic = (item.get("topic") or "").strip()
        tasks = item.get("tasks") or []
        resources = item.get("resources") or []
        if isinstance(tasks, list):
            tasks = " | ".join([str(t).strip() for t in tasks if str(t).strip()])
        if isinstance(resources, list):
            resources = " | ".join([str(u).strip() for u in resources if str(u).strip()])
        w.writerow([week, topic, tasks, resources])
    filename = ((row["name"] or ("roadmap-" + str(roadmap_id)))[:60].replace(" ", "_") + ".csv")
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/print/roadmap/<int:roadmap_id>")
@require_login
def print_roadmap(roadmap_id: int):
    user_id = session["user_id"]
    row = get_roadmap_by_id(roadmap_id, user_id)
    if not row:
        abort(404)
    try:
        roadmap = json.loads(row["roadmap_json"]) if row["roadmap_json"] else []
    except (json.JSONDecodeError, TypeError):
        roadmap = []
    return render_template("print_roadmap.html", roadmap=roadmap, path_name=row["name"], target_role=row["target_role"],
                           now_str=datetime.utcnow().strftime("%B %d, %Y"),
                           now_full=datetime.utcnow().strftime("%B %d, %Y %I:%M %p UTC"))


@app.route("/share/<token>")
def share_view(token: str):
    token = (token or "").strip()
    if not token:
        abort(404)
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT st.roadmap_id, ur.name, ur.target_role, ur.roadmap_json
            FROM share_tokens st
            JOIN user_roadmap ur ON ur.id = st.roadmap_id
            WHERE st.token = ? AND st.revoked = 0
            """,
            (token,),
        ).fetchone()
    if not row:
        abort(404)
    try:
        roadmap = json.loads(row["roadmap_json"]) if row["roadmap_json"] else []
    except (json.JSONDecodeError, TypeError):
        roadmap = []
    return render_template("share.html", roadmap=roadmap, path_name=row["name"], target_role=row["target_role"])


@app.route("/reviews")
@require_login
def reviews_page():
    user_id = session["user_id"]
    due = get_due_reviews(user_id, limit=100)
    with get_db() as conn:
        upcoming = conn.execute(
            """
            SELECT rt.id, rt.roadmap_id, rt.topic, rt.due_date, ur.name
            FROM review_tasks rt
            JOIN user_roadmap ur ON ur.id = rt.roadmap_id
            WHERE rt.user_id = ? AND rt.completed = 0 AND rt.due_date > date('now') AND ur.archived = 0
            ORDER BY rt.due_date ASC
            LIMIT 100
            """,
            (user_id,),
        ).fetchall()
    upcoming_list = [{"id": r["id"], "roadmap_id": r["roadmap_id"], "topic": r["topic"], "due_date": r["due_date"], "path_name": (r["name"] or ("Path " + str(r["roadmap_id"])))} for r in upcoming]
    return render_template("reviews.html", due_reviews=due, upcoming_reviews=upcoming_list)


@app.route("/export/roadmap/<int:roadmap_id>.ics")
@require_login
def export_roadmap_ics(roadmap_id: int):
    user_id = session["user_id"]
    row = get_roadmap_by_id(roadmap_id, user_id)
    if not row:
        abort(404)
    try:
        roadmap = json.loads(row["roadmap_json"]) if row["roadmap_json"] else []
    except (json.JSONDecodeError, TypeError):
        roadmap = []
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//AI Learning Platform//Roadmap//EN"]
    base_date = datetime.utcnow().date()
    for i, w in enumerate(roadmap, start=0):
        topic = (w.get("topic") or "").strip()
        date = (base_date + timedelta(days=7*i)).strftime("%Y%m%d")
        uid = f"roadmap-{roadmap_id}-{i}@local"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTART;VALUE=DATE:{date}",
            f"DTEND;VALUE=DATE:{date}",
            f"SUMMARY:Roadmap - {topic}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return Response("\r\n".join(lines), mimetype="text/calendar", headers={"Content-Disposition": f"attachment; filename=roadmap-{roadmap_id}.ics"})


@app.route("/export/reviews.ics")
@require_login
def export_reviews_ics():
    user_id = session["user_id"]
    with get_db() as conn:
        rows = conn.execute(
            "SELECT topic, due_date FROM review_tasks WHERE user_id = ? AND completed = 0 ORDER BY due_date ASC LIMIT 500",
            (user_id,),
        ).fetchall()
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//AI Learning Platform//Reviews//EN"]
    for idx, r in enumerate(rows):
        topic = (r["topic"] or "").strip()
        date_str = str(r["due_date"])[:10]
        try:
            dt = datetime.fromisoformat(date_str).date().strftime("%Y%m%d")
        except Exception:
            continue
        uid = f"review-{idx}-{topic.replace(' ','_')}@local"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTART;VALUE=DATE:{dt}",
            f"DTEND;VALUE=DATE:{dt}",
            f"SUMMARY:Review - {topic}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return Response("\r\n".join(lines), mimetype="text/calendar", headers={"Content-Disposition": "attachment; filename=reviews.ics"})

@app.route("/export/reviews.csv")
@require_login
def export_reviews_csv():
    user_id = session["user_id"]
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT rt.roadmap_id, rt.topic, rt.due_date, rt.completed, ur.name
            FROM review_tasks rt
            JOIN user_roadmap ur ON ur.id = rt.roadmap_id
            WHERE rt.user_id = ?
            ORDER BY rt.due_date ASC
            """,
            (user_id,),
        ).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["path_name", "roadmap_id", "topic", "due_date", "completed"])
    for r in rows:
        w.writerow([(r["name"] or ("Path " + str(r["roadmap_id"]))), r["roadmap_id"], r["topic"], r["due_date"], r["completed"]])
    return Response(buf.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=reviews.csv"})

@app.route("/print/reviews")
@require_login
def print_reviews():
    user_id = session["user_id"]
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT rt.roadmap_id, rt.topic, rt.due_date, rt.completed, ur.name
            FROM review_tasks rt
            JOIN user_roadmap ur ON ur.id = rt.roadmap_id
            WHERE rt.user_id = ?
            ORDER BY rt.due_date ASC
            """,
            (user_id,),
        ).fetchall()
    items = [{"path_name": (r["name"] or ("Path " + str(r["roadmap_id"]))), "roadmap_id": r["roadmap_id"], "topic": r["topic"], "due_date": r["due_date"], "completed": r["completed"]} for r in rows]
    return render_template("print_reviews.html", items=items,
                           now_str=datetime.utcnow().strftime("%B %d, %Y • %I:%M %p UTC"),
                           now_full=datetime.utcnow().strftime("%B %d, %Y %I:%M %p UTC"))

@app.route("/recap/weekly")
@require_login
def recap_weekly():
    user_id = session["user_id"]
    since = (datetime.utcnow().date() - timedelta(days=7)).isoformat()
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT p.topic, p.date_completed, p.notes, ur.name, p.roadmap_id
            FROM progress p
            LEFT JOIN user_roadmap ur ON ur.id = p.roadmap_id
            WHERE p.user_id = ? AND p.status = 'completed' AND p.date_completed >= ?
            ORDER BY p.date_completed DESC
            """,
            (user_id, since),
        ).fetchall()
    items = []
    total_minutes = 0
    for r in rows:
        m = get_topic_total_minutes(user_id, r["roadmap_id"], r["topic"])
        total_minutes += m
        items.append({"topic": r["topic"], "date": r["date_completed"], "notes": r["notes"] or "", "path_name": r["name"] or ("Path " + str(r["roadmap_id"] or "")), "minutes": m})
    return render_template("weekly_recap.html", items=items, total_minutes=total_minutes)

@app.route("/recap")
@require_login
def recap_root():
    return redirect("/recap/weekly")

@app.route("/api/review_add", methods=["POST"])
@require_login
def api_review_add():
    data = request.get_json() or request.form
    roadmap_id = data.get("roadmap_id")
    topic = (data.get("topic") or "").strip()
    days = data.get("days", 7)
    if not roadmap_id or not topic:
        return jsonify({"ok": False}), 400
    try:
        rid = int(roadmap_id)
        days = int(days)
    except (TypeError, ValueError):
        return jsonify({"ok": False}), 400
    due = (datetime.utcnow().date() + timedelta(days=days)).isoformat()
    user_id = session["user_id"]
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO review_tasks (user_id, roadmap_id, topic, due_date, completed) VALUES (?, ?, ?, ?, 0)",
            (user_id, rid, topic, due),
        )
        conn.commit()
    return jsonify({"ok": True, "due_date": due})

@app.route("/api/reorder_week", methods=["POST"])
@require_login
def api_reorder_week():
    data = request.get_json() or request.form
    roadmap_id = data.get("roadmap_id")
    from_index = data.get("from_index")
    to_index = data.get("to_index")
    try:
        rid = int(roadmap_id)
        fi = int(from_index)
        ti = int(to_index)
    except (TypeError, ValueError):
        return jsonify({"ok": False}), 400
    row = get_roadmap_by_id(rid, session["user_id"])
    if not row:
        return jsonify({"ok": False}), 404
    try:
        rm = json.loads(row["roadmap_json"]) if row["roadmap_json"] else []
    except (json.JSONDecodeError, TypeError):
        rm = []
    if not (0 <= fi < len(rm)) or not (0 <= ti < len(rm)):
        return jsonify({"ok": False}), 400
    item = rm.pop(fi)
    rm.insert(ti, item)
    for idx, w in enumerate(rm, start=1):
        w["week"] = idx
    with get_db() as conn:
        conn.execute("UPDATE user_roadmap SET roadmap_json = ? WHERE id = ? AND user_id = ?", (json.dumps(rm), rid, session["user_id"]))
        conn.commit()
    return jsonify({"ok": True})

@app.route("/api/weekly_focus_toggle", methods=["POST"])
@require_login
def api_weekly_focus_toggle():
    data = request.get_json() or request.form
    roadmap_id = data.get("roadmap_id")
    topic = (data.get("topic") or "").strip()
    in_focus = data.get("in_focus")
    if not roadmap_id or not topic:
        return jsonify({"ok": False}), 400
    try:
        roadmap_id = int(roadmap_id)
    except (TypeError, ValueError):
        return jsonify({"ok": False}), 400
    ws = _week_start()
    user_id = session["user_id"]
    with get_db() as conn:
        if in_focus in (True, "true", "1", 1):
            conn.execute(
                "INSERT OR IGNORE INTO weekly_focus (user_id, roadmap_id, topic, week_start) VALUES (?, ?, ?, ?)",
                (user_id, roadmap_id, topic, ws),
            )
        else:
            conn.execute(
                "DELETE FROM weekly_focus WHERE user_id = ? AND roadmap_id = ? AND topic = ? AND week_start = ?",
                (user_id, roadmap_id, topic, ws),
            )
        conn.commit()
    return jsonify({"ok": True})


@app.route("/api/timer_start", methods=["POST"])
@require_login
def api_timer_start():
    data = request.get_json() or request.form
    roadmap_id = data.get("roadmap_id")
    topic = (data.get("topic") or "").strip()
    try:
        roadmap_id = int(roadmap_id) if roadmap_id else None
    except (TypeError, ValueError):
        roadmap_id = None
    if not topic:
        return jsonify({"ok": False}), 400
    user_id = session["user_id"]
    with get_db() as conn:
        conn.execute(
            "INSERT INTO topic_sessions (user_id, roadmap_id, topic, start_ts, end_ts) VALUES (?, ?, ?, ?, NULL)",
            (user_id, roadmap_id, topic, datetime.utcnow().isoformat()),
        )
        conn.commit()
        sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return jsonify({"ok": True, "session_id": sid})


@app.route("/api/timer_stop", methods=["POST"])
@require_login
def api_timer_stop():
    data = request.get_json() or request.form
    topic = (data.get("topic") or "").strip()
    user_id = session["user_id"]
    if not topic:
        return jsonify({"ok": False}), 400
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, start_ts FROM topic_sessions WHERE user_id = ? AND topic = ? AND end_ts IS NULL ORDER BY id DESC LIMIT 1",
            (user_id, topic),
        ).fetchone()
        if not row:
            return jsonify({"ok": False}), 404
        end_ts = datetime.utcnow().isoformat()
        conn.execute("UPDATE topic_sessions SET end_ts = ? WHERE id = ?", (end_ts, row["id"]))
        conn.commit()
    return jsonify({"ok": True})


@app.route("/api/comment_add", methods=["POST"])
@require_login
def api_comment_add():
    data = request.get_json() or request.form
    roadmap_id = data.get("roadmap_id")
    topic = (data.get("topic") or "").strip()
    content = (data.get("content") or "").strip()
    if not roadmap_id or not topic or not content:
        return jsonify({"ok": False}), 400
    try:
        roadmap_id = int(roadmap_id)
    except (TypeError, ValueError):
        return jsonify({"ok": False}), 400
    user_id = session["user_id"]
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO comments (user_id, roadmap_id, topic, content, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, roadmap_id, topic, content, now),
        )
        conn.commit()
        cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return jsonify({"ok": True, "comment": {"id": cid, "user_id": user_id, "content": content, "created_at": now}})


@app.route("/api/comment_delete", methods=["POST"])
@require_login
def api_comment_delete():
    data = request.get_json() or request.form
    comment_id = data.get("comment_id")
    try:
        cid = int(comment_id)
    except (TypeError, ValueError):
        return jsonify({"ok": False}), 400
    user_id = session["user_id"]
    with get_db() as conn:
        conn.execute("DELETE FROM comments WHERE id = ? AND user_id = ?", (cid, user_id))
        conn.commit()
    return jsonify({"ok": True})


@app.route("/api/import_share", methods=["POST"])
@require_login
def api_import_share():
    data = request.get_json() or request.form
    token = (data.get("token") or "").strip()
    if not token:
        return jsonify({"ok": False}), 400
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT st.roadmap_id, ur.name, ur.domain, ur.target_role, ur.weekly_hours, ur.goal_date, ur.roadmap_json
            FROM share_tokens st
            JOIN user_roadmap ur ON ur.id = st.roadmap_id
            WHERE st.token = ? AND st.revoked = 0
            """,
            (token,),
        ).fetchone()
        if not row:
            return jsonify({"ok": False}), 404
        conn.execute(
            "INSERT INTO user_roadmap (user_id, created_at, roadmap_json, domain, name, target_role, weekly_hours, goal_date, archived) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (session["user_id"], datetime.utcnow().isoformat(), row["roadmap_json"], row["domain"], (row["name"] or "") + " (import)", row["target_role"], row["weekly_hours"], row["goal_date"]),
        )
        conn.commit()
        new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return jsonify({"ok": True, "roadmap_id": new_id})


@app.route("/api/share/<token>.json")
def share_json(token: str):
    token = (token or "").strip()
    if not token:
        abort(404)
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT st.roadmap_id, ur.name, ur.target_role, ur.roadmap_json
            FROM share_tokens st
            JOIN user_roadmap ur ON ur.id = st.roadmap_id
            WHERE st.token = ? AND st.revoked = 0
            """,
            (token,),
        ).fetchone()
    if not row:
        abort(404)
    return jsonify({"name": row["name"], "target_role": row["target_role"], "roadmap": json.loads(row["roadmap_json"] or "[]")})

@app.route("/api/topic_tags_get")
@require_login
def api_topic_tags_get():
    roadmap_id = request.args.get("roadmap_id")
    topic = (request.args.get("topic") or "").strip()
    try:
        rid = int(roadmap_id)
    except (TypeError, ValueError):
        return jsonify({"tags": []})
    with get_db() as conn:
        rows = conn.execute("SELECT tag FROM topic_tags WHERE user_id = ? AND roadmap_id = ? AND topic = ? ORDER BY tag ASC", (session["user_id"], rid, topic)).fetchall()
    return jsonify({"tags": [r["tag"] for r in rows]})

@app.route("/api/topic_tag_add", methods=["POST"])
@require_login
def api_topic_tag_add():
    data = request.get_json() or request.form
    roadmap_id = data.get("roadmap_id")
    topic = (data.get("topic") or "").strip()
    tag = (data.get("tag") or "").strip()
    try:
        rid = int(roadmap_id)
    except (TypeError, ValueError):
        return jsonify({"ok": False}), 400
    if not tag:
        return jsonify({"ok": False}), 400
    with get_db() as conn:
        conn.execute("INSERT OR IGNORE INTO topic_tags (user_id, roadmap_id, topic, tag) VALUES (?, ?, ?, ?)", (session["user_id"], rid, topic, tag))
        conn.commit()
    return jsonify({"ok": True})

@app.route("/api/topic_tag_remove", methods=["POST"])
@require_login
def api_topic_tag_remove():
    data = request.get_json() or request.form
    roadmap_id = data.get("roadmap_id")
    topic = (data.get("topic") or "").strip()
    tag = (data.get("tag") or "").strip()
    try:
        rid = int(roadmap_id)
    except (TypeError, ValueError):
        return jsonify({"ok": False}), 400
    with get_db() as conn:
        conn.execute("DELETE FROM topic_tags WHERE user_id = ? AND roadmap_id = ? AND topic = ? AND tag = ?", (session["user_id"], rid, topic, tag))
        conn.commit()
    return jsonify({"ok": True})

@app.route("/api/resource_meta_get")
@require_login
def api_resource_meta_get():
    roadmap_id = request.args.get("roadmap_id")
    topic = (request.args.get("topic") or "").strip()
    try:
        rid = int(roadmap_id)
    except (TypeError, ValueError):
        return jsonify({"items": []})
    with get_db() as conn:
        rows = conn.execute("SELECT resource_url, duration_min, difficulty FROM resource_meta WHERE user_id = ? AND roadmap_id = ? AND topic = ?", (session["user_id"], rid, topic)).fetchall()
    return jsonify({"items": [{"resource_url": r["resource_url"], "duration_min": r["duration_min"], "difficulty": r["difficulty"]} for r in rows]})

@app.route("/api/resource_meta_update", methods=["POST"])
@require_login
def api_resource_meta_update():
    data = request.get_json() or request.form
    roadmap_id = data.get("roadmap_id")
    topic = (data.get("topic") or "").strip()
    url = (data.get("resource_url") or "").strip()
    duration = data.get("duration_min")
    difficulty = (data.get("difficulty") or "").strip() or None
    try:
        rid = int(roadmap_id)
        dur = int(duration) if duration not in (None, "",) else None
    except (TypeError, ValueError):
        return jsonify({"ok": False}), 400
    with get_db() as conn:
        conn.execute(
            "INSERT INTO resource_meta (user_id, roadmap_id, topic, resource_url, duration_min, difficulty) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(user_id, roadmap_id, topic, resource_url) DO UPDATE SET duration_min=excluded.duration_min, difficulty=excluded.difficulty",
            (session["user_id"], rid, topic, url, dur, difficulty),
        )
        conn.commit()
    return jsonify({"ok": True})

def _load_resources_map():
    try:
        with (APP_DIR / "resources.json").open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

@app.route("/api/resource_alternates")
@require_login
def api_resource_alternates():
    topic = (request.args.get("topic") or "").strip()
    current = (request.args.get("current") or "").strip()
    res_map = _load_resources_map()
    options = [u for u in res_map.get(topic, []) if u != current][:5]
    return jsonify({"alternates": options})

@app.route("/api/topic_time")
@require_login
def api_topic_time():
    roadmap_id = request.args.get("roadmap_id")
    topic = (request.args.get("topic") or "").strip()
    try:
        rid = int(roadmap_id) if roadmap_id else None
    except (TypeError, ValueError):
        rid = None
    if not topic:
        return jsonify({"minutes": 0})
    minutes = get_topic_total_minutes(session["user_id"], rid, topic)
    return jsonify({"minutes": minutes})


@app.route("/api/comments")
@require_login
def api_comments():
    roadmap_id = request.args.get("roadmap_id")
    topic = (request.args.get("topic") or "").strip()
    try:
        rid = int(roadmap_id)
    except (TypeError, ValueError):
        return jsonify({"comments": []})
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, user_id, content, created_at FROM comments WHERE roadmap_id = ? AND topic = ? ORDER BY id ASC",
            (rid, topic),
        ).fetchall()
    return jsonify({"comments": [{"id": r["id"], "user_id": r["user_id"], "content": r["content"], "created_at": r["created_at"]} for r in rows]})


@app.route("/mark_completed", methods=["POST"])
@require_login
def mark_completed():
    """Record a topic as completed for the current user (and optional notes), redirect back to roadmap."""
    topic = (request.form.get("topic") or "").strip()
    roadmap_id = request.form.get("roadmap_id")
    notes = (request.form.get("notes") or "").strip()
    if not topic:
        return redirect(url_for("roadmap", roadmap_id=roadmap_id) if roadmap_id else url_for("roadmap"))
    today = datetime.utcnow().date().isoformat()
    user_id = session["user_id"]
    try:
        rid = int(roadmap_id) if roadmap_id else None
    except (TypeError, ValueError):
        rid = None
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM progress WHERE user_id = ? AND roadmap_id = ? AND topic = ?",
            (user_id, rid, topic),
        ).fetchone() if rid else None
        if existing:
            conn.execute(
                "UPDATE progress SET status = 'completed', date_completed = ?, notes = ? WHERE id = ?",
                (today, notes, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO progress (user_id, roadmap_id, topic, status, date_completed, notes) VALUES (?, ?, ?, 'completed', ?, ?)",
                (user_id, rid, topic, today, notes),
            )
        if rid:
            for d in (1, 7, 30):
                due = (datetime.utcnow().date() + timedelta(days=d)).isoformat()
                conn.execute(
                    "INSERT OR IGNORE INTO review_tasks (user_id, roadmap_id, topic, due_date, completed) VALUES (?, ?, ?, ?, 0)",
                    (user_id, rid, topic, due),
                )
        conn.commit()
    return redirect(url_for("roadmap", roadmap_id=roadmap_id) if roadmap_id else url_for("roadmap"))


# -----------------------------------------------------------------------------
# Profile helpers
# -----------------------------------------------------------------------------

def get_activity_heatmap(user_id: int, days: int = 365) -> dict:
    """Return dict of date_str -> count for the last `days` days."""
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT date(date_completed) as d, COUNT(*) as cnt
            FROM progress
            WHERE user_id = ? AND status = 'completed'
              AND date_completed >= date('now', ?)
            GROUP BY date(date_completed)
            """,
            (user_id, f"-{days} days"),
        ).fetchall()
    return {r["d"]: r["cnt"] for r in rows}


def get_domain_breakdown(user_id: int) -> list:
    """Return list of {domain, completed, total} for radar chart."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, domain, roadmap_json FROM user_roadmap WHERE user_id = ? AND archived = 0",
            (user_id,),
        ).fetchall()
    result = []
    with get_db() as conn:
        for r in rows:
            try:
                topics = [w.get("topic") for w in json.loads(r["roadmap_json"]) if w.get("topic")]
            except Exception:
                topics = []
            completed = conn.execute(
                "SELECT COUNT(DISTINCT topic) FROM progress WHERE user_id=? AND roadmap_id=? AND status='completed'",
                (user_id, r["id"]),
            ).fetchone()[0]
            result.append({"domain": r["domain"] or "General", "completed": completed, "total": len(topics)})
    return result


# -----------------------------------------------------------------------------
# Profile route
# -----------------------------------------------------------------------------

@app.route("/profile")
@require_login
def profile():
    user_id = session["user_id"]
    paths = get_paths(user_id, archived=False)
    archived_paths = get_paths(user_id, archived=True)
    completed_list = get_completed_topics(user_id)
    roadmap_topics = get_user_roadmap_topics(user_id)
    completed_topics_set = {c["topic"] for c in completed_list}
    total_topics = len(roadmap_topics)
    completed_count = sum(1 for t in roadmap_topics if t in completed_topics_set)
    progress_pct = round(100 * completed_count / total_topics, 1) if total_topics else 0
    streak = get_streak(user_id)
    weekly_data = get_completions_per_week(user_id, weeks=12)
    activity_heatmap = get_activity_heatmap(user_id, days=365)
    domain_breakdown = get_domain_breakdown(user_id)
    total_minutes = 0
    for t in roadmap_topics:
        total_minutes += get_topic_total_minutes(user_id, None, t)
    xp = completed_count * 10 + max(0, total_minutes // 5)
    badges = []
    if streak >= 3: badges.append({"label": "3-Day Streak", "icon": "fire", "color": "warning"})
    if streak >= 7: badges.append({"label": "Week Warrior", "icon": "bolt", "color": "info"})
    if streak >= 30: badges.append({"label": "30-Day Legend", "icon": "crown", "color": "warning"})
    if completed_count >= 10: badges.append({"label": "10 Topics Done", "icon": "check-double", "color": "success"})
    if completed_count >= 50: badges.append({"label": "50 Topics Done", "icon": "trophy", "color": "warning"})
    if len(paths) >= 3: badges.append({"label": "Multi-Learner", "icon": "layer-group", "color": "primary"})
    return render_template(
        "profile.html",
        username=session.get("username", "User"),
        paths=paths,
        archived_paths=archived_paths,
        total_topics=total_topics,
        completed_topics=completed_count,
        progress_pct=progress_pct,
        streak=streak,
        weekly_data=weekly_data,
        activity_heatmap=activity_heatmap,
        domain_breakdown=domain_breakdown,
        xp=xp,
        badges=badges,
        total_minutes=total_minutes,
    )


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="0.0.0.0", port=5000, use_reloader=False)
