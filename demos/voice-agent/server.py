"""
Green Shield Pest Control — Voice Agent Webhook Server
Handles Vapi tool calls, stores leads, serves dashboard.
"""

import os
import sys
import json
import sqlite3
import secrets
from datetime import datetime, timedelta
from functools import wraps

import resend
from dotenv import load_dotenv
from flask import Flask, request, jsonify, render_template_string, g, redirect, url_for, flash
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from authlib.integrations.flask_client import OAuth

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

oauth = OAuth(app)
google = oauth.register(
    name="google",
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

resend.api_key = os.environ.get("RESEND_API_KEY", "")

DB_PATH = "leads.db"


# ─── Database ──────────────────────────────────────────────────────────────

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# ─── User Model ───────────────────────────────────────────────────────────

class User(UserMixin):
    def __init__(self, id, email, name, password_hash, google_id, role, created_at):
        self.id = id
        self.email = email
        self.name = name
        self.password_hash = password_hash
        self.google_id = google_id
        self.role = role
        self.created_at = created_at

    @staticmethod
    def from_row(row):
        if row is None:
            return None
        return User(
            id=row["id"], email=row["email"], name=row["name"],
            password_hash=row["password_hash"], google_id=row["google_id"],
            role=row["role"], created_at=row["created_at"],
        )

    @staticmethod
    def get_by_id(user_id):
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        db.close()
        return User.from_row(row)

    @staticmethod
    def get_by_email(email):
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        db.close()
        return User.from_row(row)

    @staticmethod
    def get_by_google_id(google_id):
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM users WHERE google_id = ?", (google_id,)).fetchone()
        db.close()
        return User.from_row(row)

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def can_change_status(self):
        return self.role in ("admin", "attorney")


@login_manager.user_loader
def load_user(user_id):
    return User.get_by_id(int(user_id))


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated(*args, **kwargs):
            if current_user.role not in roles:
                return "Forbidden", 403
            return f(*args, **kwargs)
        return decorated
    return decorator


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            caller_name TEXT NOT NULL,
            case_type TEXT,
            case_summary TEXT,
            score INTEGER NOT NULL,
            routing TEXT NOT NULL,
            email TEXT,
            zip_code TEXT,
            phone TEXT,
            call_id TEXT,
            status TEXT NOT NULL DEFAULT 'new',
            call_duration_seconds INTEGER,
            created_at TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            password_hash TEXT,
            google_id TEXT,
            role TEXT NOT NULL DEFAULT 'staff',
            created_at TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS lead_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL REFERENCES leads(id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    try:
        db.execute("ALTER TABLE leads ADD COLUMN status TEXT NOT NULL DEFAULT 'new'")
    except sqlite3.OperationalError:
        pass
    try:
        db.execute("ALTER TABLE leads ADD COLUMN call_duration_seconds INTEGER")
    except sqlite3.OperationalError:
        pass
    db.commit()
    db.close()


# ─── Tool Handlers ─────────────────────────────────────────────────────────

def handle_log_lead(args, call_id=None, caller_phone=None):
    db = get_db()
    db.execute(
        """INSERT INTO leads (caller_name, case_type, case_summary, score, routing, email, zip_code, phone, call_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            args.get("caller_name", "Unknown"),
            args.get("case_type"),
            args.get("case_summary"),
            args.get("score", 0),
            args.get("routing", "unknown"),
            args.get("email"),
            args.get("zip_code"),
            caller_phone,
            call_id,
            datetime.now().isoformat(),
        ),
    )
    db.commit()

    # Email notification for qualified leads
    if args.get("routing") == "qualified":
        notification_email = os.environ.get("NOTIFICATION_EMAIL")
        if notification_email:
            try:
                resend.Emails.send({
                    "from": "Green Shield Pest Control <onboarding@resend.dev>",
                    "to": [notification_email],
                    "subject": f"New Hot Lead: {args.get('caller_name', 'Unknown')} — {(args.get('case_type') or 'Unknown').title()}",
                    "html": f"""
                    <div style="font-family: Georgia, serif; max-width: 600px; margin: 0 auto; color: #1a2e1a;">
                        <div style="background: #0d1f12; padding: 24px; text-align: center;">
                            <h1 style="color: #00D26A; margin: 0; font-size: 24px;">Green Shield Pest Control</h1>
                            <p style="color: rgba(255,255,255,0.6); margin: 4px 0 0;">New Hot Lead Alert — Inspection Ready</p>
                        </div>
                        <div style="padding: 32px 24px;">
                            <p><strong>Name:</strong> {args.get('caller_name', 'Unknown')}</p>
                            <p><strong>Pest Type:</strong> {args.get('case_type') or 'Not specified'}</p>
                            <p><strong>Score:</strong> {args.get('score', 0)}/10</p>
                            <p><strong>Phone:</strong> {caller_phone or 'Not available'}</p>
                            <p><strong>Summary:</strong> {args.get('case_summary') or 'No summary provided'}</p>
                            <div style="text-align: center; margin: 32px 0;">
                                <a href="{os.environ.get('BASE_URL', 'http://localhost:5002')}/dashboard"
                                   style="background: #00D26A; color: #0d1f12; padding: 14px 32px;
                                          text-decoration: none; font-weight: bold; border-radius: 4px;">
                                    View Dashboard
                                </a>
                            </div>
                        </div>
                    </div>
                    """,
                })
            except Exception as e:
                print(f"Failed to send qualified lead notification email: {e}")

    return f"Lead {args.get('caller_name')} logged successfully. Score: {args.get('score')}/10, Routing: {args.get('routing')}."


def handle_check_availability(args):
    today = datetime.now()
    slots = []
    day = today + timedelta(days=1)
    count = 0
    while count < 3:
        if day.weekday() < 5:  # Mon-Fri
            slots.append({
                "date": day.strftime("%A, %B %d"),
                "times": ["9:00 AM", "1:00 PM", "4:00 PM"],
                "technician": "Technician Martinez" if count == 0 else "Technician Johnson",
            })
            count += 1
        day += timedelta(days=1)

    return json.dumps({
        "available_slots": slots,
        "note": "Inspections are free. Same-day service available for emergencies.",
    })


def handle_send_nurture_email(args):
    name = args.get("name", "there")
    email = args.get("email")
    case_type = args.get("case_type", "your pest concern")

    if not email:
        return "Error: no email address provided."

    html = f"""
    <div style="font-family: Georgia, serif; max-width: 600px; margin: 0 auto; color: #1a2e1a;">
        <div style="background: #0d1f12; padding: 24px; text-align: center;">
            <h1 style="color: #00D26A; margin: 0; font-size: 24px;">Green Shield Pest Control</h1>
            <p style="color: rgba(255,255,255,0.6); margin: 4px 0 0;">Protecting Texas Homes Since 2015</p>
        </div>

        <div style="padding: 32px 24px;">
            <p>Dear {name},</p>

            <p>Thank you for reaching out to Green Shield Pest Control about {case_type}.
            We understand pest problems can be stressful, and we appreciate you contacting us.</p>

            <p>Here are some tips to help while you're deciding on next steps:</p>

            <ul style="line-height: 1.8;">
                <li><strong>Seal Entry Points</strong> — Caulk cracks around doors, windows, and pipes where pests enter</li>
                <li><strong>Eliminate Food & Moisture Sources</strong> — Fix leaky pipes, store food in sealed containers, empty trash regularly</li>
                <li><strong>Reduce Clutter</strong> — Pests love dark, undisturbed spaces — clear out garages and storage areas</li>
                <li><strong>Outdoor Maintenance</strong> — Trim vegetation away from the house and remove standing water</li>
            </ul>

            <p>When you're ready for a professional assessment, our inspections are <strong>100% free</strong>
            and we offer same-day service for urgent situations.</p>

            <div style="text-align: center; margin: 32px 0;">
                <a href="https://greenshieldpest.com/book"
                   style="background: #00D26A; color: #0d1f12; padding: 14px 32px;
                          text-decoration: none; font-weight: bold; border-radius: 4px;">
                    Book a Free Inspection
                </a>
            </div>

            <p>Warm regards,<br>
            <strong>The Green Shield Pest Control Team</strong><br>
            <span style="color: #888;">Texas</span></p>
        </div>

        <div style="background: #f5f5f5; padding: 16px; text-align: center; font-size: 12px; color: #888;">
            Green Shield Pest Control | Texas<br>
            Licensed &amp; family-safe treatments.
        </div>
    </div>
    """

    try:
        resend.Emails.send({
            "from": "Green Shield Pest Control <onboarding@resend.dev>",
            "to": [email],
            "subject": f"Your Free Quote &amp; Pest Tips — Green Shield Pest Control",
            "html": html,
        })
        return f"Quote email sent to {email} successfully."
    except Exception as e:
        return f"Email failed: {str(e)}"


def handle_transfer_call(args):
    return json.dumps({
        "destination": {
            "type": "number",
            "number": "+18305555555",
            "message": "Connecting you with a Green Shield specialist right now.",
        }
    })


TOOL_HANDLERS = {
    "log_lead": handle_log_lead,
    "check_availability": handle_check_availability,
    "send_nurture_email": handle_send_nurture_email,
    "transfer_call": handle_transfer_call,
}


# ─── Webhook Routes ────────────────────────────────────────────────────────

@app.route("/webhook/tools", methods=["POST"])
def webhook_tools():
    data = request.get_json()
    message = data.get("message", {})
    call_obj = message.get("call", {})
    call_id = call_obj.get("id")
    caller_phone = call_obj.get("customer", {}).get("number")

    # Vapi sends tool calls in message.toolCallList
    # Each item may use "function.name" instead of "name" directly
    tool_call_list = message.get("toolCallList", [])
    if not tool_call_list:
        # Try alternate payload structure — tool call may be at top level
        tool_call_list = data.get("toolCallList", [])

    results = []
    for tool_call in tool_call_list:
        # Support both flat (name) and nested (function.name) structures
        tool_name = tool_call.get("name") or tool_call.get("function", {}).get("name")
        tool_args = tool_call.get("parameters", {}) or tool_call.get("arguments", {}) or tool_call.get("function", {}).get("arguments", {})
        tool_call_id = tool_call.get("id") or tool_call.get("toolCallId")
        handler = TOOL_HANDLERS.get(tool_name)
        if handler:
            if tool_name == "log_lead":
                result = handler(tool_args, call_id, caller_phone)
            else:
                result = handler(tool_args)
        else:
            result = f"Unknown tool: {tool_name}"

        results.append({
            "toolCallId": tool_call_id,
            "name": tool_name,
            "result": result,
        })

    return jsonify({"results": results})


@app.route("/webhook/vapi", methods=["POST"])
def webhook_vapi():
    data = request.get_json()
    message = data.get("message", {})
    event_type = message.get("type")

    if event_type == "end-of-call-report":
        print(f"\n{'='*50}")
        print(f"Call ended: {message.get('endedReason')}")
        transcript = message.get("artifact", {}).get("transcript", "")
        if transcript:
            print(f"Transcript preview: {transcript[:200]}...")
        print(f"{'='*50}\n")

        # Store call duration
        duration = message.get("durationSeconds")
        call_id = message.get("call", {}).get("id")
        if duration and call_id:
            db = get_db()
            db.execute(
                "UPDATE leads SET call_duration_seconds = ? WHERE call_id = ?",
                (duration, call_id),
            )
            db.commit()

    elif event_type == "status-update":
        status = message.get("status")
        print(f"Call status: {status}")

    return jsonify({"ok": True})


# ─── Auth Routes ───────────────────────────────────────────────────────────

LOGIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sign In — Green Shield Pest Control</title>
<style>
  :root {
    --dark: #0d1f12;
    --gold: #00D26A;
    --bg: #f8f9fa;
    --text: #1a2e1a;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--dark);
    color: var(--text);
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 100vh;
  }
  .login-card {
    background: white;
    border-radius: 12px;
    box-shadow: 0 8px 32px rgba(0,0,0,0.3);
    width: 100%;
    max-width: 400px;
    padding: 40px 32px;
  }
  .login-card h1 {
    color: var(--dark);
    font-size: 22px;
    text-align: center;
    margin-bottom: 4px;
  }
  .login-card .subtitle {
    color: #888;
    font-size: 13px;
    text-align: center;
    margin-bottom: 24px;
  }
  .login-card .brand {
    text-align: center;
    margin-bottom: 24px;
  }
  .login-card .brand span {
    color: var(--gold);
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 0.5px;
  }
  .google-btn {
    display: block;
    width: 100%;
    padding: 12px;
    background: var(--dark);
    color: white;
    border: none;
    border-radius: 6px;
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    text-align: center;
    text-decoration: none;
    margin-bottom: 20px;
  }
  .google-btn:hover { background: #1a3a1a; }
  .divider {
    display: flex;
    align-items: center;
    margin: 20px 0;
    color: #ccc;
    font-size: 12px;
  }
  .divider::before, .divider::after {
    content: '';
    flex: 1;
    border-bottom: 1px solid #e0e0e0;
  }
  .divider span { padding: 0 12px; }
  label {
    display: block;
    font-size: 13px;
    font-weight: 600;
    margin-bottom: 4px;
    color: #555;
  }
  input[type="email"], input[type="password"] {
    width: 100%;
    padding: 10px 12px;
    border: 1px solid #ddd;
    border-radius: 6px;
    font-size: 14px;
    margin-bottom: 16px;
  }
  input:focus { outline: none; border-color: var(--gold); }
  .submit-btn {
    width: 100%;
    padding: 12px;
    background: var(--gold);
    color: var(--dark);
    border: none;
    border-radius: 6px;
    font-size: 14px;
    font-weight: 700;
    cursor: pointer;
  }
  .submit-btn:hover { background: #00b85a; }
  .alert {
    padding: 10px 14px;
    border-radius: 6px;
    font-size: 13px;
    margin-bottom: 16px;
  }
  .alert-error { background: #fdecea; color: #c0392b; }
  .alert-info { background: #e8f4fd; color: #2471a3; }
</style>
</head>
<body>
<div class="login-card">
  <div class="brand"><span>Green Shield Pest Control</span></div>
  <h1>Sign In</h1>
  <p class="subtitle">AI Receptionist Dashboard</p>

  {% if error %}
  <div class="alert alert-error">{{ error }}</div>
  {% endif %}
  {% if message %}
  <div class="alert alert-info">{{ message }}</div>
  {% endif %}

  <a href="{{ url_for('google_login') }}" class="google-btn">Sign in with Google</a>

  <div class="divider"><span>or use email</span></div>

  <form method="POST" action="{{ url_for('login') }}">
    <label for="email">Email</label>
    <input type="email" id="email" name="email" required placeholder="you@greenshieldpest.com">

    <label for="password">Password</label>
    <input type="password" id="password" name="password" required placeholder="Enter your password">

    <button type="submit" class="submit-btn">Sign In</button>
  </form>
</div>
</body>
</html>
"""


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        user = User.get_by_email(email)
        if user and user.password_hash and check_password_hash(user.password_hash, password):
            login_user(user)
            next_page = request.args.get("next")
            return redirect(next_page or url_for("dashboard"))
        return render_template_string(LOGIN_HTML, error="Invalid email or password.", message=None)
    message = request.args.get("message")
    error = request.args.get("error")
    return render_template_string(LOGIN_HTML, error=error, message=message)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login", message="You have been signed out."))


@app.route("/login/google")
def google_login():
    redirect_uri = url_for("google_callback", _external=True)
    return google.authorize_redirect(redirect_uri)


@app.route("/login/google/callback")
def google_callback():
    token = google.authorize_access_token()
    userinfo = token.get("userinfo") or google.userinfo()
    google_id = userinfo["sub"]
    email = userinfo["email"]

    # Look up by google_id first, then by email
    user = User.get_by_google_id(google_id)
    if not user:
        user = User.get_by_email(email)
        if not user:
            return redirect(url_for("login", error="No account found for this email. Access is invite-only."))
        # Link google_id to existing user
        db = sqlite3.connect(DB_PATH)
        db.execute("UPDATE users SET google_id = ? WHERE id = ?", (google_id, user.id))
        db.commit()
        db.close()
        user = User.get_by_id(user.id)

    login_user(user)
    return redirect(url_for("dashboard"))


# ─── Dashboard ─────────────────────────────────────────────────────────────

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Green Shield Pest Control — AI Receptionist Dashboard</title>
<style>
  :root {
    --dark: #0d1f12;
    --gold: #00D26A;
    --green: #27ae60;
    --yellow: #f39c12;
    --red: #e74c3c;
    --bg: #f8f9fa;
    --text: #2D1B2E;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); }

  .header {
    background: var(--dark);
    padding: 20px 32px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .header h1 { color: var(--gold); font-size: 20px; }
  .header p { color: rgba(255,255,255,0.5); font-size: 13px; }
  .header .stats { display: flex; gap: 24px; }
  .header nav { display: flex; gap: 16px; align-items: center; }
  .header nav a {
    color: rgba(255,255,255,0.7);
    text-decoration: none;
    font-size: 13px;
    padding: 6px 12px;
    border-radius: 4px;
  }
  .header nav a:hover { background: rgba(255,255,255,0.1); color: white; }
  .stat { text-align: center; }
  .stat-value { color: white; font-size: 24px; font-weight: 700; }
  .stat-label { color: rgba(255,255,255,0.5); font-size: 11px; text-transform: uppercase; }

  .container { max-width: 1200px; margin: 0 auto; padding: 24px; }

  .filter-bar {
    background: white;
    padding: 16px;
    border-radius: 8px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.08);
    margin-bottom: 16px;
    display: flex;
    gap: 12px;
    align-items: center;
    flex-wrap: wrap;
  }
  .filter-bar input, .filter-bar select {
    padding: 8px 12px;
    border: 1px solid #ddd;
    border-radius: 6px;
    font-size: 13px;
  }
  .filter-bar input { flex: 1; min-width: 180px; }
  .filter-bar input:focus, .filter-bar select:focus { outline: none; border-color: var(--gold); }
  .filter-btn {
    background: var(--gold);
    color: var(--dark);
    border: none;
    padding: 8px 20px;
    border-radius: 6px;
    font-size: 13px;
    font-weight: 700;
    cursor: pointer;
  }
  .filter-btn:hover { background: #00b85a; }
  .filter-clear {
    color: #888;
    font-size: 13px;
    text-decoration: none;
  }
  .filter-clear:hover { color: var(--text); }

  table { width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
  th { background: var(--dark); color: var(--gold); padding: 12px 16px; text-align: left; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
  td { padding: 14px 16px; border-bottom: 1px solid #eee; font-size: 14px; }
  tr.clickable:hover { background: #fafafa; cursor: pointer; }

  .badge {
    display: inline-block;
    padding: 4px 10px;
    border-radius: 12px;
    font-size: 12px;
    font-weight: 600;
  }
  .badge-qualified { background: #d4edda; color: #155724; }
  .badge-nurture { background: #fff3cd; color: #856404; }
  .badge-redirect { background: #f8d7da; color: #721c24; }

  .badge-status {
    display: inline-block;
    padding: 3px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
  }
  .badge-status-new { background: #e2e3e5; color: #383d41; }
  .badge-status-contacted { background: #cce5ff; color: #004085; }
  .badge-status-inspection_booked { background: #fff3cd; color: #856404; }
  .badge-status-scheduled { background: #d4edda; color: #155724; }
  .badge-status-closed { background: #f8d7da; color: #721c24; }

  .score { font-weight: 700; font-size: 16px; }
  .score-high { color: var(--green); }
  .score-mid { color: var(--yellow); }
  .score-low { color: var(--red); }

  .empty { text-align: center; padding: 64px; color: #999; }
  .refresh { color: rgba(255,255,255,0.7); background: none; border: 1px solid rgba(255,255,255,0.3); padding: 8px 16px; border-radius: 4px; cursor: pointer; font-size: 13px; }
  .refresh:hover { background: rgba(255,255,255,0.1); }
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>Green Shield Pest Control</h1>
    <p>AI Receptionist Dashboard</p>
  </div>
  <div class="stats">
    <div class="stat">
      <div class="stat-value" id="totalLeads">{{ total }}</div>
      <div class="stat-label">Total Leads</div>
    </div>
    <div class="stat">
      <div class="stat-value" style="color: #27ae60;" id="qualifiedCount">{{ qualified }}</div>
      <div class="stat-label">Qualified</div>
    </div>
    <div class="stat">
      <div class="stat-value" style="color: #f39c12;" id="nurtureCount">{{ nurture }}</div>
      <div class="stat-label">Nurture</div>
    </div>
    <div class="stat">
      <div class="stat-value" style="color: #e74c3c;" id="redirectCount">{{ redirect }}</div>
      <div class="stat-label">Redirected</div>
    </div>
  </div>
  <nav>
    {% if current_user.is_admin %}
    <a href="{{ url_for('admin_users') }}">Users</a>
    <a href="{{ url_for('admin_costs') }}">Costs</a>
    {% endif %}
    <a href="{{ url_for('logout') }}">Logout ({{ current_user.name }})</a>
  </nav>
</div>
<div class="container">
  <form class="filter-bar" method="GET" action="{{ url_for('dashboard') }}">
    <input type="text" name="q" placeholder="Search name, phone, or email..." value="{{ q or '' }}">
    <select name="routing">
      <option value="">All Routing</option>
      <option value="qualified" {% if routing == 'qualified' %}selected{% endif %}>Qualified</option>
      <option value="nurture" {% if routing == 'nurture' %}selected{% endif %}>Nurture</option>
      <option value="redirect" {% if routing == 'redirect' %}selected{% endif %}>Redirect</option>
    </select>
    <select name="status">
      <option value="">All Status</option>
      <option value="new" {% if status_filter == 'new' %}selected{% endif %}>New</option>
      <option value="contacted" {% if status_filter == 'contacted' %}selected{% endif %}>Contacted</option>
      <option value="inspection_booked" {% if status_filter == 'inspection_booked' %}selected{% endif %}>Inspection Booked</option>
      <option value="scheduled" {% if status_filter == 'scheduled' %}selected{% endif %}>Scheduled</option>
      <option value="closed" {% if status_filter == 'closed' %}selected{% endif %}>Closed</option>
    </select>
    <button type="submit" class="filter-btn">Filter</button>
    <a href="{{ url_for('dashboard') }}" class="filter-clear">Clear</a>
  </form>

  {% if leads %}
  <table>
    <thead>
      <tr>
        <th>Time</th>
        <th>Name</th>
        <th>Pest Type</th>
        <th>Score</th>
        <th>Routing</th>
        <th>Status</th>
        <th>Phone</th>
        <th>Email</th>
        <th>Zip</th>
      </tr>
    </thead>
    <tbody>
      {% for lead in leads %}
      <tr class="clickable" onclick="location.href='/lead/{{ lead.id }}'">
        <td>{{ lead.created_at[:16] }}</td>
        <td><strong>{{ lead.caller_name }}</strong></td>
        <td>{{ lead.case_type or '—' }}</td>
        <td>
          <span class="score {% if lead.score >= 7 %}score-high{% elif lead.score >= 4 %}score-mid{% else %}score-low{% endif %}">
            {{ lead.score }}/10
          </span>
        </td>
        <td>
          <span class="badge badge-{{ lead.routing }}">
            {{ lead.routing | upper }}
          </span>
        </td>
        <td>
          <span class="badge-status badge-status-{{ lead.status }}">
            {{ lead.status | replace('_', ' ') }}
          </span>
        </td>
        <td>{{ lead.phone or '—' }}</td>
        <td>{{ lead.email or '—' }}</td>
        <td>{{ lead.zip_code or '—' }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}
  <div class="empty">
    <p style="font-size: 18px; margin-bottom: 8px;">No leads yet</p>
    <p>Call your Vapi number to generate the first lead.</p>
  </div>
  {% endif %}
</div>

<script>
  // Notification banner for new qualified leads + auto-refresh
  (function() {
    var leads = {{ leads_json | safe }};
    var lastSeenKey = 'greenshield_last_seen_lead';
    var lastSeen = parseInt(localStorage.getItem(lastSeenKey) || '0', 10);

    // Find the max lead ID
    var maxId = 0;
    for (var i = 0; i < leads.length; i++) {
      if (leads[i].id > maxId) maxId = leads[i].id;
    }

    // Check for new qualified leads
    if (lastSeen > 0) {
      for (var i = 0; i < leads.length; i++) {
        var lead = leads[i];
        if (lead.id > lastSeen && lead.routing === 'qualified') {
          // Show notification banner
          var banner = document.createElement('div');
          banner.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:9999;background:#00D26A;color:#0d1f12;padding:14px 24px;font-size:15px;font-weight:600;text-align:center;box-shadow:0 2px 8px rgba(0,0,0,0.2);';
          banner.innerHTML = 'New hot lead: ' + lead.caller_name + ' &mdash; ' + (lead.case_type || 'Unknown') + ' <a href="/lead/' + lead.id + '" style="color:#0d1f12;text-decoration:underline;margin-left:8px;">(Click to view)</a>';
          document.body.prepend(banner);
          // Auto-remove after 15 seconds
          setTimeout(function() { banner.remove(); }, 15000);
          break; // Show only the most recent
        }
      }
    }

    // Update last seen to latest ID
    if (maxId > lastSeen) {
      localStorage.setItem(lastSeenKey, maxId.toString());
    }

    // Auto-refresh every 10 seconds
    setTimeout(function() { location.reload(); }, 10000);
  })();
</script>
</body>
</html>
"""


ADMIN_USERS_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>User Management — Green Shield Pest Control</title>
<style>
  :root {
    --dark: #0d1f12;
    --gold: #00D26A;
    --green: #27ae60;
    --red: #e74c3c;
    --bg: #f8f9fa;
    --text: #1a2e1a;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); }

  .header {
    background: var(--dark);
    padding: 20px 32px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .header h1 { color: var(--gold); font-size: 20px; }
  .header p { color: rgba(255,255,255,0.5); font-size: 13px; }
  .header nav { display: flex; gap: 16px; align-items: center; }
  .header nav a {
    color: rgba(255,255,255,0.7);
    text-decoration: none;
    font-size: 13px;
    padding: 6px 12px;
    border-radius: 4px;
  }
  .header nav a:hover { background: rgba(255,255,255,0.1); color: white; }

  .container { max-width: 1000px; margin: 0 auto; padding: 24px; }

  .flash {
    padding: 12px 16px;
    border-radius: 6px;
    font-size: 14px;
    margin-bottom: 16px;
  }
  .flash-success { background: #d4edda; color: #155724; }
  .flash-error { background: #f8d7da; color: #721c24; }

  table { width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.08); margin-bottom: 32px; }
  th { background: var(--dark); color: var(--gold); padding: 12px 16px; text-align: left; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
  td { padding: 14px 16px; border-bottom: 1px solid #eee; font-size: 14px; vertical-align: middle; }
  tr:hover { background: #fafafa; }

  .badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 12px;
    font-size: 12px;
    font-weight: 600;
  }
  .badge-admin { background: #d4edda; color: #155724; }
  .badge-attorney { background: #cce5ff; color: #004085; }
  .badge-staff { background: #e2e3e5; color: #383d41; }

  select {
    padding: 6px 10px;
    border: 1px solid #ddd;
    border-radius: 4px;
    font-size: 13px;
    cursor: pointer;
  }
  .btn-remove {
    background: var(--red);
    color: white;
    border: none;
    padding: 6px 14px;
    border-radius: 4px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
  }
  .btn-remove:hover { background: #c0392b; }

  .you-label {
    color: #888;
    font-size: 12px;
    font-style: italic;
  }

  .invite-form {
    background: white;
    border-radius: 8px;
    padding: 24px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.08);
  }
  .invite-form h2 {
    font-size: 18px;
    margin-bottom: 16px;
    color: var(--dark);
  }
  .form-row {
    display: flex;
    gap: 12px;
    align-items: flex-end;
    flex-wrap: wrap;
  }
  .form-group {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-width: 140px;
  }
  .form-group label {
    font-size: 12px;
    font-weight: 600;
    color: #555;
    margin-bottom: 4px;
  }
  .form-group input, .form-group select {
    padding: 10px 12px;
    border: 1px solid #ddd;
    border-radius: 6px;
    font-size: 14px;
  }
  .form-group input:focus, .form-group select:focus { outline: none; border-color: var(--gold); }
  .btn-invite {
    background: var(--gold);
    color: var(--dark);
    border: none;
    padding: 10px 24px;
    border-radius: 6px;
    font-size: 14px;
    font-weight: 700;
    cursor: pointer;
    white-space: nowrap;
  }
  .btn-invite:hover { background: #00b85a; }
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>Green Shield Pest Control</h1>
    <p>User Management</p>
  </div>
  <nav>
    <a href="{{ url_for('dashboard') }}">Dashboard</a>
    <a href="{{ url_for('admin_costs') }}">Costs</a>
    <a href="{{ url_for('logout') }}">Logout</a>
  </nav>
</div>
<div class="container">
  {% with messages = get_flashed_messages(with_categories=true) %}
    {% for category, message in messages %}
      <div class="flash flash-{{ category }}">{{ message }}</div>
    {% endfor %}
  {% endwith %}

  <table>
    <thead>
      <tr>
        <th>Name</th>
        <th>Email</th>
        <th>Role</th>
        <th>Joined</th>
        <th>Actions</th>
      </tr>
    </thead>
    <tbody>
      {% for user in users %}
      <tr>
        <td><strong>{{ user.name }}</strong></td>
        <td>{{ user.email }}</td>
        <td><span class="badge badge-{{ user.role }}">{{ user.role | upper }}</span></td>
        <td>{{ user.created_at[:10] }}</td>
        <td>
          {% if user.id == current_user_id %}
            <span class="you-label">You</span>
          {% else %}
            <form method="POST" action="{{ url_for('admin_users_role') }}" style="display:inline;">
              <input type="hidden" name="user_id" value="{{ user.id }}">
              <select name="role" onchange="this.form.submit()">
                <option value="admin" {% if user.role == 'admin' %}selected{% endif %}>Admin</option>
                <option value="attorney" {% if user.role == 'attorney' %}selected{% endif %}>Attorney</option>
                <option value="staff" {% if user.role == 'staff' %}selected{% endif %}>Staff</option>
              </select>
            </form>
            <form method="POST" action="{{ url_for('admin_users_delete') }}" style="display:inline; margin-left: 8px;" onsubmit="return confirm('Remove this user?');">
              <input type="hidden" name="user_id" value="{{ user.id }}">
              <button type="submit" class="btn-remove">Remove</button>
            </form>
          {% endif %}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>

  <div class="invite-form">
    <h2>Invite New User</h2>
    <form method="POST" action="{{ url_for('admin_users_invite') }}">
      <div class="form-row">
        <div class="form-group">
          <label for="name">Name</label>
          <input type="text" id="name" name="name" required placeholder="Full name">
        </div>
        <div class="form-group">
          <label for="email">Email</label>
          <input type="email" id="email" name="email" required placeholder="user@westbrooklaw.com">
        </div>
        <div class="form-group">
          <label for="role">Role</label>
          <select id="role" name="role">
            <option value="staff">Staff</option>
            <option value="attorney">Attorney</option>
            <option value="admin">Admin</option>
          </select>
        </div>
        <div class="form-group">
          <label for="password">Temporary Password</label>
          <input type="password" id="password" name="password" required placeholder="Temporary password">
        </div>
        <button type="submit" class="btn-invite">Invite</button>
      </div>
    </form>
  </div>
</div>
</body>
</html>
"""


@app.route("/admin/users")
@role_required("admin")
def admin_users():
    db = get_db()
    users = db.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    return render_template_string(ADMIN_USERS_HTML, users=users, current_user_id=current_user.id)


@app.route("/admin/users/invite", methods=["POST"])
@role_required("admin")
def admin_users_invite():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    role = request.form.get("role", "staff")
    password = request.form.get("password", "")

    if not name or not email or not password:
        flash("All fields are required.", "error")
        return redirect(url_for("admin_users"))

    if role not in ("admin", "attorney", "staff"):
        flash("Invalid role.", "error")
        return redirect(url_for("admin_users"))

    db = get_db()
    existing = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if existing:
        flash(f"A user with email {email} already exists.", "error")
        return redirect(url_for("admin_users"))

    db.execute(
        "INSERT INTO users (email, name, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?)",
        (email, name, generate_password_hash(password, method="pbkdf2:sha256"), role, datetime.now().isoformat()),
    )
    db.commit()
    flash(f"User {name} ({email}) invited as {role}.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/role", methods=["POST"])
@role_required("admin")
def admin_users_role():
    user_id = request.form.get("user_id", type=int)
    new_role = request.form.get("role", "")

    if new_role not in ("admin", "attorney", "staff"):
        flash("Invalid role.", "error")
        return redirect(url_for("admin_users"))

    db = get_db()
    target = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not target:
        flash("User not found.", "error")
        return redirect(url_for("admin_users"))

    # Prevent demoting the last admin
    if target["role"] == "admin" and new_role != "admin":
        admin_count = db.execute("SELECT COUNT(*) as cnt FROM users WHERE role = 'admin'").fetchone()["cnt"]
        if admin_count <= 1:
            flash("Cannot demote the last admin.", "error")
            return redirect(url_for("admin_users"))

    db.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, user_id))
    db.commit()
    flash(f"Role updated to {new_role} for {target['name']}.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/delete", methods=["POST"])
@role_required("admin")
def admin_users_delete():
    user_id = request.form.get("user_id", type=int)

    if user_id == current_user.id:
        flash("You cannot remove yourself.", "error")
        return redirect(url_for("admin_users"))

    db = get_db()
    target = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not target:
        flash("User not found.", "error")
        return redirect(url_for("admin_users"))

    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    flash(f"User {target['name']} removed.", "success")
    return redirect(url_for("admin_users"))


COSTS_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Cost Analysis — Green Shield Pest Control</title>
<style>
  :root {
    --dark: #0d1f12;
    --gold: #00D26A;
    --green: #27ae60;
    --yellow: #f39c12;
    --red: #e74c3c;
    --bg: #f8f9fa;
    --text: #1a2e1a;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); }

  .header {
    background: var(--dark);
    padding: 20px 32px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .header h1 { color: var(--gold); font-size: 20px; }
  .header p { color: rgba(255,255,255,0.5); font-size: 13px; }
  .header nav { display: flex; gap: 16px; align-items: center; }
  .header nav a {
    color: rgba(255,255,255,0.7);
    text-decoration: none;
    font-size: 13px;
    padding: 6px 12px;
    border-radius: 4px;
  }
  .header nav a:hover { background: rgba(255,255,255,0.1); color: white; }

  .container { max-width: 1000px; margin: 0 auto; padding: 24px; }

  .cards {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
    gap: 16px;
    margin-bottom: 32px;
  }
  .card {
    background: white;
    border-radius: 8px;
    padding: 24px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.08);
    text-align: center;
  }
  .card-value {
    font-size: 32px;
    font-weight: 700;
    color: var(--dark);
    margin-bottom: 4px;
  }
  .card-value.gold { color: var(--gold); }
  .card-value.green { color: var(--green); }
  .card-label {
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: #888;
    font-weight: 600;
  }

  .section {
    background: white;
    border-radius: 8px;
    padding: 24px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.08);
    margin-bottom: 24px;
  }
  .section h3 {
    font-size: 16px;
    color: var(--dark);
    margin-bottom: 16px;
    padding-bottom: 8px;
    border-bottom: 1px solid #eee;
  }

  .pricing-table { width: 100%; border-collapse: collapse; }
  .pricing-table th {
    background: var(--dark);
    color: var(--gold);
    padding: 10px 16px;
    text-align: left;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .pricing-table td {
    padding: 12px 16px;
    border-bottom: 1px solid #eee;
    font-size: 14px;
  }
  .pricing-table tr:last-child td { border-bottom: none; }
  .pricing-table .cost { font-weight: 600; color: var(--dark); }
  .month-label { font-size: 14px; color: #888; margin-bottom: 20px; }
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>Green Shield Pest Control</h1>
    <p>Cost Analysis</p>
  </div>
  <nav>
    <a href="{{ url_for('dashboard') }}">Dashboard</a>
    <a href="{{ url_for('admin_users') }}">Users</a>
    <a href="{{ url_for('logout') }}">Logout</a>
  </nav>
</div>
<div class="container">
  <p class="month-label">Showing data for {{ month_label }}</p>

  <div class="cards">
    <div class="card">
      <div class="card-value">{{ total_calls }}</div>
      <div class="card-label">Total Calls (This Month)</div>
    </div>
    <div class="card">
      <div class="card-value">{{ total_minutes }}</div>
      <div class="card-label">Total Minutes</div>
    </div>
    <div class="card">
      <div class="card-value gold">${{ '%.2f' | format(vapi_cost) }}</div>
      <div class="card-label">Est. Vapi Cost (@$0.05/min)</div>
    </div>
    <div class="card">
      <div class="card-value green">{{ emails_sent }}</div>
      <div class="card-label">Emails Sent</div>
    </div>
  </div>

  <div class="section">
    <h3>Pricing Reference</h3>
    <table class="pricing-table">
      <thead>
        <tr>
          <th>Service</th>
          <th>Cost</th>
          <th>Notes</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td>Vapi</td>
          <td class="cost">~$0.05/min</td>
          <td>Per-minute voice AI usage</td>
        </tr>
        <tr>
          <td>Resend</td>
          <td class="cost">Free up to 3,000/mo</td>
          <td>Transactional email delivery</td>
        </tr>
        <tr>
          <td>Phone Number</td>
          <td class="cost">~$2/mo</td>
          <td>Dedicated Vapi phone number</td>
        </tr>
        <tr>
          <td>ngrok</td>
          <td class="cost">Free or $8/mo</td>
          <td>Tunnel for local webhook development</td>
        </tr>
      </tbody>
    </table>
  </div>
</div>
</body>
</html>
"""


@app.route("/admin/costs")
@role_required("admin")
def admin_costs():
    db = get_db()
    now = datetime.now()
    month_start = now.strftime("%Y-%m-01")
    month_label = now.strftime("%B %Y")

    # Current month stats
    stats = db.execute(
        "SELECT COUNT(*) as cnt, COALESCE(SUM(call_duration_seconds), 0) as total_seconds FROM leads WHERE created_at >= ?",
        (month_start,),
    ).fetchone()

    total_calls = stats["cnt"]
    total_seconds = stats["total_seconds"]
    total_minutes = round(total_seconds / 60, 1) if total_seconds else 0
    vapi_cost = total_minutes * 0.05

    # Estimate emails sent: nurture emails + qualified notification emails
    email_stats = db.execute(
        "SELECT COUNT(*) as cnt FROM leads WHERE created_at >= ? AND routing IN ('nurture', 'qualified')",
        (month_start,),
    ).fetchone()
    emails_sent = email_stats["cnt"]

    return render_template_string(
        COSTS_HTML,
        total_calls=total_calls,
        total_minutes=total_minutes,
        vapi_cost=vapi_cost,
        emails_sent=emails_sent,
        month_label=month_label,
    )


LEAD_DETAIL_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ lead.caller_name }} — Green Shield Pest Control</title>
<style>
  :root {
    --dark: #0d1f12;
    --gold: #00D26A;
    --green: #27ae60;
    --yellow: #f39c12;
    --red: #e74c3c;
    --bg: #f8f9fa;
    --text: #2D1B2E;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); }

  .header {
    background: var(--dark);
    padding: 20px 32px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .header h1 { color: var(--gold); font-size: 20px; }
  .header p { color: rgba(255,255,255,0.5); font-size: 13px; }
  .header nav { display: flex; gap: 16px; align-items: center; }
  .header nav a {
    color: rgba(255,255,255,0.7);
    text-decoration: none;
    font-size: 13px;
    padding: 6px 12px;
    border-radius: 4px;
  }
  .header nav a:hover { background: rgba(255,255,255,0.1); color: white; }

  .container { max-width: 900px; margin: 0 auto; padding: 24px; }

  .back-link { color: var(--gold); text-decoration: none; font-size: 14px; display: inline-block; margin-bottom: 20px; }
  .back-link:hover { text-decoration: underline; }

  .lead-header {
    display: flex;
    align-items: center;
    gap: 16px;
    margin-bottom: 24px;
  }
  .lead-header h2 { font-size: 28px; color: var(--dark); }
  .lead-score {
    font-size: 20px;
    font-weight: 700;
    padding: 4px 12px;
    border-radius: 8px;
  }
  .lead-score-high { color: var(--green); background: #d4edda; }
  .lead-score-mid { color: var(--yellow); background: #fff3cd; }
  .lead-score-low { color: var(--red); background: #f8d7da; }

  .badge {
    display: inline-block;
    padding: 4px 12px;
    border-radius: 12px;
    font-size: 12px;
    font-weight: 600;
  }
  .badge-qualified { background: #d4edda; color: #155724; }
  .badge-nurture { background: #fff3cd; color: #856404; }
  .badge-redirect { background: #f8d7da; color: #721c24; }

  .info-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 16px;
    background: white;
    padding: 24px;
    border-radius: 8px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.08);
    margin-bottom: 24px;
  }
  .info-item label {
    display: block;
    font-size: 11px;
    text-transform: uppercase;
    color: #888;
    font-weight: 600;
    letter-spacing: 0.5px;
    margin-bottom: 4px;
  }
  .info-item span { font-size: 15px; font-weight: 500; }

  .section {
    background: white;
    padding: 24px;
    border-radius: 8px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.08);
    margin-bottom: 24px;
  }
  .section h3 {
    font-size: 16px;
    color: var(--dark);
    margin-bottom: 12px;
    padding-bottom: 8px;
    border-bottom: 1px solid #eee;
  }
  .section p { font-size: 14px; line-height: 1.6; color: #555; }

  .status-buttons { display: flex; gap: 8px; flex-wrap: wrap; }
  .status-btn {
    padding: 8px 16px;
    border: 2px solid #ddd;
    background: white;
    border-radius: 6px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    text-transform: capitalize;
  }
  .status-btn:hover { border-color: var(--gold); }
  .status-btn.active {
    background: var(--dark);
    color: var(--gold);
    border-color: var(--dark);
  }

  .note {
    padding: 14px 0;
    border-bottom: 1px solid #eee;
  }
  .note:last-of-type { border-bottom: none; }
  .note-meta {
    font-size: 12px;
    color: #888;
    margin-bottom: 4px;
  }
  .note-meta strong { color: var(--dark); }
  .note-content { font-size: 14px; line-height: 1.5; }

  .note-form { margin-top: 16px; padding-top: 16px; border-top: 1px solid #eee; }
  .note-form textarea {
    width: 100%;
    padding: 12px;
    border: 1px solid #ddd;
    border-radius: 6px;
    font-size: 14px;
    font-family: inherit;
    resize: vertical;
    min-height: 80px;
    margin-bottom: 8px;
  }
  .note-form textarea:focus { outline: none; border-color: var(--gold); }
  .note-submit {
    background: var(--gold);
    color: var(--dark);
    border: none;
    padding: 8px 20px;
    border-radius: 6px;
    font-size: 13px;
    font-weight: 700;
    cursor: pointer;
  }
  .note-submit:hover { background: #00b85a; }

  .flash {
    padding: 12px 16px;
    border-radius: 6px;
    font-size: 14px;
    margin-bottom: 16px;
  }
  .flash-success { background: #d4edda; color: #155724; }
  .flash-error { background: #f8d7da; color: #721c24; }
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>Green Shield Pest Control</h1>
    <p>Lead Detail</p>
  </div>
  <nav>
    <a href="{{ url_for('dashboard') }}">Dashboard</a>
    {% if current_user.is_admin %}
    <a href="{{ url_for('admin_users') }}">Users</a>
    <a href="{{ url_for('admin_costs') }}">Costs</a>
    {% endif %}
    <a href="{{ url_for('logout') }}">Logout</a>
  </nav>
</div>
<div class="container">
  {% with messages = get_flashed_messages(with_categories=true) %}
    {% for category, message in messages %}
      <div class="flash flash-{{ category }}">{{ message }}</div>
    {% endfor %}
  {% endwith %}

  <a href="{{ url_for('dashboard') }}" class="back-link">&larr; Back to Dashboard</a>

  <div class="lead-header">
    <h2>{{ lead.caller_name }}</h2>
    <span class="lead-score {% if lead.score >= 7 %}lead-score-high{% elif lead.score >= 4 %}lead-score-mid{% else %}lead-score-low{% endif %}">
      {{ lead.score }}/10
    </span>
    <span class="badge badge-{{ lead.routing }}">{{ lead.routing | upper }}</span>
  </div>

  <div class="info-grid">
    <div class="info-item">
      <label>Pest Type</label>
      <span>{{ lead.case_type or '—' }}</span>
    </div>
    <div class="info-item">
      <label>Phone</label>
      <span>{{ lead.phone or '—' }}</span>
    </div>
    <div class="info-item">
      <label>Email</label>
      <span>{{ lead.email or '—' }}</span>
    </div>
    <div class="info-item">
      <label>Zip Code</label>
      <span>{{ lead.zip_code or '—' }}</span>
    </div>
    <div class="info-item">
      <label>Date</label>
      <span>{{ lead.created_at[:16] }}</span>
    </div>
    <div class="info-item">
      <label>Call Duration</label>
      <span>{% if lead.call_duration_seconds %}{{ lead.call_duration_seconds }}s{% else %}—{% endif %}</span>
    </div>
  </div>

  {% if lead.case_summary %}
  <div class="section">
    <h3>Problem Description</h3>
    <p>{{ lead.case_summary }}</p>
  </div>
  {% endif %}

  {% if current_user.can_change_status %}
  <div class="section">
    <h3>Status</h3>
    <div class="status-buttons">
      {% for s in ['new', 'contacted', 'inspection_booked', 'scheduled', 'closed'] %}
      <form method="POST" action="{{ url_for('lead_status', lead_id=lead.id) }}" style="display:inline;">
        <input type="hidden" name="status" value="{{ s }}">
        <button type="submit" class="status-btn {% if lead.status == s %}active{% endif %}">
          {{ s | replace('_', ' ') }}
        </button>
      </form>
      {% endfor %}
    </div>
  </div>
  {% endif %}

  <div class="section">
    <h3>Notes</h3>
    {% if notes %}
      {% for note in notes %}
      <div class="note">
        <div class="note-meta"><strong>{{ note.user_name }}</strong> &mdash; {{ note.created_at[:16] }}</div>
        <div class="note-content">{{ note.content }}</div>
      </div>
      {% endfor %}
    {% else %}
      <p style="color: #999;">No notes yet.</p>
    {% endif %}

    <div class="note-form">
      <form method="POST" action="{{ url_for('lead_add_note', lead_id=lead.id) }}">
        <textarea name="content" placeholder="Add a note..." required></textarea>
        <button type="submit" class="note-submit">Add Note</button>
      </form>
    </div>
  </div>
</div>
</body>
</html>
"""


@app.route("/lead/<int:lead_id>")
@login_required
def lead_detail(lead_id):
    db = get_db()
    lead = db.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not lead:
        return "Lead not found", 404

    notes = db.execute(
        """SELECT lead_notes.*, users.name as user_name
           FROM lead_notes JOIN users ON lead_notes.user_id = users.id
           WHERE lead_notes.lead_id = ?
           ORDER BY lead_notes.created_at ASC""",
        (lead_id,),
    ).fetchall()

    return render_template_string(LEAD_DETAIL_HTML, lead=lead, notes=notes)


@app.route("/lead/<int:lead_id>/status", methods=["POST"])
@role_required("admin", "attorney")
def lead_status(lead_id):
    new_status = request.form.get("status", "")
    valid_statuses = ("new", "contacted", "inspection_booked", "scheduled", "closed")
    if new_status not in valid_statuses:
        flash("Invalid status.", "error")
        return redirect(url_for("lead_detail", lead_id=lead_id))

    db = get_db()
    db.execute("UPDATE leads SET status = ? WHERE id = ?", (new_status, lead_id))
    db.commit()
    flash(f"Status updated to {new_status.replace('_', ' ')}.", "success")
    return redirect(url_for("lead_detail", lead_id=lead_id))


@app.route("/lead/<int:lead_id>/note", methods=["POST"])
@login_required
def lead_add_note(lead_id):
    content = request.form.get("content", "").strip()
    if not content:
        flash("Note cannot be empty.", "error")
        return redirect(url_for("lead_detail", lead_id=lead_id))

    db = get_db()
    lead = db.execute("SELECT id FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not lead:
        return "Lead not found", 404

    db.execute(
        "INSERT INTO lead_notes (lead_id, user_id, content, created_at) VALUES (?, ?, ?, ?)",
        (lead_id, current_user.id, content, datetime.now().isoformat()),
    )
    db.commit()
    flash("Note added.", "success")
    return redirect(url_for("lead_detail", lead_id=lead_id))


@app.route("/")
@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()

    q = request.args.get("q", "").strip()
    routing = request.args.get("routing", "").strip()
    status_filter = request.args.get("status", "").strip()

    query = "SELECT * FROM leads WHERE 1=1"
    params = []

    if q:
        query += " AND (caller_name LIKE ? OR phone LIKE ? OR email LIKE ?)"
        like_q = f"%{q}%"
        params.extend([like_q, like_q, like_q])
    if routing:
        query += " AND routing = ?"
        params.append(routing)
    if status_filter:
        query += " AND status = ?"
        params.append(status_filter)

    query += " ORDER BY created_at DESC"
    leads = db.execute(query, params).fetchall()

    # Stats are always for all leads (unfiltered)
    all_leads = db.execute("SELECT routing FROM leads").fetchall()
    total = len(all_leads)
    qualified = sum(1 for l in all_leads if l["routing"] == "qualified")
    nurture_count = sum(1 for l in all_leads if l["routing"] == "nurture")
    redirect_count = sum(1 for l in all_leads if l["routing"] == "redirect")

    return render_template_string(
        DASHBOARD_HTML,
        leads=leads,
        leads_json=json.dumps([dict(l) for l in leads]),
        total=total,
        qualified=qualified,
        nurture=nurture_count,
        redirect=redirect_count,
        q=q,
        routing=routing,
        status_filter=status_filter,
    )


# ─── API ───────────────────────────────────────────────────────────────────

@app.route("/api/leads")
@login_required
def api_leads():
    db = get_db()
    leads = db.execute("SELECT * FROM leads ORDER BY created_at DESC").fetchall()
    return jsonify([dict(l) for l in leads])


# ─── CLI ───────────────────────────────────────────────────────────────────

def create_admin_user():
    email = input("Admin email: ").strip()
    name = input("Admin name: ").strip()
    password = input("Admin password: ").strip()
    if not email or not name or not password:
        print("All fields required.")
        return
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    existing = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if existing:
        print(f"User {email} already exists.")
        db.close()
        return
    db.execute(
        "INSERT INTO users (email, name, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?)",
        (email, name, generate_password_hash(password, method="pbkdf2:sha256"), "admin", datetime.now().isoformat()),
    )
    db.commit()
    db.close()
    print(f"Admin user '{name}' ({email}) created.")


# ─── Main ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    if len(sys.argv) > 1 and sys.argv[1] == "create-admin":
        create_admin_user()
    else:
        print("=" * 60)
        print("  Green Shield Pest Control — AI Receptionist Webhook Server")
        print("  Dashboard: http://localhost:5002")
        print("  Webhook:   http://localhost:5002/webhook/tools")
        print("=" * 60)
        app.run(debug=True, port=5002)
