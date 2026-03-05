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
from datetime import datetime, timedelta
from pathlib import Path

from flask import (
    Flask,
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
    """Create and migrate tables: users (incl. google_id), progress (roadmap_id, notes), user_roadmap (name, target_role, archived), resource_progress."""
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
        """)
        conn.commit()
        for table, col, sql in [
            ("users", "google_id", "ALTER TABLE users ADD COLUMN google_id TEXT"),
            ("user_roadmap", "name", "ALTER TABLE user_roadmap ADD COLUMN name TEXT"),
            ("user_roadmap", "target_role", "ALTER TABLE user_roadmap ADD COLUMN target_role TEXT"),
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
            "SELECT id, name, domain, target_role, created_at, roadmap_json, archived FROM user_roadmap WHERE user_id = ? AND archived = ? ORDER BY id DESC",
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
            out.append({
                "id": r["id"],
                "name": r["name"] or ("Path " + str(r["id"])),
                "domain": r["domain"] or "",
                "target_role": r["target_role"] or "Learner",
                "created_at": r["created_at"],
                "progress_pct": pct,
                "total_topics": total,
                "completed_topics": completed,
            })
        return out


def get_roadmap_by_id(roadmap_id: int, user_id: int):
    """Return roadmap row (id, name, domain, target_role, roadmap_json) or None."""
    with get_db() as conn:
        return conn.execute(
            "SELECT id, name, domain, target_role, roadmap_json FROM user_roadmap WHERE id = ? AND user_id = ?",
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
    )


@app.route("/generate", methods=["GET", "POST"])
@require_login
def generate():
    """Generate Learning Path form; on POST save roadmap with name and redirect to roadmap/<id>."""
    if request.method == "GET":
        domains = get_available_domains()
        roles = []
        try:
            with open(APP_DIR / "data" / "emerging_roles.json", "r", encoding="utf-8") as f:
                roles = json.load(f)
        except Exception:
            pass
        return render_template("generate.html", emerging_roles=roles, domains=domains)
    domain = request.form.get("domain", "AI").strip()
    current_level = request.form.get("current_level", "Beginner").strip()
    target_role = (request.form.get("target_role") or "").strip() or "Learner"
    path_name = (request.form.get("path_name") or "").strip() or ("%s – %s" % (domain, target_role))
    weekly_hours = request.form.get("weekly_study_hours", "5")
    try:
        weekly_study_hours = max(1, min(20, int(weekly_hours)))
    except ValueError:
        weekly_study_hours = 5
    known_skills_raw = request.form.get("known_skills") or ""
    known_skills = [s.strip() for s in known_skills_raw.split(",") if s.strip()]
    try:
        roadmap = generate_roadmap(
            domain=domain,
            current_level=current_level,
            weekly_study_hours=weekly_study_hours,
            known_skills=known_skills,
            knowledge_base_path=str(APP_DIR / "knowledge_base.json"),
            resources_path=str(APP_DIR / "resources.json"),
        )
    except (ValueError, FileNotFoundError) as e:
        roles = []
        try:
            with open(APP_DIR / "data" / "emerging_roles.json", "r", encoding="utf-8") as f:
                roles = json.load(f)
        except Exception:
            pass
        domains = get_available_domains()
        return render_template("generate.html", error=str(e), emerging_roles=roles, domains=domains)
    with get_db() as conn:
        conn.execute(
            "INSERT INTO user_roadmap (user_id, created_at, roadmap_json, domain, name, target_role, archived) VALUES (?, ?, ?, ?, ?, ?, 0)",
            (session["user_id"], datetime.utcnow().isoformat(), json.dumps(roadmap), domain, path_name, target_role),
        )
        conn.commit()
        roadmap_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    session["roadmap"] = roadmap
    session["target_role"] = target_role
    return redirect(url_for("roadmap", roadmap_id=roadmap_id))


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
    else:
        roadmap_data = session.get("roadmap") or []
        target_role = session.get("target_role") or "Learner"
        path_name = None
        if not roadmap_data:
            paths = get_paths(user_id, archived=False)
            if paths:
                return redirect(url_for("roadmap", roadmap_id=paths[0]["id"]))
    completed_list = get_completed_topics(user_id, roadmap_id) if roadmap_id else get_completed_topics(user_id)
    completed_set = {c["topic"] for c in completed_list}
    resource_done = get_resource_progress(user_id, roadmap_id) if roadmap_id else set()
    milestone_notes = get_milestone_notes(user_id, roadmap_id) if roadmap_id else {}
    return render_template(
        "roadmap.html",
        roadmap=roadmap_data,
        roadmap_id=roadmap_id,
        path_name=path_name,
        target_role=target_role,
        completed_set=completed_set,
        resource_done=resource_done,
        milestone_notes=milestone_notes,
    )


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
        conn.commit()
    return redirect(url_for("roadmap", roadmap_id=roadmap_id) if roadmap_id else url_for("roadmap"))


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
