# Admin Backend Design — Westbrook & Associates Voice Agent

**Date:** 2026-03-04
**Status:** Approved

## Goal

Add an authenticated admin backend so the entire firm (admins, attorneys, staff) can manage leads, get notified of hot leads, and track API costs.

## Architecture

- Flask + Jinja server-rendered pages (no separate frontend)
- SQLite database (existing, extended with new tables)
- Flask-Login for session management
- Authlib for Google OAuth
- Resend for email notifications (already integrated)

## Authentication

- **Login page** with Google OAuth button + email/password form
- **Invite-only registration** — Admin adds users via admin panel (no public signup)
- **First user** created via CLI: `python server.py create-admin`
- **Sessions** managed by Flask-Login, server-side
- **Password reset** — Admin resets passwords for users (no email reset flow)

## Roles & Permissions

| Role | View Leads | Add Notes | Change Lead Status | Manage Users | Delete Leads |
|------|-----------|-----------|-------------------|-------------|-------------|
| Admin | All | Yes | Yes | Yes | Yes |
| Attorney | All | Yes | Yes | No | No |
| Staff | All | Yes | No | No | No |

- Roles assigned at invite time, changeable by Admin
- At least one Admin must always exist

## Lead Management

**Lead table enhancements:**
- Add `status` column: New → Contacted → Consultation Booked → Retained / Closed
- Click a row to open lead detail view

**Lead detail view:**
- All lead info (name, case type, score, routing, phone, email, zip, call ID)
- Notes thread — any user can add notes, timestamped with author name
- Status change buttons (Attorney+ only)

**Filtering & Search:**
- Filter by routing (qualified/nurture/redirect), status, date range
- Search by name, phone, or email

**Deletion:**
- Only Admin can archive/delete leads

## Notifications

**Browser notifications:**
- On dashboard auto-refresh (every 10 seconds), check for new qualified leads (score 7-10) since last check
- Show banner at top: "New qualified lead: [Name] — [Case Type]" with link
- No WebSockets — uses existing auto-refresh mechanism

**Email notifications:**
- Admin can configure a notification email address in settings
- When a score 7-10 lead arrives, Resend sends alert to that address

## Cost Analysis

Admin-only page at `/admin/costs` showing:

**Tracked metrics:**
- Total calls this month
- Total call minutes (from end-of-call-report webhook duration)
- Emails sent this month (nurture + notification)

**Cost estimates:**
- Vapi.ai: ~$0.05/min — calculated from tracked minutes
- Resend: Free tier 3,000 emails/mo, then $20/mo
- ngrok: Free or $8/mo for stable URL
- Twilio (via Vapi): ~$2/mo per phone number

**Database support:**
- Add `call_duration_seconds` column to leads table
- Store duration from end-of-call-report webhook

## Database Changes

**New table: `users`**
```sql
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    password_hash TEXT,          -- null for Google-only users
    google_id TEXT,
    role TEXT NOT NULL DEFAULT 'staff',  -- admin, attorney, staff
    created_at TEXT NOT NULL
);
```

**New table: `lead_notes`**
```sql
CREATE TABLE lead_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES leads(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

**Altered table: `leads`**
- Add column: `status TEXT NOT NULL DEFAULT 'new'` (new, contacted, consultation_booked, retained, closed)
- Add column: `call_duration_seconds INTEGER`
