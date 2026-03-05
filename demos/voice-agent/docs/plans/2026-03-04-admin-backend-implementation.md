# Admin Backend Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an authenticated admin backend with role-based access, lead management, notifications, and cost tracking to the Westbrook & Associates voice agent.

**Architecture:** Extend the existing Flask + Jinja app with Flask-Login for sessions, Authlib for Google OAuth, and new SQLite tables for users and notes. All pages server-rendered. No separate frontend.

**Tech Stack:** Flask, Flask-Login, Authlib, Werkzeug (password hashing), SQLite, Resend, Jinja2

---

### Task 1: Add Dependencies

**Files:**
- Modify: `demos/voice-agent/requirements.txt`

**Step 1: Update requirements.txt**

```
flask==3.1.0
python-dotenv==1.0.1
resend==2.5.1
requests==2.32.3
flask-login==0.6.3
authlib==1.4.1
```

**Step 2: Update .env.example with Google OAuth vars**

Add to `demos/voice-agent/.env.example`:

```
VAPI_API_KEY=your_vapi_api_key
RESEND_API_KEY=your_resend_api_key
WEBHOOK_SECRET=your_webhook_secret
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
SECRET_KEY=your_flask_secret_key
NOTIFICATION_EMAIL=optional_email_for_hot_lead_alerts
```

**Step 3: Install new dependencies**

Run:
```bash
cd demos/voice-agent && source .venv/bin/activate && pip install -r requirements.txt
```

**Step 4: Commit**

```bash
git add requirements.txt .env.example
git commit -m "feat: add Flask-Login and Authlib dependencies"
```

---

### Task 2: Database Schema Changes

**Files:**
- Modify: `demos/voice-agent/server.py` (init_db function)

**Step 1: Update init_db to create users and lead_notes tables, alter leads table**

Replace the `init_db()` function in `server.py`:

```python
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
    # Add status and call_duration_seconds columns if missing (migration for existing DBs)
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
```

**Step 2: Verify by deleting old DB and restarting**

Run:
```bash
rm -f leads.db && python -c "import server; server.init_db(); print('DB initialized')"
```
Expected: "DB initialized", `leads.db` created with all 3 tables.

**Step 3: Verify tables exist**

Run:
```bash
python -c "
import sqlite3
db = sqlite3.connect('leads.db')
tables = db.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()
print([t[0] for t in tables])
"
```
Expected: `['leads', 'users', 'lead_notes']`

**Step 4: Commit**

```bash
git add server.py
git commit -m "feat: add users and lead_notes tables, add status/duration to leads"
```

---

### Task 3: User Model and Flask-Login Setup

**Files:**
- Modify: `demos/voice-agent/server.py`

**Step 1: Add imports and Flask-Login setup at the top of server.py**

After the existing imports, add:

```python
import secrets
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
```

After `app = Flask(__name__)`, add:

```python
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
```

**Step 2: Add User class and login manager loader**

Add after the database section:

```python
# ─── User Model ────────────────────────────────────────────────────────────

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
            id=row["id"],
            email=row["email"],
            name=row["name"],
            password_hash=row["password_hash"],
            google_id=row["google_id"],
            role=row["role"],
            created_at=row["created_at"],
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
```

**Step 3: Add role_required decorator**

Add after the User class:

```python
from functools import wraps

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
```

**Step 4: Add create-admin CLI command**

Add before `if __name__ == "__main__":`:

```python
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
        (email, name, generate_password_hash(password), "admin", datetime.now().isoformat()),
    )
    db.commit()
    db.close()
    print(f"Admin user '{name}' ({email}) created.")
```

Update `if __name__ == "__main__":`:

```python
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
```

Also add `import sys` at the top if not already present.

**Step 5: Verify create-admin works**

Run:
```bash
echo -e "admin@westbrook.com\nAdmin User\nadmin123" | python server.py create-admin
```
Expected: "Admin user 'Admin User' (admin@westbrook.com) created."

**Step 6: Commit**

```bash
git add server.py
git commit -m "feat: add User model, Flask-Login, role decorator, create-admin CLI"
```

---

### Task 4: Login Page and Auth Routes

**Files:**
- Modify: `demos/voice-agent/server.py`

**Step 1: Add login page HTML template**

Add as a constant (like DASHBOARD_HTML):

```python
LOGIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Westbrook & Associates — Login</title>
<style>
  :root { --dark: #1a1a2e; --gold: #D4AF37; --bg: #f8f9fa; --text: #2D1B2E; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); display: flex; min-height: 100vh; align-items: center; justify-content: center; }
  .login-card {
    background: white; padding: 48px 40px; border-radius: 12px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.1); max-width: 420px; width: 100%;
  }
  .logo { text-align: center; margin-bottom: 32px; }
  .logo h1 { color: var(--dark); font-size: 22px; }
  .logo p { color: #888; font-size: 13px; margin-top: 4px; }
  .divider { display: flex; align-items: center; gap: 12px; margin: 24px 0; color: #aaa; font-size: 13px; }
  .divider::before, .divider::after { content: ''; flex: 1; height: 1px; background: #ddd; }
  label { display: block; font-size: 13px; font-weight: 600; margin-bottom: 6px; color: #555; }
  input[type="email"], input[type="password"] {
    width: 100%; padding: 12px 14px; border: 1px solid #ddd; border-radius: 6px;
    font-size: 14px; margin-bottom: 16px; outline: none;
  }
  input:focus { border-color: var(--gold); box-shadow: 0 0 0 3px rgba(212,175,55,0.15); }
  .btn {
    width: 100%; padding: 12px; border: none; border-radius: 6px;
    font-size: 14px; font-weight: 600; cursor: pointer; text-align: center; text-decoration: none; display: block;
  }
  .btn-google { background: white; color: #333; border: 1px solid #ddd; margin-bottom: 12px; }
  .btn-google:hover { background: #f5f5f5; }
  .btn-login { background: var(--dark); color: var(--gold); }
  .btn-login:hover { opacity: 0.9; }
  .error { background: #f8d7da; color: #721c24; padding: 10px 14px; border-radius: 6px; font-size: 13px; margin-bottom: 16px; }
  .flash { background: #d4edda; color: #155724; padding: 10px 14px; border-radius: 6px; font-size: 13px; margin-bottom: 16px; }
</style>
</head>
<body>
<div class="login-card">
  <div class="logo">
    <h1>Westbrook & Associates</h1>
    <p>Lead Management Portal</p>
  </div>

  {% if error %}
  <div class="error">{{ error }}</div>
  {% endif %}

  {% if message %}
  <div class="flash">{{ message }}</div>
  {% endif %}

  <a href="{{ url_for('google_login') }}" class="btn btn-google">Sign in with Google</a>

  <div class="divider">or</div>

  <form method="POST" action="{{ url_for('login') }}">
    <label>Email</label>
    <input type="email" name="email" required placeholder="you@westbrook.com">
    <label>Password</label>
    <input type="password" name="password" required placeholder="Enter your password">
    <button type="submit" class="btn btn-login">Sign In</button>
  </form>
</div>
</body>
</html>
"""
```

**Step 2: Add login/logout routes**

```python
@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    error = None
    message = request.args.get("message")

    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        user = User.get_by_email(email)

        if user and user.password_hash and check_password_hash(user.password_hash, password):
            login_user(user)
            next_page = request.args.get("next")
            return redirect(next_page or url_for("dashboard"))
        else:
            error = "Invalid email or password."

    return render_template_string(LOGIN_HTML, error=error, message=message)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login", message="You have been logged out."))
```

Also add `redirect` to the Flask imports:

```python
from flask import Flask, request, jsonify, render_template_string, g, redirect, url_for
```

**Step 3: Protect the dashboard and API routes**

Add `@login_required` to the `dashboard()` and `api_leads()` functions:

```python
@app.route("/")
@app.route("/dashboard")
@login_required
def dashboard():
    # ... existing code ...
```

```python
@app.route("/api/leads")
@login_required
def api_leads():
    # ... existing code ...
```

**NOTE:** Do NOT add `@login_required` to the webhook routes (`/webhook/tools` and `/webhook/vapi`) — those are called by Vapi, not users.

**Step 4: Verify login page renders**

Run:
```bash
rm -f leads.db && python -c "
import server
server.init_db()
with server.app.test_client() as c:
    resp = c.get('/')
    print(f'Dashboard redirects to login: {resp.status_code} {resp.location}')
    resp = c.get('/login')
    print(f'Login page: {resp.status_code}')
    assert resp.status_code == 200
    assert b'Sign in with Google' in resp.data
    print('Login page renders correctly')
"
```

**Step 5: Commit**

```bash
git add server.py
git commit -m "feat: add login page with email/password auth and route protection"
```

---

### Task 5: Google OAuth

**Files:**
- Modify: `demos/voice-agent/server.py`

**Step 1: Add Authlib setup**

After the Flask-Login setup, add:

```python
from authlib.integrations.flask_client import OAuth

oauth = OAuth(app)
google = oauth.register(
    name="google",
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)
```

**Step 2: Add Google OAuth routes**

```python
@app.route("/login/google")
def google_login():
    redirect_uri = url_for("google_callback", _external=True)
    return google.authorize_redirect(redirect_uri)


@app.route("/login/google/callback")
def google_callback():
    token = google.authorize_access_token()
    userinfo = token.get("userinfo")
    if not userinfo:
        return redirect(url_for("login", error="Google login failed."))

    google_id = userinfo["sub"]
    email = userinfo["email"]
    name = userinfo.get("name", email)

    # Check if user exists by google_id or email
    user = User.get_by_google_id(google_id)
    if not user:
        user = User.get_by_email(email)

    if not user:
        # User not invited — reject
        return redirect(url_for("login") + "?message=No+account+found.+Ask+your+admin+for+an+invite.")

    # Link google_id if not already linked
    if not user.google_id:
        db = sqlite3.connect(DB_PATH)
        db.execute("UPDATE users SET google_id = ? WHERE id = ?", (google_id, user.id))
        db.commit()
        db.close()
        user.google_id = google_id

    login_user(user)
    return redirect(url_for("dashboard"))
```

**Step 3: Update .env with placeholder Google credentials**

Add to `.env`:
```
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
SECRET_KEY=change-me-to-a-random-string
```

**Step 4: Commit**

```bash
git add server.py
git commit -m "feat: add Google OAuth login via Authlib"
```

---

### Task 6: Admin User Management Page

**Files:**
- Modify: `demos/voice-agent/server.py`

**Step 1: Add admin users page HTML template**

```python
ADMIN_USERS_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Westbrook & Associates — User Management</title>
<style>
  :root { --dark: #1a1a2e; --gold: #D4AF37; --bg: #f8f9fa; --text: #2D1B2E; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); }
  .header { background: var(--dark); padding: 20px 32px; display: flex; align-items: center; justify-content: space-between; }
  .header h1 { color: var(--gold); font-size: 20px; }
  .header-right { display: flex; gap: 16px; align-items: center; }
  .header a { color: rgba(255,255,255,0.7); text-decoration: none; font-size: 13px; }
  .header a:hover { color: white; }
  .container { max-width: 900px; margin: 0 auto; padding: 24px; }
  h2 { margin-bottom: 16px; }
  table { width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.08); margin-bottom: 32px; }
  th { background: var(--dark); color: var(--gold); padding: 12px 16px; text-align: left; font-size: 12px; text-transform: uppercase; }
  td { padding: 14px 16px; border-bottom: 1px solid #eee; font-size: 14px; }
  .badge { display: inline-block; padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; }
  .badge-admin { background: #e8daef; color: #6c3483; }
  .badge-attorney { background: #d4edda; color: #155724; }
  .badge-staff { background: #d6eaf8; color: #1a5276; }
  .invite-form { background: white; padding: 24px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
  .invite-form h3 { margin-bottom: 16px; }
  .form-row { display: flex; gap: 12px; margin-bottom: 12px; flex-wrap: wrap; }
  .form-row input, .form-row select { padding: 10px 12px; border: 1px solid #ddd; border-radius: 6px; font-size: 14px; }
  .form-row input { flex: 1; min-width: 150px; }
  .btn { padding: 10px 20px; border: none; border-radius: 6px; font-size: 14px; font-weight: 600; cursor: pointer; }
  .btn-invite { background: var(--dark); color: var(--gold); }
  .btn-small { padding: 6px 12px; font-size: 12px; border: 1px solid #ddd; background: white; border-radius: 4px; cursor: pointer; }
  .btn-small:hover { background: #f5f5f5; }
  .btn-danger { color: #dc3545; border-color: #dc3545; }
  .flash { padding: 10px 14px; border-radius: 6px; font-size: 13px; margin-bottom: 16px; }
  .flash-success { background: #d4edda; color: #155724; }
  .flash-error { background: #f8d7da; color: #721c24; }
</style>
</head>
<body>
<div class="header">
  <div>
    <h1>Westbrook & Associates</h1>
  </div>
  <div class="header-right">
    <a href="{{ url_for('dashboard') }}">Dashboard</a>
    <a href="{{ url_for('admin_costs') }}">Costs</a>
    <a href="{{ url_for('logout') }}">Logout ({{ current_user.name }})</a>
  </div>
</div>
<div class="container">
  {% if flash_message %}
  <div class="flash {{ flash_class }}">{{ flash_message }}</div>
  {% endif %}

  <h2>Team Members</h2>
  <table>
    <thead>
      <tr><th>Name</th><th>Email</th><th>Role</th><th>Joined</th><th>Actions</th></tr>
    </thead>
    <tbody>
      {% for user in users %}
      <tr>
        <td><strong>{{ user.name }}</strong></td>
        <td>{{ user.email }}</td>
        <td><span class="badge badge-{{ user.role }}">{{ user.role | upper }}</span></td>
        <td>{{ user.created_at[:10] }}</td>
        <td>
          {% if user.id != current_user.id %}
          <form method="POST" action="{{ url_for('admin_change_role') }}" style="display:inline;">
            <input type="hidden" name="user_id" value="{{ user.id }}">
            <select name="role" onchange="this.form.submit()">
              <option value="admin" {% if user.role == 'admin' %}selected{% endif %}>Admin</option>
              <option value="attorney" {% if user.role == 'attorney' %}selected{% endif %}>Attorney</option>
              <option value="staff" {% if user.role == 'staff' %}selected{% endif %}>Staff</option>
            </select>
          </form>
          <form method="POST" action="{{ url_for('admin_delete_user') }}" style="display:inline;" onsubmit="return confirm('Remove this user?')">
            <input type="hidden" name="user_id" value="{{ user.id }}">
            <button type="submit" class="btn-small btn-danger">Remove</button>
          </form>
          {% else %}
          <span style="color:#999;">You</span>
          {% endif %}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>

  <div class="invite-form">
    <h3>Invite New User</h3>
    <form method="POST" action="{{ url_for('admin_invite_user') }}">
      <div class="form-row">
        <input type="text" name="name" placeholder="Full name" required>
        <input type="email" name="email" placeholder="Email address" required>
        <select name="role">
          <option value="staff">Staff</option>
          <option value="attorney">Attorney</option>
          <option value="admin">Admin</option>
        </select>
        <input type="password" name="password" placeholder="Temporary password" required>
        <button type="submit" class="btn btn-invite">Invite</button>
      </div>
    </form>
  </div>
</div>
</body>
</html>
"""
```

**Step 2: Add admin routes**

```python
@app.route("/admin/users")
@role_required("admin")
def admin_users():
    db = get_db()
    users = db.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    flash_message = request.args.get("flash_message")
    flash_class = request.args.get("flash_class", "flash-success")
    return render_template_string(ADMIN_USERS_HTML, users=users, flash_message=flash_message, flash_class=flash_class)


@app.route("/admin/users/invite", methods=["POST"])
@role_required("admin")
def admin_invite_user():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    role = request.form.get("role", "staff")
    password = request.form.get("password", "")

    if not name or not email or not password:
        return redirect(url_for("admin_users", flash_message="All fields required.", flash_class="flash-error"))

    if role not in ("admin", "attorney", "staff"):
        role = "staff"

    db = get_db()
    existing = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if existing:
        return redirect(url_for("admin_users", flash_message=f"{email} already exists.", flash_class="flash-error"))

    db.execute(
        "INSERT INTO users (email, name, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?)",
        (email, name, generate_password_hash(password), role, datetime.now().isoformat()),
    )
    db.commit()
    return redirect(url_for("admin_users", flash_message=f"Invited {name} ({email}) as {role}."))


@app.route("/admin/users/role", methods=["POST"])
@role_required("admin")
def admin_change_role():
    user_id = request.form.get("user_id", type=int)
    new_role = request.form.get("role")
    if new_role not in ("admin", "attorney", "staff"):
        return redirect(url_for("admin_users", flash_message="Invalid role.", flash_class="flash-error"))

    db = get_db()
    # Prevent removing last admin
    if new_role != "admin":
        admin_count = db.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0]
        target_role = db.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
        if target_role and target_role["role"] == "admin" and admin_count <= 1:
            return redirect(url_for("admin_users", flash_message="Cannot demote the last admin.", flash_class="flash-error"))

    db.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, user_id))
    db.commit()
    return redirect(url_for("admin_users", flash_message="Role updated."))


@app.route("/admin/users/delete", methods=["POST"])
@role_required("admin")
def admin_delete_user():
    user_id = request.form.get("user_id", type=int)
    if user_id == current_user.id:
        return redirect(url_for("admin_users", flash_message="Cannot remove yourself.", flash_class="flash-error"))

    db = get_db()
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    return redirect(url_for("admin_users", flash_message="User removed."))
```

**Step 3: Verify admin page**

Run:
```bash
python -c "
import server
server.init_db()
with server.app.test_client() as c:
    # Login as admin first
    resp = c.get('/admin/users')
    print(f'Redirect to login (no auth): {resp.status_code}')
"
```
Expected: 302 redirect to login.

**Step 4: Commit**

```bash
git add server.py
git commit -m "feat: add admin user management — invite, role changes, removal"
```

---

### Task 7: Lead Detail View with Notes and Status

**Files:**
- Modify: `demos/voice-agent/server.py`

**Step 1: Add lead detail HTML template**

```python
LEAD_DETAIL_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Lead — {{ lead.caller_name }} | Westbrook & Associates</title>
<style>
  :root { --dark: #1a1a2e; --gold: #D4AF37; --green: #27ae60; --yellow: #f39c12; --red: #e74c3c; --bg: #f8f9fa; --text: #2D1B2E; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); }
  .header { background: var(--dark); padding: 20px 32px; display: flex; align-items: center; justify-content: space-between; }
  .header h1 { color: var(--gold); font-size: 20px; }
  .header-right { display: flex; gap: 16px; align-items: center; }
  .header a { color: rgba(255,255,255,0.7); text-decoration: none; font-size: 13px; }
  .header a:hover { color: white; }
  .container { max-width: 900px; margin: 0 auto; padding: 24px; }
  .back { color: #888; text-decoration: none; font-size: 13px; margin-bottom: 16px; display: inline-block; }
  .back:hover { color: var(--text); }
  .lead-header { display: flex; align-items: center; gap: 16px; margin-bottom: 24px; }
  .lead-header h2 { font-size: 24px; }
  .score { font-weight: 700; font-size: 20px; }
  .score-high { color: var(--green); }
  .score-mid { color: var(--yellow); }
  .score-low { color: var(--red); }
  .badge { display: inline-block; padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; }
  .badge-qualified { background: #d4edda; color: #155724; }
  .badge-nurture { background: #fff3cd; color: #856404; }
  .badge-redirect { background: #f8d7da; color: #721c24; }
  .info-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 32px; }
  .info-card { background: white; padding: 16px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
  .info-card label { font-size: 11px; text-transform: uppercase; color: #888; letter-spacing: 0.5px; }
  .info-card p { font-size: 15px; margin-top: 4px; }
  .section { margin-bottom: 32px; }
  .section h3 { margin-bottom: 12px; }
  .status-form { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 24px; }
  .status-btn { padding: 8px 16px; border: 2px solid #ddd; border-radius: 6px; background: white; cursor: pointer; font-size: 13px; font-weight: 600; }
  .status-btn:hover { border-color: var(--gold); }
  .status-btn.active { border-color: var(--dark); background: var(--dark); color: var(--gold); }
  .notes { background: white; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); overflow: hidden; }
  .note { padding: 16px; border-bottom: 1px solid #eee; }
  .note:last-child { border-bottom: none; }
  .note-meta { font-size: 12px; color: #888; margin-bottom: 6px; }
  .note-content { font-size: 14px; line-height: 1.5; }
  .note-form { padding: 16px; border-top: 2px solid #eee; }
  .note-form textarea { width: 100%; padding: 12px; border: 1px solid #ddd; border-radius: 6px; font-size: 14px; resize: vertical; min-height: 80px; font-family: inherit; }
  .note-form textarea:focus { border-color: var(--gold); outline: none; }
  .btn { padding: 10px 20px; border: none; border-radius: 6px; font-size: 14px; font-weight: 600; cursor: pointer; }
  .btn-primary { background: var(--dark); color: var(--gold); margin-top: 8px; }
  .summary { background: white; padding: 16px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); font-size: 14px; line-height: 1.6; }
</style>
</head>
<body>
<div class="header">
  <h1>Westbrook & Associates</h1>
  <div class="header-right">
    <a href="{{ url_for('dashboard') }}">Dashboard</a>
    {% if current_user.is_admin %}
    <a href="{{ url_for('admin_users') }}">Users</a>
    <a href="{{ url_for('admin_costs') }}">Costs</a>
    {% endif %}
    <a href="{{ url_for('logout') }}">Logout ({{ current_user.name }})</a>
  </div>
</div>
<div class="container">
  <a href="{{ url_for('dashboard') }}" class="back">&larr; Back to Dashboard</a>

  <div class="lead-header">
    <h2>{{ lead.caller_name }}</h2>
    <span class="score {% if lead.score >= 7 %}score-high{% elif lead.score >= 4 %}score-mid{% else %}score-low{% endif %}">
      {{ lead.score }}/10
    </span>
    <span class="badge badge-{{ lead.routing }}">{{ lead.routing | upper }}</span>
  </div>

  <div class="info-grid">
    <div class="info-card"><label>Case Type</label><p>{{ lead.case_type or '—' }}</p></div>
    <div class="info-card"><label>Phone</label><p>{{ lead.phone or '—' }}</p></div>
    <div class="info-card"><label>Email</label><p>{{ lead.email or '—' }}</p></div>
    <div class="info-card"><label>Zip Code</label><p>{{ lead.zip_code or '—' }}</p></div>
    <div class="info-card"><label>Date</label><p>{{ lead.created_at[:16] }}</p></div>
    <div class="info-card"><label>Call Duration</label><p>{% if lead.call_duration_seconds %}{{ (lead.call_duration_seconds // 60) }}m {{ lead.call_duration_seconds % 60 }}s{% else %}—{% endif %}</p></div>
  </div>

  {% if lead.case_summary %}
  <div class="section">
    <h3>Case Summary</h3>
    <div class="summary">{{ lead.case_summary }}</div>
  </div>
  {% endif %}

  {% if current_user.can_change_status %}
  <div class="section">
    <h3>Status</h3>
    <form method="POST" action="{{ url_for('update_lead_status', lead_id=lead.id) }}" class="status-form">
      {% for s in ['new', 'contacted', 'consultation_booked', 'retained', 'closed'] %}
      <button type="submit" name="status" value="{{ s }}" class="status-btn {% if lead.status == s %}active{% endif %}">
        {{ s.replace('_', ' ').title() }}
      </button>
      {% endfor %}
    </form>
  </div>
  {% else %}
  <div class="section">
    <h3>Status</h3>
    <p>{{ lead.status.replace('_', ' ').title() }}</p>
  </div>
  {% endif %}

  <div class="section">
    <h3>Notes ({{ notes | length }})</h3>
    <div class="notes">
      {% if notes %}
        {% for note in notes %}
        <div class="note">
          <div class="note-meta">{{ note.user_name }} — {{ note.created_at[:16] }}</div>
          <div class="note-content">{{ note.content }}</div>
        </div>
        {% endfor %}
      {% else %}
        <div class="note"><div class="note-content" style="color: #999;">No notes yet.</div></div>
      {% endif %}
      <div class="note-form">
        <form method="POST" action="{{ url_for('add_lead_note', lead_id=lead.id) }}">
          <textarea name="content" placeholder="Add a note..." required></textarea>
          <button type="submit" class="btn btn-primary">Add Note</button>
        </form>
      </div>
    </div>
  </div>
</div>
</body>
</html>
"""
```

**Step 2: Add lead detail, status update, and notes routes**

```python
@app.route("/lead/<int:lead_id>")
@login_required
def lead_detail(lead_id):
    db = get_db()
    lead = db.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not lead:
        return "Lead not found", 404

    notes = db.execute("""
        SELECT lead_notes.*, users.name as user_name
        FROM lead_notes
        JOIN users ON lead_notes.user_id = users.id
        WHERE lead_notes.lead_id = ?
        ORDER BY lead_notes.created_at DESC
    """, (lead_id,)).fetchall()

    return render_template_string(LEAD_DETAIL_HTML, lead=lead, notes=notes)


@app.route("/lead/<int:lead_id>/status", methods=["POST"])
@role_required("admin", "attorney")
def update_lead_status(lead_id):
    new_status = request.form.get("status")
    valid = ("new", "contacted", "consultation_booked", "retained", "closed")
    if new_status not in valid:
        return "Invalid status", 400

    db = get_db()
    db.execute("UPDATE leads SET status = ? WHERE id = ?", (new_status, lead_id))
    db.commit()
    return redirect(url_for("lead_detail", lead_id=lead_id))


@app.route("/lead/<int:lead_id>/note", methods=["POST"])
@login_required
def add_lead_note(lead_id):
    content = request.form.get("content", "").strip()
    if not content:
        return redirect(url_for("lead_detail", lead_id=lead_id))

    db = get_db()
    db.execute(
        "INSERT INTO lead_notes (lead_id, user_id, content, created_at) VALUES (?, ?, ?, ?)",
        (lead_id, current_user.id, content, datetime.now().isoformat()),
    )
    db.commit()
    return redirect(url_for("lead_detail", lead_id=lead_id))
```

**Step 3: Update dashboard table rows to be clickable**

In `DASHBOARD_HTML`, change each table row `<tr>` to link to the detail view:

Replace:
```html
      <tr>
```
inside the for loop with:
```html
      <tr onclick="location.href='/lead/{{ lead.id }}'" style="cursor: pointer;">
```

Also add a Status column to the dashboard table header and body:

Add to `<thead>` after Zip:
```html
        <th>Status</th>
```

Add to `<tbody>` row after Zip:
```html
        <td>{{ (lead.status or 'new').replace('_', ' ').title() }}</td>
```

**Step 4: Update dashboard header with nav links**

Replace the dashboard header's Refresh button section to include navigation:

Replace in DASHBOARD_HTML:
```html
  <button class="refresh" onclick="location.reload()">Refresh</button>
```
with:
```html
  <div style="display: flex; gap: 12px; align-items: center;">
    {% if current_user.is_admin %}
    <a href="{{ url_for('admin_users') }}" class="refresh" style="text-decoration:none;">Users</a>
    <a href="{{ url_for('admin_costs') }}" class="refresh" style="text-decoration:none;">Costs</a>
    {% endif %}
    <a href="{{ url_for('logout') }}" class="refresh" style="text-decoration:none;">Logout ({{ current_user.name }})</a>
    <button class="refresh" onclick="location.reload()">Refresh</button>
  </div>
```

**Step 5: Add filtering and search to dashboard**

Add a filter bar above the table in DASHBOARD_HTML, after `<div class="container">`:

```html
  <form method="GET" style="display: flex; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; align-items: center;">
    <input type="text" name="q" placeholder="Search name, phone, email..." value="{{ q or '' }}"
           style="padding: 10px 14px; border: 1px solid #ddd; border-radius: 6px; font-size: 14px; flex: 1; min-width: 200px;">
    <select name="routing" style="padding: 10px 12px; border: 1px solid #ddd; border-radius: 6px; font-size: 14px;">
      <option value="">All Routing</option>
      <option value="qualified" {% if routing == 'qualified' %}selected{% endif %}>Qualified</option>
      <option value="nurture" {% if routing == 'nurture' %}selected{% endif %}>Nurture</option>
      <option value="redirect" {% if routing == 'redirect' %}selected{% endif %}>Redirect</option>
    </select>
    <select name="status" style="padding: 10px 12px; border: 1px solid #ddd; border-radius: 6px; font-size: 14px;">
      <option value="">All Status</option>
      <option value="new" {% if status_filter == 'new' %}selected{% endif %}>New</option>
      <option value="contacted" {% if status_filter == 'contacted' %}selected{% endif %}>Contacted</option>
      <option value="consultation_booked" {% if status_filter == 'consultation_booked' %}selected{% endif %}>Consultation Booked</option>
      <option value="retained" {% if status_filter == 'retained' %}selected{% endif %}>Retained</option>
      <option value="closed" {% if status_filter == 'closed' %}selected{% endif %}>Closed</option>
    </select>
    <button type="submit" style="padding: 10px 20px; background: var(--dark); color: var(--gold); border: none; border-radius: 6px; font-weight: 600; cursor: pointer;">Filter</button>
    {% if q or routing or status_filter %}
    <a href="{{ url_for('dashboard') }}" style="color: #888; font-size: 13px;">Clear</a>
    {% endif %}
  </form>
```

Update the `dashboard()` route to handle filtering:

```python
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
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
    if routing:
        query += " AND routing = ?"
        params.append(routing)
    if status_filter:
        query += " AND status = ?"
        params.append(status_filter)

    query += " ORDER BY created_at DESC"
    leads = db.execute(query, params).fetchall()

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
        q=q,
        routing=routing,
        status_filter=status_filter,
    )
```

**Step 6: Commit**

```bash
git add server.py
git commit -m "feat: add lead detail view with notes, status tracking, filtering"
```

---

### Task 8: Notifications

**Files:**
- Modify: `demos/voice-agent/server.py`

**Step 1: Add notification banner to dashboard**

Add a JavaScript block to DASHBOARD_HTML before the closing `</body>`. Replace the existing auto-refresh `<script>` with:

```html
<script>
  // Track last seen lead ID
  const lastSeenKey = 'westbrook_last_seen_lead';
  const leads = {{ leads_json | safe }};

  if (leads.length > 0) {
    const latestId = leads[0].id;
    const lastSeen = parseInt(localStorage.getItem(lastSeenKey) || '0');

    const newQualified = leads.filter(l => l.id > lastSeen && l.routing === 'qualified');
    if (newQualified.length > 0 && lastSeen > 0) {
      const lead = newQualified[0];
      const banner = document.createElement('div');
      banner.style.cssText = 'position:fixed;top:0;left:0;right:0;background:#27ae60;color:white;padding:14px 32px;text-align:center;font-weight:600;z-index:1000;cursor:pointer;';
      banner.innerHTML = 'New qualified lead: ' + lead.caller_name + ' — ' + (lead.case_type || 'Unknown') + ' <span style="opacity:0.7;margin-left:12px;">(Click to view)</span>';
      banner.onclick = () => { location.href = '/lead/' + lead.id; };
      document.body.prepend(banner);
      setTimeout(() => banner.remove(), 15000);
    }

    localStorage.setItem(lastSeenKey, latestId.toString());
  }

  // Auto-refresh every 10 seconds
  setTimeout(() => location.reload(), 10000);
</script>
```

**Step 2: Pass leads as JSON to the template**

In the `dashboard()` route, add `leads_json` to the template context:

```python
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
```

**Step 3: Add email notification when qualified lead is logged**

In the `handle_log_lead` function, after `db.commit()`, add:

```python
    # Send email notification for qualified leads
    notification_email = os.environ.get("NOTIFICATION_EMAIL")
    if notification_email and args.get("routing") == "qualified":
        try:
            resend.Emails.send({
                "from": "Westbrook & Associates <onboarding@resend.dev>",
                "to": [notification_email],
                "subject": f"New Qualified Lead: {args.get('caller_name')} — Score {args.get('score')}/10",
                "html": f"""
                <div style="font-family: -apple-system, sans-serif; padding: 24px;">
                    <h2 style="color: #27ae60;">New Qualified Lead</h2>
                    <p><strong>Name:</strong> {args.get('caller_name')}</p>
                    <p><strong>Case Type:</strong> {args.get('case_type', 'Unknown')}</p>
                    <p><strong>Score:</strong> {args.get('score')}/10</p>
                    <p><strong>Phone:</strong> {caller_phone or 'Unknown'}</p>
                    <p><strong>Summary:</strong> {args.get('case_summary', 'N/A')}</p>
                    <p style="margin-top: 16px;">
                        <a href="http://localhost:5002/dashboard" style="background: #1a1a2e; color: #D4AF37; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: 600;">
                            View Dashboard
                        </a>
                    </p>
                </div>
                """,
            })
        except Exception as e:
            print(f"Notification email failed: {e}")
```

**Step 4: Commit**

```bash
git add server.py
git commit -m "feat: add browser banner and email notifications for qualified leads"
```

---

### Task 9: Cost Analysis Page

**Files:**
- Modify: `demos/voice-agent/server.py`

**Step 1: Store call duration from end-of-call webhook**

Update the `webhook_vapi` route's `end-of-call-report` handler:

```python
    if event_type == "end-of-call-report":
        call_id = message.get("call", {}).get("id")
        duration = message.get("durationSeconds")

        if call_id and duration:
            db = get_db()
            db.execute("UPDATE leads SET call_duration_seconds = ? WHERE call_id = ?", (duration, call_id))
            db.commit()

        print(f"\n{'='*50}")
        print(f"Call ended: {message.get('endedReason')} (duration: {duration}s)")
        transcript = message.get("artifact", {}).get("transcript", "")
        if transcript:
            print(f"Transcript preview: {transcript[:200]}...")
        print(f"{'='*50}\n")
```

**Step 2: Add cost analysis HTML template**

```python
COSTS_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Westbrook & Associates — Cost Analysis</title>
<style>
  :root { --dark: #1a1a2e; --gold: #D4AF37; --bg: #f8f9fa; --text: #2D1B2E; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); }
  .header { background: var(--dark); padding: 20px 32px; display: flex; align-items: center; justify-content: space-between; }
  .header h1 { color: var(--gold); font-size: 20px; }
  .header-right { display: flex; gap: 16px; align-items: center; }
  .header a { color: rgba(255,255,255,0.7); text-decoration: none; font-size: 13px; }
  .header a:hover { color: white; }
  .container { max-width: 900px; margin: 0 auto; padding: 24px; }
  h2 { margin-bottom: 16px; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 32px; }
  .card { background: white; padding: 24px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
  .card label { font-size: 11px; text-transform: uppercase; color: #888; letter-spacing: 0.5px; }
  .card .value { font-size: 28px; font-weight: 700; margin-top: 4px; }
  .card .sub { font-size: 13px; color: #888; margin-top: 4px; }
  table { width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
  th { background: var(--dark); color: var(--gold); padding: 12px 16px; text-align: left; font-size: 12px; text-transform: uppercase; }
  td { padding: 14px 16px; border-bottom: 1px solid #eee; font-size: 14px; }
</style>
</head>
<body>
<div class="header">
  <h1>Westbrook & Associates</h1>
  <div class="header-right">
    <a href="{{ url_for('dashboard') }}">Dashboard</a>
    <a href="{{ url_for('admin_users') }}">Users</a>
    <a href="{{ url_for('logout') }}">Logout ({{ current_user.name }})</a>
  </div>
</div>
<div class="container">
  <h2>Cost Analysis — {{ month_label }}</h2>

  <div class="cards">
    <div class="card">
      <label>Total Calls</label>
      <div class="value">{{ total_calls }}</div>
      <div class="sub">this month</div>
    </div>
    <div class="card">
      <label>Total Minutes</label>
      <div class="value">{{ total_minutes }}</div>
      <div class="sub">{{ total_seconds }}s total</div>
    </div>
    <div class="card">
      <label>Est. Vapi Cost</label>
      <div class="value" style="color: #e74c3c;">${{ vapi_cost }}</div>
      <div class="sub">@ $0.05/min</div>
    </div>
    <div class="card">
      <label>Emails Sent</label>
      <div class="value">{{ emails_sent }}</div>
      <div class="sub">nurture + notifications</div>
    </div>
  </div>

  <h2>Service Pricing Reference</h2>
  <table>
    <thead>
      <tr><th>Service</th><th>Pricing</th><th>Notes</th></tr>
    </thead>
    <tbody>
      <tr>
        <td><strong>Vapi.ai</strong></td>
        <td>~$0.05/min</td>
        <td>Includes Claude Sonnet, ElevenLabs, Deepgram</td>
      </tr>
      <tr>
        <td><strong>Resend</strong></td>
        <td>Free up to 3,000 emails/mo, then $20/mo</td>
        <td>Nurture emails + lead notifications</td>
      </tr>
      <tr>
        <td><strong>Phone Number</strong></td>
        <td>~$2/mo</td>
        <td>Twilio via Vapi</td>
      </tr>
      <tr>
        <td><strong>ngrok</strong></td>
        <td>Free (random URL) or $8/mo (stable URL)</td>
        <td>Required for webhook tunnel</td>
      </tr>
    </tbody>
  </table>
</div>
</body>
</html>
"""
```

**Step 3: Add costs route**

```python
@app.route("/admin/costs")
@role_required("admin")
def admin_costs():
    db = get_db()
    now = datetime.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0).isoformat()
    month_label = now.strftime("%B %Y")

    # Calls this month
    calls = db.execute(
        "SELECT COUNT(*) as count, COALESCE(SUM(call_duration_seconds), 0) as total_seconds FROM leads WHERE created_at >= ?",
        (month_start,),
    ).fetchone()

    total_calls = calls["count"]
    total_seconds = calls["total_seconds"]
    total_minutes = round(total_seconds / 60, 1) if total_seconds else 0
    vapi_cost = round(total_minutes * 0.05, 2)

    # Estimate emails (nurture leads this month + qualified notifications)
    nurture_count = db.execute(
        "SELECT COUNT(*) FROM leads WHERE routing = 'nurture' AND created_at >= ?", (month_start,)
    ).fetchone()[0]
    qualified_count = db.execute(
        "SELECT COUNT(*) FROM leads WHERE routing = 'qualified' AND created_at >= ?", (month_start,)
    ).fetchone()[0]
    emails_sent = nurture_count + qualified_count

    return render_template_string(
        COSTS_HTML,
        month_label=month_label,
        total_calls=total_calls,
        total_minutes=total_minutes,
        total_seconds=total_seconds,
        vapi_cost=f"{vapi_cost:.2f}",
        emails_sent=emails_sent,
    )
```

**Step 4: Commit**

```bash
git add server.py
git commit -m "feat: add cost analysis page with call tracking and pricing reference"
```

---

### Task 10: Final Verification and Cleanup

**Step 1: Remove the diagnostic payload log from webhook_tools**

Remove this line from `webhook_tools()`:
```python
    print(f"\n=== TOOL CALL PAYLOAD ===\n{json.dumps(data, indent=2)}\n========================\n")
```

**Step 2: Verify full flow with test client**

Run:
```bash
python -c "
import server
server.init_db()
with server.app.test_client() as c:
    # Unauthenticated: should redirect to login
    resp = c.get('/')
    assert resp.status_code == 302
    print('1. Dashboard redirects to login: OK')

    # Login page renders
    resp = c.get('/login')
    assert resp.status_code == 200
    assert b'Sign in with Google' in resp.data
    print('2. Login page renders: OK')

    # Webhook routes are NOT protected
    resp = c.post('/webhook/vapi', json={'message': {'type': 'test'}})
    assert resp.status_code == 200
    print('3. Webhook routes unprotected: OK')

    print('All checks passed!')
"
```

**Step 3: Update README with admin backend section**

Add a section about the admin backend, login, user management, and cost analysis to `README.md`.

**Step 4: Final commit and push**

```bash
git add server.py README.md
git commit -m "feat: admin backend complete — auth, roles, leads, notifications, costs"
git push
```
