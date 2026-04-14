# 6Minds 🧠

> 6 expert minds analyze your startup idea. Real market data. Brutal honesty. No sugarcoating.

![6Minds](static/icons/logo.png)

## What is 6Minds?

6Minds is a startup idea analyzer that puts your idea in front of 6 world-class expert personas — each with a different lens, different priorities, and zero tolerance for bad ideas.

Not a feel-good validator. Not a generic AI chatbot.
A brutal, honest, market-aware analysis engine.

---

## The 6 Expert Minds

| Expert | Role | What they look for |
|--------|------|-------------------|
| 🦈 The Shark | Investor | TAM, unit economics, exit potential |
| 🔥 Been There, Failed That | Failed Founder | Real execution risks and failure patterns |
| 🌍 The Market Oracle | Market Analyst | Real competitors, market size, timing |
| 😤 Your Target Customer | End User | Willingness to pay, switching costs |
| ⚔️ Your Biggest Competitor | Competitor CEO | How they would crush you |
| 💡 The Billionaire Builder | Serial Founder | The one thing that makes or breaks it |

---

## Features

- **Real Market Analysis** — actual competitor names, funding data, market sizes
- **Startup Viability Score** — scored across 4 dimensions (not just a random number)
- **Collapsible Expert Cards** — KEY INSIGHT always visible, full analysis on demand
- **Startup Battle Plan** — after the roast, get a full rescue plan to actually succeed
- **Find Your First 10 Customers** — specific, actionable steps
- **30-Day Validation Plan** — exactly what to do this month
- **Kill Metrics** — 3 numbers that tell you in 30 days if it works

---

## How It Works

1. **Describe your idea** — the more detail, the better the analysis
2. **Get roasted** — all 6 experts analyze it simultaneously
3. **See your score** — Startup Viability Score out of 100
4. **Get your battle plan** — click "Get Your Startup Battle Plan" for a full rescue

---

## Scoring Guide

| Score | Verdict |
|-------|---------|
| 0–20 | Don't quit your day job |
| 21–35 | Needs serious rethinking |
| 36–50 | Interesting but needs major differentiation |
| 51–65 | Has potential — execution is everything |
| 66–80 | Strong idea. Move fast. |
| 81+ | Why are you still reading this? Go build it. |

---

## Tech Stack

- **Backend** — Python, Flask
- **AI** — Anthropic Claude API (Haiku model)
- **Frontend** — Vanilla JS, Pure CSS
- **Deployment** — Railway
- **No database** — stateless, fast, simple

---

## Local Development

```bash
# Clone the repo
git clone https://github.com/meisamith/6minds.git
cd 6minds

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Add your API key
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY

# Run locally
python app.py
```

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Your Anthropic API key |
| `FLASK_SECRET_KEY` | Any random secret string |

---

## Deployment

Deployed on Railway. Every push to `main` auto-deploys.

---

## Built by

**Amith Choudhary**

---

## License

MIT — do whatever you want with it.
