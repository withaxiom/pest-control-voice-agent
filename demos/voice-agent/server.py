"""
Westbrook & Associates — Voice Agent Webhook Server
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
                "times": ["10:00 AM", "2:00 PM"],
                "attorney": "Attorney Reynolds" if count == 0 else "Attorney Kim",
            })
            count += 1
        day += timedelta(days=1)

    return json.dumps({
        "available_slots": slots,
        "note": "Consultations are 30 minutes. Free for first-time clients.",
    })


def handle_send_nurture_email(args):
    name = args.get("name", "there")
    email = args.get("email")
    case_type = args.get("case_type", "your legal matter")

    if not email:
        return "Error: no email address provided."

    html = f"""
    <div style="font-family: Georgia, serif; max-width: 600px; margin: 0 auto; color: #2D1B2E;">
        <div style="background: #1a1a2e; padding: 24px; text-align: center;">
            <h1 style="color: #D4AF37; margin: 0; font-size: 24px;">Westbrook & Associates</h1>
            <p style="color: rgba(255,255,255,0.6); margin: 4px 0 0;">Attorneys at Law</p>
        </div>

        <div style="padding: 32px 24px;">
            <p>Dear {name},</p>

            <p>Thank you for reaching out to us regarding {case_type}. We understand this can be
            a stressful time, and we appreciate your trust in contacting our firm.</p>

            <p>As promised, here are some resources that may be helpful:</p>

            <ul style="line-height: 1.8;">
                <li><strong>Know Your Rights</strong> — A guide to understanding your legal options</li>
                <li><strong>What to Expect</strong> — How the legal process works, step by step</li>
                <li><strong>Document Checklist</strong> — What to gather before your consultation</li>
            </ul>

            <p>When you're ready, we'd love to schedule a free 30-minute consultation to discuss
            your situation in detail.</p>

            <div style="text-align: center; margin: 32px 0;">
                <a href="https://westbrookassociates.com/book"
                   style="background: #D4AF37; color: #1a1a2e; padding: 14px 32px;
                          text-decoration: none; font-weight: bold; border-radius: 4px;">
                    Book a Free Consultation
                </a>
            </div>

            <p>Warm regards,<br>
            <strong>The Westbrook & Associates Team</strong><br>
            <span style="color: #888;">Eagle Pass, Texas</span></p>
        </div>

        <div style="background: #f5f5f5; padding: 16px; text-align: center; font-size: 12px; color: #888;">
            Westbrook & Associates | Eagle Pass, TX<br>
            This email is not legal advice.
        </div>
    </div>
    """

    try:
        resend.Emails.send({
            "from": "Westbrook & Associates <onboarding@resend.dev>",
            "to": [email],
            "subject": f"Resources from Westbrook & Associates — {case_type.title()}",
            "html": html,
        })
        return f"Nurture email sent to {email} successfully."
    except Exception as e:
        return f"Email failed: {str(e)}"


def handle_transfer_call(args):
    return json.dumps({
        "destination": {
            "type": "number",
            "number": "+18305555555",
            "message": "Transferring you to our reception desk now.",
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
    print(f"\n=== TOOL CALL PAYLOAD ===\n{json.dumps(data, indent=2)}\n========================\n")
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
<title>Sign In — Westbrook & Associates</title>
<style>
  :root {
    --dark: #1a1a2e;
    --gold: #D4AF37;
    --bg: #f8f9fa;
    --text: #2D1B2E;
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
  .google-btn:hover { background: #2a2a4e; }
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
  .submit-btn:hover { background: #c9a230; }
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
  <div class="brand"><span>Westbrook & Associates</span></div>
  <h1>Sign In</h1>
  <p class="subtitle">Lead Qualification Dashboard</p>

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
    <input type="email" id="email" name="email" required placeholder="you@westbrooklaw.com">

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
<title>Westbrook & Associates — Lead Dashboard</title>
<style>
  :root {
    --dark: #1a1a2e;
    --gold: #D4AF37;
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
  .stat { text-align: center; }
  .stat-value { color: white; font-size: 24px; font-weight: 700; }
  .stat-label { color: rgba(255,255,255,0.5); font-size: 11px; text-transform: uppercase; }

  .container { max-width: 1200px; margin: 0 auto; padding: 24px; }

  table { width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
  th { background: var(--dark); color: var(--gold); padding: 12px 16px; text-align: left; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
  td { padding: 14px 16px; border-bottom: 1px solid #eee; font-size: 14px; }
  tr:hover { background: #fafafa; }

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
    <h1>Westbrook & Associates</h1>
    <p>Lead Qualification Dashboard</p>
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
  <button class="refresh" onclick="location.reload()">Refresh</button>
</div>
<div class="container">
  {% if leads %}
  <table>
    <thead>
      <tr>
        <th>Time</th>
        <th>Name</th>
        <th>Case Type</th>
        <th>Score</th>
        <th>Routing</th>
        <th>Phone</th>
        <th>Email</th>
        <th>Zip</th>
      </tr>
    </thead>
    <tbody>
      {% for lead in leads %}
      <tr>
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
  // Auto-refresh every 10 seconds
  setTimeout(() => location.reload(), 10000);
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
<title>User Management — Westbrook & Associates</title>
<style>
  :root {
    --dark: #1a1a2e;
    --gold: #D4AF37;
    --green: #27ae60;
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
  .btn-invite:hover { background: #c9a230; }
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>Westbrook & Associates</h1>
    <p>User Management</p>
  </div>
  <nav>
    <a href="{{ url_for('dashboard') }}">Dashboard</a>
    <a href="#">Costs</a>
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


@app.route("/")
@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    leads = db.execute("SELECT * FROM leads ORDER BY created_at DESC").fetchall()

    total = len(leads)
    qualified = sum(1 for l in leads if l["routing"] == "qualified")
    nurture_count = sum(1 for l in leads if l["routing"] == "nurture")
    redirect_count = sum(1 for l in leads if l["routing"] == "redirect")

    return render_template_string(
        DASHBOARD_HTML,
        leads=leads,
        total=total,
        qualified=qualified,
        nurture=nurture_count,
        redirect=redirect_count,
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
        print("  Westbrook & Associates — Voice Agent Webhook Server")
        print("  Dashboard: http://localhost:5002")
        print("  Webhook:   http://localhost:5002/webhook/tools")
        print("=" * 60)
        app.run(debug=True, port=5002)
