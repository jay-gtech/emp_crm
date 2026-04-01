# 🏢 Employee CRM — AI-Powered Task Assistant

A production-ready **FastAPI** Employee Management CRM with an integrated AI Task Assistant featuring ML-based priority prediction, delay risk scoring, and explainable outputs.

---

## ✨ Features

| Module | Description |
|---|---|
| 👤 **Auth** | Session-based login with RBAC (Admin / Manager / Team Lead / Employee) |
| 📋 **Tasks** | Create, assign, update status; AI-ranked suggestions |
| 🤖 **AI Assistant** | RandomForest priority prediction + LogisticRegression delay model + hybrid rule engine |
| 📊 **Dashboard** | Role-scoped KPIs, charts, and team analytics |
| 🏖️ **Leaves** | Apply, approve/reject; annual quota tracking |
| 🕐 **Attendance** | Check-in / check-out with break tracking |
| 🔔 **Notifications** | Event-driven alerts routed via hierarchy service |
| 📧 **Email** | SMTP notification support (opt-in via `EMAIL_ENABLED`) |
| 🔍 **Audit Log** | Immutable trail of all critical actions |

---

## 🤖 AI Task Assistant

```
GET /ai/task-suggestions
```

Returns active tasks ranked by urgency with:

- `ai_priority` — 🔥 High / ⚠️ Medium / 🟢 Low
- `confidence` — ML model probability (0–1)
- `reason` — Human-readable explanation ("Overdue by 3 days")
- `at_risk_of_delay` — Boolean delay risk flag
- `rank` — Urgency rank (1 = most urgent)

### Hybrid Logic

```
final_priority = max_urgency(rule_result, ml_result)
```

Rules always **escalate** — the ML model can never silently downgrade a rule-flagged HIGH task.

### Self-Learning Pipeline

Every prediction is logged to `training_log.jsonl` for future retraining. Outcomes are patched back via `update_outcome()`.

---

## 🚀 Quick Start

### Prerequisites

- Python 3.10+
- pip

### 1. Clone & set up environment

```bash
git clone https://github.com/jay-gtech/emp_crm.git
cd emp_crm
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — set SECRET_KEY, SMTP settings, etc.
```

### 3. Run the server

```bash
python run.py
```

Open [http://localhost:8000](http://localhost:8000)

---

## 🧪 Testing

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Run full test suite
pytest tests/ -v

# AI Task Assistant tests only
pytest tests/test_ai_task_assistant.py -v

# With coverage
pytest --cov=app tests/
```

**Test suite covers:**
- API response shape & authentication
- ML priority logic (overdue / near / far deadline)
- Hybrid rule-override behaviour
- Confidence range validation
- Delay prediction accuracy
- Ranking correctness
- Fallback on model load failure
- JSONL logging pipeline
- Outcome update patching
- Edge cases (no tasks, missing deadline, invalid data)

---

## 📁 Project Structure

```
emp_crm/
├── app/
│   ├── core/           # Config, auth, database
│   ├── ml/
│   │   └── task_assistant/
│   │       ├── predict.py          # Hybrid ML + rule predictor
│   │       ├── train.py            # Model training script
│   │       ├── save_training_data.py  # JSONL logging pipeline
│   │       ├── model.pkl           # RandomForest (priority)
│   │       └── delay_model.pkl     # LogisticRegression (delay)
│   ├── models/         # SQLAlchemy ORM models
│   ├── routes/         # FastAPI routers
│   ├── services/       # Business logic layer
│   ├── static/         # CSS, JS assets
│   └── templates/      # Jinja2 HTML templates
├── tests/
│   ├── conftest.py                  # Shared fixtures
│   ├── test_ai_task_assistant.py    # AI QA suite (65 tests)
│   ├── test_tasks.py
│   ├── test_auth.py
│   ├── test_leaves.py
│   └── ...
├── requirements.txt
├── requirements-dev.txt
├── run.py
└── .env.example
```

---

## ⚙️ Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | *(required)* | Session signing key |
| `DATABASE_URL` | `sqlite:///emp_crm.db` | Production DB URL |
| `TEST_DATABASE_URL` | `sqlite:///test_crm.db` | Test DB URL |
| `EMAIL_ENABLED` | `false` | Enable SMTP notifications |
| `SMTP_HOST` | `smtp.gmail.com` | SMTP server |
| `SMTP_PORT` | `587` | SMTP port |
| `EMAIL_USER` | — | Sender email address |
| `EMAIL_PASSWORD` | — | App password / token |

---

## 🔒 Security

- Passwords hashed with **bcrypt**
- Session cookies signed with `SECRET_KEY`
- `.env` and all `*.db` files are **gitignored** — never committed
- RBAC enforced on every route

---

## 📄 License

MIT — see [LICENSE](LICENSE) for details.
