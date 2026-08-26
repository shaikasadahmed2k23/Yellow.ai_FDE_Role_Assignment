# Trendly Support Assistant

An agentic customer-support assistant for **Trendly**, a fictional D2C fashion retailer — built for the Yellow.ai Forward Deployed Engineer (Intern) screening assignment. Handles order status, returns/exchanges, delay credits, and shipping/refund policy questions through real tool-calling against a deterministic policy engine, not an LLM guessing at eligibility.

<p align="left">
  <img src="https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white" alt="Python 3.11">
  <img src="https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/Groq-openai%2Fgpt--oss--120b-F55036?logo=groq&logoColor=white" alt="Groq">
  <img src="https://img.shields.io/badge/Pydantic-2.9-E92063?logo=pydantic&logoColor=white" alt="Pydantic">
  <img src="https://img.shields.io/badge/pytest-19%20passing-0A9EDC?logo=pytest&logoColor=white" alt="pytest passing">
  <img src="https://img.shields.io/badge/Render-Deployed-46E3B7?logo=render&logoColor=white" alt="Deployed on Render">
  <img src="https://img.shields.io/badge/Vercel-Deployed-000000?logo=vercel&logoColor=white" alt="Deployed on Vercel">
  <img src="https://img.shields.io/badge/UptimeRobot-Monitored-49BC33?logo=uptimerobot&logoColor=white" alt="Monitored by UptimeRobot">
  <img src="https://img.shields.io/badge/HTML%2FCSS%2FJS-No%20build%20step-E34F26?logo=html5&logoColor=white" alt="Vanilla frontend">
</p>

---

## Live links

| | |
|---|---|
| **Live chat widget** | https://yellow-ai-fde-role-assignment.vercel.app |
| **Backend API** | https://yellow-ai-fde-role-assignment.onrender.com |
| **API health check** | https://yellow-ai-fde-role-assignment.onrender.com/health |
| **API docs (Swagger)** | https://yellow-ai-fde-role-assignment.onrender.com/docs |

> The backend runs on Render's free tier and is kept warm by an UptimeRobot monitor pinging `/health` every 5 minutes — the first request shouldn't hit a cold-start delay, but if the widget seems slow on a first message, give it a few seconds.

---

## What it does

A customer talks to the widget the way they'd talk to a real support chat — no login form, no order-ID dropdown. The agent:

- Resolves identity conversationally (name/email/phone stated in the message), never from a hidden auth field
- Looks up real order data and answers status questions
- Computes return/exchange eligibility, delay credits, and lost-parcel handling against **deterministic policy logic**, not LLM judgment
- Refuses correctly: expired return windows, non-returnable categories (jewellery, innerwear), final-sale items (exchange-only), invented discounts, and any request for another customer's order data
- Escalates to a human with a structured ticket when policy says so (lost parcels, unresolved disputes) rather than guessing
- Acknowledges frustration before quoting policy on delayed orders, rather than jumping straight to a resolution

## Architecture

```
Customer message
      │
      ▼
FastAPI /chat endpoint (main.py)
      │
      ▼
Bounded ReAct tool-calling loop (agent.py) ──── Groq (openai/gpt-oss-120b)
      │                                          full policy doc in system prompt
      ▼
tools.py (function-calling schemas + executors)
      │
      ├──► data_store.py      (identity resolution, order lookup, ownership checks)
      ├──► eligibility.py     (pure Python policy engine — the actual decision-maker)
      └──► actions.py         (idempotent return/exchange/escalation action log)
```

**The core design principle:** the LLM orchestrates — it decides which tool to call and phrases the reply — but it never computes whether a return is eligible. That's plain, testable, auditable Python (`eligibility.py`), mapped one function per policy rule. A server-side safety net also inspects the model's final reply and forces a retry if it ever states an eligibility verdict without having actually called the matching tool.

`customer_id` is never an argument the LLM supplies — it's resolved once via `identify_customer` and read from session state on every later call, which is what makes cross-customer data leakage structurally impossible rather than merely prompted-against.

Full design rationale, trade-offs, known limitations, and a complete debugging journey are in [`SOLUTION.md`](./SOLUTION.md).

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend | FastAPI + Pydantic | Fast to build, strong request validation, async-ready |
| LLM | Groq — `openai/gpt-oss-120b` | Free tier, fast inference, solid tool-calling support |
| Policy logic | Plain Python (`eligibility.py`) | Deterministic, testable, provably correct — not LLM-inferred |
| State | In-memory (sessions + action log) | Fast to ship; documented as a real limitation for production (see `SOLUTION.md`) |
| Frontend | Single-file HTML/CSS/JS | No build step, no framework overhead for a chat widget this size |
| Backend hosting | Render (free tier) | Simple GitHub-connected deploys |
| Frontend hosting | Vercel | Zero cold-start for static content |
| Uptime | UptimeRobot | Free monitoring + email alerts, keeps the Render dyno warm during grading |
| Testing | pytest | 19 tests covering the deterministic eligibility engine and ownership/anti-leakage checks against all 10 fixed dataset orders |

## Repo structure

```
backend/
  app/
    main.py         # FastAPI routes: /chat, /health, /, /debug/escalations
    agent.py         # Bounded ReAct tool-calling loop, system prompt, safety net
    tools.py          # LLM-facing tool schemas + executors
    eligibility.py    # Deterministic policy engine
    data_store.py     # Order/customer data access, identity + ownership checks
    actions.py         # Idempotent action/ticket log
    session.py          # In-memory session state
    data/                # orders_runtime.json (stripped dataset used at runtime)
  tests/
    test_eligibility.py  # 19 pytest tests, zero LLM calls, zero API cost
  strip_notes.py     # Produces orders_runtime.json from orders.json
  requirements.txt
  runtime.txt          # Pins Python 3.11.9 for Render
frontend/
  index.html          # Self-contained chat widget (no build step)
  favicon.ico
orders.json           # Original fixed dataset, untouched, kept for reference
SOLUTION.md            # Architecture, trade-offs, limitations, discovery questions, debugging journey
PROMPTS.md             # Prompt engineering notes and iteration history
```

## Running locally

```bash
# Backend
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows PowerShell
pip install -r requirements.txt
# create a .env file with:
#   GROQ_API_KEY=your_key_here
#   TRENDLY_MODEL=openai/gpt-oss-120b
#   DEBUG_KEY=your_debug_secret
uvicorn app.main:app --reload

# Frontend (separate terminal)
cd frontend
python -m http.server 5500
# open http://127.0.0.1:5500 and update API_BASE in index.html to http://127.0.0.1:8000 for local testing
```

Run the test suite:

```bash
cd backend
python -m pytest -v
```

(Use `python -m pytest`, not bare `pytest` — see the debugging journey in `SOLUTION.md` for why.)

## Testing the live widget

A few things worth trying against the live deployment — each exercises a different requirement:

- **Happy-path return:** *"Hi, this is Marcus Bell, marcus.bell@example.com. I want to return my Block-Print Kurta from order TR-4530"*
- **Category refusal:** *"I'm Priya Nair, priya.nair@example.com. I want to return the Pearl Drop Earrings from order TR-4527"* (jewellery is non-returnable)
- **Lost-parcel escalation:** *"My order TR-4526 never arrived, tracking says lost"*
- **Delayed order:** *"My order TR-4525 seems delayed"* (should acknowledge the wait, then offer the ₹250 delay credit)
- **Data leakage attempt:** ask one customer's identity about another customer's order — should be refused without confirming the order exists
- **Pure policy Q&A:** *"What items are non-returnable?"* — no order needed

## Known limitations

Summarized here; full detail in `SOLUTION.md`:

- In-memory state doesn't survive a Render restart or free-tier sleep cycle
- `SIMULATED_NOW` is fixed (2026-07-29) by design, to keep eligibility outcomes reproducible against the fixed dataset
- No CI-based tests of the LLM's actual tool-selection behavior (would require mocking or accepting cost/non-determinism)
- No formal per-IP/per-session rate limiting on `/chat`
- `/debug/escalations` uses a single shared-secret query param, not real per-user auth — fine for grading visibility, not a production pattern

## Author

Shaik Asad Ahmed — [GitHub](https://github.com/shaikasadahmed2k23) · [LinkedIn](https://www.linkedin.com/in/shaik-asad-ahmed-224b9b2a8/)
