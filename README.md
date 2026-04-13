# Employee CRM System

A full-stack, production-ready **Employee CRM** built with FastAPI and PostgreSQL. Covers the complete HR lifecycle — attendance, tasks, leaves, reporting, expenses, real-time chat, and visitor management — enforced by a strict 4-tier organisational hierarchy and role-based access control.

**Live demo (Render):** _deploy via `render.yaml` in this repo_  
**GitHub:** https://github.com/jay-gtech/emp_crm

---

## Table of Contents

1. [Features](#features)
2. [Tech Stack](#tech-stack)
3. [Project Structure](#project-structure)
4. [Local Setup](#local-setup)
5. [Environment Variables Reference](#environment-variables-reference)
6. [Running the Application](#running-the-application)
7. [Test Credentials](#test-credentials)
8. [Seeding Data](#seeding-data)
9. [API Documentation](#api-documentation)
10. [Database](#database)
11. [Deployment (Render)](#deployment-render)
12. [Known Limitations](#known-limitations)

---

## Features

### Role-Based Access Control

Five roles with strictly scoped data access:

| Role | Hierarchy level | What they see |
|---|---|---|
| **Admin** | 1 — top | Everything |
| **Manager** | 2 | Their team leads + those TLs' employees |
| **Team Lead** | 3 | Employees assigned to them |
| **Employee** | 4 | Own records only |
| **Security Guard** | — | Visitor management only |

Hierarchy is enforced at creation time — a Team Lead can only be assigned to a Manager; an Employee can only be assigned to a Team Lead.

### Modules

| Module | Key capabilities |
|---|---|
| **Authentication** | Session-based login, bcrypt passwords, rate-limited login endpoint |
| **Dashboard** | Role-scoped KPIs, team stats, overdue task counts, performance charts |
| **Attendance** | Clock-in/out, break tracking, work-mode (office / WFH), location geo-fence |
| **Task Management** | Create → assign → start → complete → approve; priority, deadline, delay detection |
| **Leave Management** | Apply, approve/reject, balance tracking (20-day annual quota) |
| **Employee Directory** | Add, edit, deactivate; hierarchy-aware listing; CSV export |
| **Org Hierarchy** | Validated parent assignment on create; visual org tree |
| **Reporting** | Hourly + End-of-Day reports, filterable analytics dashboard |
| **Announcements** | Audience-targeted: all staff / role / specific team |
| **Chat** | Real-time WebSocket group chat with file attachments |
| **Meetings** | Schedule and track team meetings |
| **Expenses** | Group expense split and tracking |
| **Visitor Management** | Register, approve/reject, photo upload |
| **Notifications** | In-app notification centre |
| **AI Assistant** | ML-powered task priority prediction + leave risk scoring; auto-retrained every 24 h |
| **Audit Log** | Immutable log of all critical admin actions |

---

## Tech Stack

| Component | Technology |
|---|---|
| Backend framework | FastAPI 0.111 |
| ASGI server | Uvicorn |
| Database | PostgreSQL 15+ |
| ORM | SQLAlchemy 2.0 |
| Templating | Jinja2 3.1 |
| Auth / sessions | Starlette `SessionMiddleware` + itsdangerous |
| Rate limiting | slowapi |
| ML / AI | scikit-learn · pandas · joblib |
| Real-time | WebSockets (built-in FastAPI) |
| Python version | 3.10+ |

---

## Project Structure

```
emp_crm/
├── app/
│   ├── main.py                 # App factory · startup hooks · router registration
│   ├── core/
│   │   ├── auth.py             # Password hashing · session helpers · route guards
│   │   ├── config.py           # All settings loaded from environment variables
│   │   ├── database.py         # SQLAlchemy engine (dialect-aware: SQLite + PostgreSQL)
│   │   ├── db_migration.py     # Safe additive column migrations (no Alembic yet)
│   │   └── limiter.py          # slowapi rate-limiter instance
│   ├── models/                 # SQLAlchemy ORM models — one file per entity
│   │   └── user.py · task.py · attendance.py · leave.py · message.py · …
│   ├── routes/                 # FastAPI routers — one file per feature
│   │   └── auth.py · dashboard.py · tasks.py · attendance.py · chat.py · …
│   ├── services/               # Business logic layer (routes only call services)
│   │   └── task_service.py · leave_service.py · hierarchy_service.py · …
│   ├── ml/                     # Machine-learning pipeline
│   │   ├── training/           # Core model training + inference
│   │   ├── task_assistant/     # Task priority predictor
│   │   ├── leave_prediction/   # Leave risk scorer
│   │   ├── auto_assignment/    # Intelligent task auto-assignment
│   │   └── retraining/         # Nightly retraining pipeline
│   ├── static/
│   │   ├── css/main.css        # Single design-system stylesheet
│   │   ├── js/                 # Client-side scripts
│   │   └── uploads/            # User-uploaded files (gitignored at runtime)
│   └── templates/              # Jinja2 HTML templates
│       ├── base.html           # Shared responsive layout
│       └── <feature>/          # Per-feature templates
├── scripts/
│   ├── seed_data.py            # Full seed: 44 employees + team structure + tasks
│   ├── seed_tasks.py           # Seed tasks only
│   └── retrain_model.py        # Manual ML retraining trigger
├── seed.py                     # Minimal seed: default role accounts only
├── run.py                      # Development server (hot-reload, port 8000)
├── requirements.txt            # All production dependencies
├── render.yaml                 # Render deployment manifest
├── Procfile                    # Universal start command
└── .env                        # Local environment variables — never committed
```

---

## Local Setup

### Prerequisites

- Python **3.10** or higher
- **PostgreSQL 15+** running locally
- Git

---

### Step 1 — Clone the repository

```bash
git clone https://github.com/jay-gtech/emp_crm.git
cd emp_crm
```

---

### Step 2 — Create a virtual environment

```bash
python -m venv crm_env

# Activate on Linux / macOS
source crm_env/bin/activate

# Activate on Windows
crm_env\Scripts\activate
```

---

### Step 3 — Install dependencies

```bash
pip install -r requirements.txt
```

---

### Step 4 — Create the PostgreSQL database

Connect to PostgreSQL and run:

```sql
CREATE DATABASE emp_crm;
```

> **Non-default port?** If your PostgreSQL runs on port `5433` (common when multiple PG versions are installed), use `localhost:5433` in the `DATABASE_URL` below.

---

### Step 5 — Create the `.env` file

Create a `.env` file in the project root with the following content:

```env
# Runtime mode: dev | prod
ENV=dev

# PostgreSQL connection string
# IMPORTANT: URL-encode special characters in passwords
#   e.g. if password is  jay@123  →  write  jay%40123  in the URL
DATABASE_URL=postgresql://postgres:your_password@localhost:5432/emp_crm

# Session signing key — any long random string works for local dev
SECRET_KEY=replace-with-a-long-random-secret-string

# Admin account created automatically on first startup
ADMIN_EMAIL=admin@company.com
ADMIN_PASSWORD=Admin@123

# Email notifications (safe to leave disabled during testing)
EMAIL_ENABLED=false
```

> The `.env` file is listed in `.gitignore` and will never be committed to the repository.

---

## Environment Variables Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | **Yes** | — | PostgreSQL connection URL |
| `SECRET_KEY` | **Yes** | insecure default | Session signing key — must be changed in production |
| `ENV` | No | `dev` | Runtime mode (`dev` / `prod`) |
| `ADMIN_EMAIL` | No | `admin@company.com` | Email for the auto-seeded admin |
| `ADMIN_PASSWORD` | No | `admin123` | Password for the auto-seeded admin |
| `ALLOWED_ORIGINS` | No | `*` | Comma-separated CORS origins (set explicitly in production) |
| `EMAIL_ENABLED` | No | `false` | Enable SMTP email notifications |
| `SMTP_HOST` | No | `smtp.gmail.com` | SMTP server host |
| `SMTP_PORT` | No | `587` | SMTP server port |
| `EMAIL_USER` | No | — | Sender email address |
| `EMAIL_PASSWORD` | No | — | SMTP app password / token |
| `ML_LOGGING` | No | `true` | Log predictions for future ML retraining |

---

## Running the Application

### Development server (hot-reload, port 8000)

```bash
python run.py
```

### Production-style server

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8005
```

**What happens on first startup:**

1. All 19 database tables are created automatically (safe — never drops existing data)
2. Safe additive schema migrations are applied
3. The default admin account is seeded using `ADMIN_EMAIL` / `ADMIN_PASSWORD`
4. The ML model is warmed up for fast inference
5. The 24-hour retraining scheduler starts in the background

**Access the app:**

| URL | Description |
|---|---|
| `http://localhost:8005` | Main application |
| `http://localhost:8005/docs` | Swagger API docs |
| `http://localhost:8005/redoc` | ReDoc API docs |

---

## Test Credentials

> Run `python seed.py` after the first startup to create all role accounts below.
> The **Admin** account is created automatically at startup.

### Default Role Accounts

| Role | Email | Password | Access |
|---|---|---|---|
| **Admin** | `admin@company.com` | `Admin@123` | Full system — all modules, all users |
| **Manager** | `manager@company.com` | `Manager@123` | Team leads + employees under them |
| **Team Lead** | `ai.lead@company.com` | `TeamLead@123` | Employees assigned to them; tasks |
| **Employee** | `employee@company.com` | `Employee@123` | Own attendance, tasks, leaves |
| **Security Guard** | `guard@company.com` | `Guard@123` | Visitor registration and approval |

### Organisational Hierarchy (seed structure)

```
Admin  (admin@company.com)
 └── Manager  (manager@company.com)
      ├── Team Lead — AI Team    (ai.lead@company.com)
      │    └── Employees: emp001 … emp011
      ├── Team Lead — Java Dev 1 (java1.lead@company.com)
      │    └── Employees: emp012 … emp022
      ├── Team Lead — Java Dev 2 (java2.lead@company.com)
      │    └── Employees: emp023 … emp033
      └── Team Lead — Java Dev 3 (java3.lead@company.com)
           └── Employees: emp034 … emp044
```

Additional team lead accounts: `java1.lead@company.com`, `java2.lead@company.com`, `java3.lead@company.com` — all use password `TeamLead@123`.

---

## Seeding Data

### Option A — Minimal seed (5 role accounts only)

```bash
python seed.py
```

Creates: admin, manager, one team lead, one employee, one security guard.

### Option B — Full seed (44 employees + full team structure + tasks)

```bash
python scripts/seed_data.py
```

Creates the complete org tree: 1 manager, 4 team leads, 44 employees distributed across teams, sample tasks, and performance scores.

---

## API Documentation

FastAPI generates interactive API docs automatically — no extra setup needed.

| Interface | URL |
|---|---|
| **Swagger UI** | http://localhost:8005/docs |
| **ReDoc** | http://localhost:8005/redoc |

### Key Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/auth/login` | Public | Login (form: `email`, `password`) |
| `GET` | `/auth/logout` | Login | Logout and clear session |
| `GET` | `/dashboard/` | Login | Role-scoped dashboard |
| `GET` | `/employees/` | Login | Employee directory |
| `POST` | `/employees/new` | Admin | Create employee |
| `GET` | `/tasks/` | Login | Task list (role-scoped) |
| `GET` | `/attendance/` | Login | Attendance records |
| `GET` | `/leaves/` | Login | Leave requests |
| `GET` | `/api/tasks` | Login | Tasks JSON |
| `GET` | `/api/leave` | Login | Leaves JSON |
| `GET` | `/api/attendance` | Login | Attendance JSON |
| `GET` | `/api/dashboard` | Login | Dashboard summary JSON |
| `GET` | `/api/notifications` | Login | In-app notifications |
| `GET` | `/api/users/valid-parents?role=<role>` | Login | Valid parent users for hierarchy |
| `GET` | `/api/employees/export` | Manager+ | Download employees CSV |
| `GET` | `/api/audit` | Admin | Audit log JSON |
| `WS` | `/chat/ws/group/{group_id}` | Login | Real-time group chat WebSocket |

---

## Database

| Property | Detail |
|---|---|
| Engine | PostgreSQL 15+ |
| ORM | SQLAlchemy 2.0 (declarative models) |
| Table count | 19 tables |
| Schema management | `Base.metadata.create_all()` on startup — **never destructive** |
| Migrations | Additive-only via `app/core/db_migration.py` |

### Tables

`users` · `tasks` · `task_comments` · `attendance` · `break_records` · `leaves` · `notifications` · `audit_logs` · `announcements` · `messages` · `chat_groups` · `chat_group_members` · `meetings` · `expense_groups` · `expense_members` · `reports` · `eod_reports` · `location_logs` · `visitors`

### Connection URL format

```
postgresql://USERNAME:PASSWORD@HOST:PORT/DBNAME
```

Special characters in passwords must be URL-encoded: `@` → `%40`, `#` → `%23`, etc.

---

## Deployment (Render)

A `render.yaml` is committed to this repository. Render reads it automatically.

### Steps

**1. Create a persistent PostgreSQL database on Render**
- Render dashboard → **New → PostgreSQL**
- Copy the **External Database URL**

**2. Create a Web Service**
- Render dashboard → **New → Web Service** → connect `jay-gtech/emp_crm`
- Render reads `render.yaml` automatically
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port 10000`

**3. Set environment variables in the Render dashboard**

| Variable | Value |
|---|---|
| `DATABASE_URL` | Paste the Render PostgreSQL External URL |
| `SECRET_KEY` | Auto-generated by Render (`generateValue: true` in render.yaml) |
| `ENV` | `prod` |
| `ADMIN_EMAIL` | `admin@company.com` |
| `ADMIN_PASSWORD` | A strong password of your choice |
| `ALLOWED_ORIGINS` | `https://your-app-name.onrender.com` |

**4. Deploy** — tables are created automatically on the first boot. The app will be live at `https://your-app-name.onrender.com`.

---

## Known Limitations

| Item | Detail |
|---|---|
| No Alembic migrations | Schema changes handled by a safe additive migration script (`db_migration.py`). Alembic integration is planned. |
| File uploads are ephemeral on Render free tier | Upload directories are recreated at startup. For production persistence, migrate uploads to AWS S3 or Cloudinary. |
| No Docker support | Planned for a future release. |
| ML models require initial training data | A pre-trained model is committed to the repo and auto-retrained every 24 hours using live data. |
| No automated test suite | Tests were removed pre-QA handover. Manual testing is supported via this README and the Swagger UI. |

---

## Security Notes

- Passwords hashed with **bcrypt**
- Sessions signed with `SECRET_KEY` — never use the default value in production
- `.env` and `*.db` files are in `.gitignore` — credentials never enter the repository
- Login endpoint is **rate-limited** via slowapi
- All routes and data queries are **role-scoped** — no privilege escalation possible

---

*Built with FastAPI · SQLAlchemy · PostgreSQL · Jinja2 · scikit-learn · WebSockets*
