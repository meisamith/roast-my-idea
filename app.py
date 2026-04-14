import os
import json
import re
from statistics import mean
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Flask, render_template, request, jsonify, redirect
import anthropic
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "6minds-secret-2024")
client = anthropic.Anthropic(
    api_key=os.environ.get("ANTHROPIC_API_KEY"),
    timeout=120.0
)

# ── Shared scoring guide appended to every persona ────────────────────────────

_SCORING_GUIDE = (
    "SCORING GUIDE — be accurate, not harsh or lenient: "
    "copying existing product with zero differentiation → severity 9-10; "
    "existing idea in crowded market, slight twist → severity 7-8; "
    "real problem, some competition, unclear differentiation → severity 5-6; "
    "real problem, clear differentiation, viable market → severity 3-4; "
    "strong idea, good timing, defensible moat → severity 2-3; "
    "exceptional, perfect timing, clear path to scale → severity 1-2. "
    "If someone describes an existing dominant product (e.g. 'build Zomato') → severity 9-10. "
    "MUST reference REAL companies, REAL market data, REAL trends. "
    "Your roast must be at minimum 80 words — give real depth, not a one-liner. "
    'Respond ONLY in valid JSON: {"roast": "<your analysis>", "severity": <integer 1-10>, '
    '"key_insight": "<one powerful sentence — the single most important thing they need to hear>"} '
    "No markdown, no preamble, no trailing text."
)

# ── Persona definitions ───────────────────────────────────────────────────────

PERSONAS = [
    {
        "key": "shark",
        "persona": "The Shark",
        "emoji": "🦈",
        "angle": "Speaking as your potential investor...",
        "score_label": "Market Potential",
        "system": (
            "You are a cold, numbers-only venture capitalist. You only care about TAM, unit economics, "
            "and exit potential. Every question you ask is 'how does this become a $1B company?' "
            "You cite real market sizes (from Statista, CB Insights, Inc42), real comparable exits, "
            "and brutal assessments of whether this market is large enough to matter to an investor. "
            "You don't care about the product — only the business model and the size of the prize. "
            "You ask hard questions about defensibility and moat. "
            + _SCORING_GUIDE
        ),
    },
    {
        "key": "failed_founder",
        "persona": "Been There, Failed That",
        "emoji": "🔥",
        "angle": "Speaking as someone who tried this...",
        "score_label": "Execution Risk",
        "system": (
            "You tried to build something very similar to this and failed. You share SPECIFIC execution "
            "nightmares from real experience — the wrong tech hire that cost you 6 months, CAC that was "
            "10x what you modeled, churn you couldn't fix, unit economics that looked great at 100 users "
            "and destroyed you at 10,000, the pivot you made too late. You are not bitter — you are "
            "painfully honest. You give realistic numbers: what hiring a founding CTO actually costs, "
            "what CAC looks like in this specific vertical, what kills startups like this in year 2. "
            "You speak from scar tissue, not theory. Name the specific mistakes to avoid. "
            + _SCORING_GUIDE
        ),
    },
    {
        "key": "market_oracle",
        "persona": "The Market Oracle",
        "emoji": "🌍",
        "angle": "Speaking as a market analyst...",
        "score_label": "Market Timing",
        "system": (
            "You are a deeply researched market analyst. You give ACTUAL market size data (cite sources "
            "like Statista, McKinsey, Inc42, YourStory, Tracxn), name REAL competitors worldwide and in "
            "India, assess whether this market is growing or dying, and identify the real opportunity gap "
            "if any exists. You reference real funding rounds in this space, recent M&A activity, and "
            "macro trends (AI, regulation, demographics) affecting this market. You distinguish TAM from "
            "SAM from SOM with real numbers. You are neither positive nor negative — you report reality "
            "with precision. "
            + _SCORING_GUIDE
        ),
    },
    {
        "key": "angry_customer",
        "persona": "Your Target Customer",
        "emoji": "😤",
        "angle": "Speaking as the person you're building this for...",
        "score_label": "Customer Fit",
        "system": (
            "You ARE the actual target customer for this product. You react authentically — you name the "
            "real tools you use today to solve this problem (even if imperfect), your actual switching "
            "cost, what would genuinely make you pay vs what you'd ignore. What is the real problem this "
            "solves for you vs what the founder thinks the problem is? What's missing from their pitch? "
            "How much would you actually pay (be honest, not aspirational)? You are the toughest critic "
            "because you know your own pain better than any founder does. "
            + _SCORING_GUIDE
        ),
    },
    {
        "key": "the_competitor",
        "persona": "Your Biggest Competitor",
        "emoji": "⚔️",
        "angle": "Speaking as the CEO who will crush you...",
        "score_label": "Defensibility",
        "system": (
            "You are the CEO of a SPECIFIC, named real competitor in this space — identify yourself by "
            "name in the first sentence. You lay out in cold tactical detail: your current advantages "
            "(distribution, data moat, brand recognition, existing customer relationships, funding), "
            "exactly how you would replicate their core feature in 60-90 days, and how you would price "
            "it or bundle it free to kill the startup. You explain why your existing customers would "
            "never switch. You are clinical, not emotional — this is just business. You describe exactly "
            "what this startup would need to do to make you actually worried about them. "
            + _SCORING_GUIDE
        ),
    },
    {
        "key": "billionaire_builder",
        "persona": "The Billionaire Builder",
        "emoji": "💡",
        "angle": "Speaking as someone who's built this before...",
        "score_label": "Founder-Market Fit",
        "system": (
            "You are a serial entrepreneur who has built 3+ successful companies — think the mindset of "
            "Elon Musk crossed with Narayana Murthy. You've seen 10,000 pitches and know the difference "
            "between a feature and a company. You cut through noise and identify the ONE thing that could "
            "make this work, or the ONE fatal flaw that will kill it before anything else matters. "
            "You give real, actionable founder advice — specific pivots, specific go-to-market strategies, "
            "specific hiring priorities for the first 90 days. You are not a cheerleader. You are honest "
            "but constructive. You reference what actually worked for companies you've built or know well. "
            + _SCORING_GUIDE
        ),
    },
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def clean_json_response(raw):
    """Extract pure JSON from Claude response - handles all edge cases"""
    if not raw:
        return raw
    raw = raw.strip()

    # Method 1: Remove ```json ... ``` fences directly
    if raw.startswith('```'):
        # Remove opening fence
        raw = re.sub(r'^```(?:json)?\s*\n?', '', raw)
        # Remove closing fence
        raw = re.sub(r'\n?```\s*$', '', raw)
        raw = raw.strip()

    # Method 2: Find the JSON object by braces
    start = raw.find('{')
    end = raw.rfind('}')
    if start != -1 and end != -1 and end > start:
        return raw[start:end+1]

    return raw


def _parse_persona_response(raw: str) -> dict:
    # Attempt 1: direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Attempt 2: strip code fences, then parse
    cleaned = clean_json_response(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Attempt 3: extract the first {...} block that contains "roast"
    match = re.search(r'\{[^{}]*"roast"[^{}]*\}', raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Attempt 4: plain-text fallback
    return {"roast": raw[:800], "severity": 5, "key_insight": ""}


def extract_roast_text(text):
    """Extract clean roast text from any format"""
    text = text.strip()
    # Remove markdown fences
    if '```' in text:
        parts = text.split('```')
        for part in parts:
            part = part.strip()
            if part.startswith('json'):
                part = part[4:].strip()
            if len(part) > 50:
                text = part
                break
    # If it looks like JSON with a "roast" key, extract just the value
    if text.startswith('{') and '"roast"' in text:
        try:
            parsed = json.loads(text)
            if 'roast' in parsed:
                return parsed['roast']
        except Exception:
            # Try to extract value after "roast":
            match = re.search(r'"roast"\s*:\s*"(.*?)"\s*[,}]', text, re.DOTALL)
            if match:
                return match.group(1)
    return text


def _call_persona(persona: dict, idea: str) -> dict:
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        system=persona["system"],
        messages=[{"role": "user", "content": f"Analyze this startup idea: {idea}"}],
    )
    raw = message.content[0].text.strip()
    try:
        parsed = _parse_persona_response(raw)
        severity = max(1, min(10, int(parsed.get("severity", 5))))
        roast_text = extract_roast_text(str(parsed.get("roast", raw)))[:2000]
        key_insight = str(parsed.get("key_insight", ""))[:400]
    except Exception:
        return {
            "key": persona["key"],
            "persona": persona["persona"],
            "emoji": persona["emoji"],
            "angle": persona["angle"],
            "score_label": persona["score_label"],
            "roast": f"[{persona['persona']} had nothing coherent to say.]",
            "key_insight": "",
            "severity": 5,
        }
    return {
        "key": persona["key"],
        "persona": persona["persona"],
        "emoji": persona["emoji"],
        "angle": persona["angle"],
        "score_label": persona["score_label"],
        "roast": roast_text,
        "key_insight": key_insight,
        "severity": severity,
    }


def _compute_sub_scores(roast_list: list) -> dict:
    """Derive 4 dimension sub-scores (each 0-25) from individual persona severities."""
    key_map = {r["key"]: r["severity"] for r in roast_list}

    def _inv(s):
        return max(0, 25 - round(s * 2.5))

    market = _inv(key_map.get("shark", 5))
    timing = _inv(key_map.get("market_oracle", 5))
    diff = _inv(mean([key_map.get("the_competitor", 5), key_map.get("angry_customer", 5)]))
    execution = _inv(mean([key_map.get("failed_founder", 5), key_map.get("billionaire_builder", 5)]))

    return {
        "market": market,
        "differentiation": round(diff),
        "execution": round(execution),
        "timing": timing,
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/roast", methods=["POST"])
def roast():
    data = request.get_json(silent=True) or {}
    idea = data.get("idea", "").strip()

    if not idea:
        return jsonify({"error": "Please enter your startup idea."}), 400
    if len(idea) < 15:
        return jsonify({"error": "Please describe your idea in more detail — what problem does it solve and for who?"}), 400
    if len(idea) > 500:
        return jsonify({"error": "Idea must be 500 characters or less."}), 400

    # Quick validation: check whether the input is actually a startup idea
    try:
        validation_msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            system=(
                "You are a validator. Reply with exactly 'YES' if the input describes a startup idea "
                "(even vaguely — something with a business model, problem, or service). "
                "Reply with exactly 'NO' if it is just a product name, brand, single word, "
                "random text, or something with no describable business concept."
            ),
            messages=[{"role": "user", "content": idea}],
        )
        is_idea = validation_msg.content[0].text.strip().upper().startswith("YES")
    except Exception:
        is_idea = True  # fail open so a validation outage doesn't block real users

    if not is_idea:
        stub_roasts = [
            {
                "key": p["key"],
                "persona": p["persona"],
                "emoji": p["emoji"],
                "angle": p["angle"],
                "score_label": p["score_label"],
                "roast": "That's not an idea. That's a product that already exists (or just words). Try describing YOUR idea — what problem it solves and for who.",
                "key_insight": "Describe a real problem you've personally experienced and want to solve.",
                "severity": 10,
            }
            for p in PERSONAS
        ]
        zero_sub = {"market": 0, "differentiation": 0, "execution": 0, "timing": 0}
        return jsonify({"roasts": stub_roasts, "survival_score": 0, "sub_scores": zero_sub, "not_an_idea": True})

    try:
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {
                executor.submit(_call_persona, persona, idea): persona["key"]
                for persona in PERSONAS
            }
            results = {}
            for future in as_completed(futures):
                key = futures[future]
                results[key] = future.result()

    except anthropic.AuthenticationError:
        return jsonify({"error": "Invalid API key. Check your ANTHROPIC_API_KEY in .env."}), 401
    except anthropic.RateLimitError:
        return jsonify({"error": "Rate limit reached. Wait a moment and try again."}), 429
    except anthropic.APIStatusError as e:
        return jsonify({"error": f"Claude returned an error (HTTP {e.status_code}). Try again shortly."}), 502
    except anthropic.APIConnectionError:
        return jsonify({"error": "Couldn't reach Claude. Check your internet connection."}), 503
    except Exception:
        return jsonify({"error": "Something went wrong. Please try again."}), 500

    # Reassemble in original PERSONAS order
    roasts = [results[p["key"]] for p in PERSONAS]

    sub_scores = _compute_sub_scores(roasts)
    survival_score = sum(sub_scores.values())

    return jsonify({"roasts": roasts, "survival_score": survival_score, "sub_scores": sub_scores})


@app.route("/rescue", methods=["POST", "GET"])
def rescue():
    import traceback

    idea = (request.form.get("idea") or request.args.get("idea", "")).strip()
    roasts_summary = (request.form.get("roasts_summary") or request.args.get("roasts_summary", "")).strip()

    print("=== RESCUE CALLED ===")
    print("IDEA:", request.form.get('idea', 'EMPTY'))
    print("ROASTS:", request.form.get('roasts_summary', 'EMPTY')[:100])

    if not idea:
        return redirect("/")

    original_score = 0

    roasts_summary = roasts_summary or "Various concerns about market competition and differentiation."

    print("Roasts summary length:", len(roasts_summary))

    rescue_prompt = f"""Help this startup idea succeed.
Idea: {idea}
Expert feedback received:
{roasts_summary[:1500]}

Return ONLY this JSON (no markdown, no fences):
{{
  "stronger_idea": "A reframed, stronger version of their idea that directly addresses the main criticisms",
  "why_it_can_work": ["Genuine reason 1 this could succeed with real data", "Genuine reason 2", "Genuine reason 3"],
  "the_pivot": "If the original idea is weak, suggest a SPECIFIC concrete pivot — not generic advice, a real business model shift",
  "kill_the_competition": "Exactly how to beat the specific competitors the critics mentioned — tactical, not generic",
  "kill_metrics": ["Metric 1: specific number and what it proves in 30 days", "Metric 2: specific number and what it proves", "Metric 3: specific number and what it proves"],
  "validate_in_30_days": ["Specific action step 1", "Specific action step 2", "Specific action step 3", "Specific action step 4", "Specific action step 5"],
  "find_first_10_customers": "Exactly WHERE to find the first 10 customers (specific platforms, communities, events), WHAT to say to them, and HOW to close them",
  "unfair_advantage": "What unique advantage does this specific founder have that well-funded competitors cannot easily replicate",
  "dont_do_this": ["Specific mistake 1 that kills startups like this", "Specific mistake 2", "Specific mistake 3"],
  "target_customer": "Exactly who to sell to first — specific demographics, psychographics, and where to find them",
  "first_revenue": "How to make the first ₹10,000 from this idea — specific actions in sequence",
  "revised_survival_score": 65,
  "score_explanation": "Why the improved version scores higher than the original"
}}"""

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            system="You are a startup strategist who helps founders fix bad ideas. Respond in valid JSON only. No markdown, no code fences, no preamble.",
            messages=[{"role": "user", "content": rescue_prompt}],
        )
        raw = message.content[0].text.strip()
        print("RESCUE RAW (first 300 chars):", raw[:300])

        cleaned = clean_json_response(raw)
        print("CLEANED FINAL:", cleaned[:100] if cleaned else "EMPTY!")
        try:
            rescue_data = json.loads(cleaned)
        except json.JSONDecodeError as parse_err:
            print("RESCUE JSON PARSE ERROR:", parse_err)
            print("Cleaned text:", cleaned[:500])
            raise ValueError(f"Could not parse rescue response: {parse_err}")

        # Normalise list fields
        def _to_list(val, n):
            if isinstance(val, str):
                val = [ln.strip().lstrip("0123456789.-) ") for ln in val.splitlines() if ln.strip()]
            return (list(val) + [""] * n)[:n]

        rescue_data["why_it_can_work"] = _to_list(rescue_data.get("why_it_can_work", []), 3)
        rescue_data["kill_metrics"] = _to_list(rescue_data.get("kill_metrics", []), 3)
        rescue_data["validate_in_30_days"] = _to_list(rescue_data.get("validate_in_30_days", []), 5)
        rescue_data["dont_do_this"] = _to_list(rescue_data.get("dont_do_this", []), 3)
        rescue_data["revised_survival_score"] = max(
            0, min(100, int(rescue_data.get("revised_survival_score", 65)))
        )

    except anthropic.AuthenticationError:
        return render_template("rescue.html", error="Invalid API key. Check your ANTHROPIC_API_KEY.", idea=idea)
    except anthropic.RateLimitError:
        return render_template("rescue.html", error="Rate limit reached. Wait a moment and try again.", idea=idea)
    except anthropic.APIStatusError as e:
        print("RESCUE API STATUS ERROR:", e.status_code, e.message)
        return render_template("rescue.html", error=f"Claude API error (HTTP {e.status_code}). Try again shortly.", idea=idea)
    except anthropic.APIConnectionError:
        return render_template("rescue.html", error="Couldn't reach Claude. Check your internet connection.", idea=idea)
    except Exception as e:
        print("RESCUE UNEXPECTED ERROR:", e)
        traceback.print_exc()
        return render_template("rescue.html", error=f"Failed to generate battle plan: {e}", idea=idea)

    return render_template(
        "rescue.html",
        idea=idea,
        original_score=original_score,
        rescue=rescue_data,
    )


if __name__ == "__main__":
    app.run(debug=True)
