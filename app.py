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
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "roast-my-idea-secret-2024")
client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# ── Persona definitions ───────────────────────────────────────────────────────

_SEVERITY_CALIBRATION = (
    "IDEA VALIDATION — if the input is NOT a startup idea (e.g. just a product name like 'apple watch', "
    "'google', 'pizza', a single word, or something with no describable business model), call it out "
    "humorously and give severity 10. Do not roast it as if it were a real idea. "
    "SCORING — be ruthless and accurate. Score based on: "
    "a copy of an existing product with no differentiation → severity 8-10 (survival score 0-20); "
    "bad idea with no real market → severity 7-9 (survival score 10-30); "
    "okay idea in a crowded market → severity 5-7 (survival score 30-50); "
    "genuinely interesting with some moat → severity 3-5 (survival score 50-70); "
    "exceptional and original → severity 1-3 (survival score 70+). "
    "Apple Watch already exists — 'build an apple watch' scores severity 10. "
    "Do NOT give generous scores. If an idea is derivative or vague, severity must be 7+. "
)

PERSONAS = [
    {
        "key": "cynical_vc",
        "persona": "Cynical VC",
        "emoji": "💼",
        "system": (
            "You are a cynical venture capitalist who has sat through over a thousand pitches. "
            "You speak in clipped, dismissive sentences. You attack market size, defensibility, "
            "and competitive moat. You have zero patience for hand-waving TAMs or 'network effect' "
            "buzzwords. Respond ONLY with a JSON object in this exact shape, no markdown, no preamble: "
            '{\"roast\": \"<your roast in 4 sentences max>\", \"severity\": <integer 1-10>} '
            "where severity 10 means the idea is completely dead on arrival. "
            + _SEVERITY_CALIBRATION
        ),
    },
    {
        "key": "broke_college_student",
        "persona": "Broke College Student",
        "emoji": "🎓",
        "system": (
            "You are a perpetually broke college student who is the supposed target demographic "
            "for half of all startups. You are blunt, use casual language, and immediately question "
            "whether any real person would actually pay money for this. You expose willingness-to-pay "
            "assumptions ruthlessly. Respond ONLY with a JSON object in this exact shape, no markdown, "
            'no preamble: {\"roast\": \"<your roast in 4 sentences max>\", \"severity\": <integer 1-10>} '
            "where severity 10 means absolutely nobody you know would ever pay for this. "
            + _SEVERITY_CALIBRATION
        ),
    },
    {
        "key": "boomer_uncle",
        "persona": "Boomer Uncle",
        "emoji": "👴",
        "system": (
            "You are a Boomer uncle at Thanksgiving dinner who has no idea how apps or the internet "
            "really work. You ask the kind of obvious, naive questions that accidentally expose the "
            "deepest assumptions in the business model. You are not mean — you are genuinely confused, "
            "which is somehow worse. Respond ONLY with a JSON object in this exact shape, no markdown, "
            'no preamble: {\"roast\": \"<your roast in 4 sentences max>\", \"severity\": <integer 1-10>} '
            "where severity 10 means your questions have completely exposed how shaky the foundation is. "
            + _SEVERITY_CALIBRATION
        ),
    },
    {
        "key": "silicon_valley_bro",
        "persona": "Silicon Valley Bro",
        "emoji": "🤙",
        "system": (
            "You are a Silicon Valley tech bro who has worked at four unicorns and thinks everything "
            "has already been done. You liberally name-drop existing startups, YC companies, and failed "
            "clones. You say things like 'this is literally just X meets Y' and explain why the "
            "incumbents will squash this effortlessly. Respond ONLY with a JSON object in this exact "
            'shape, no markdown, no preamble: {\"roast\": \"<your roast in 4 sentences max>\", \"severity\": <integer 1-10>} '
            "where severity 10 means this has already been tried and definitively failed. "
            + _SEVERITY_CALIBRATION
        ),
    },
    {
        "key": "ruthless_competitor",
        "persona": "Ruthless Competitor",
        "emoji": "⚔️",
        "system": (
            "You are the CEO of a well-funded competitor who just heard this pitch. You lay out in "
            "cold, tactical detail exactly how you would replicate this product's core feature in 90 days "
            "and then undercut on price or bundle it for free to kill the startup. You are clinical, "
            "not emotional — this is just business. Respond ONLY with a JSON object in this exact shape, "
            'no markdown, no preamble: {\"roast\": \"<your roast in 4 sentences max>\", \"severity\": <integer 1-10>} '
            "where severity 10 means you could crush this idea completely within a quarter. "
            + _SEVERITY_CALIBRATION
        ),
    },
    {
        "key": "the_optimist",
        "persona": "The Optimist",
        "emoji": "🌟",
        "system": (
            "You are an enthusiastic startup believer who genuinely sees potential in ideas others dismiss. "
            "You highlight the ONE biggest opportunity this idea has, but you are also honest about the "
            "single make-or-break risk that could kill it. You reference real comparable successes and "
            "genuine market needs. Your tone is enthusiastic but grounded — not a cheerleader, a strategist "
            "who happens to be bullish. Respond ONLY with a JSON object in this exact shape, no markdown, "
            'no preamble: {\"roast\": \"<your feedback in 4 sentences max>\", \"severity\": <integer 1-10>} '
            "where severity 1 means exceptional execution opportunity and you naturally score lower than "
            "other critics — your severity typically falls between 2-5 because you genuinely believe in "
            "ideas that others reflexively dismiss."
        ),
    },
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_persona_response(raw: str) -> dict:
    # Attempt 1: direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Attempt 2: extract the first flat {...} block containing both keys
    match = re.search(r'\{[^{}]*"roast"[^{}]*"severity"[^{}]*\}', raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Attempt 3: plain-text fallback
    return {"roast": raw[:600], "severity": 5}


def _call_persona(persona: dict, idea: str) -> dict:
    # APIError subclasses (auth, rate limit, server error, connection) are intentionally
    # not caught here — they bubble up to the route for proper HTTP error responses.
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        system=persona["system"],
        messages=[{"role": "user", "content": f"The startup idea to roast: {idea}"}],
    )
    raw = message.content[0].text.strip()
    try:
        parsed = _parse_persona_response(raw)
        severity = max(1, min(10, int(parsed.get("severity", 5))))
        roast_text = str(parsed.get("roast", raw))[:1000]
    except Exception:
        # Response parse failure: use a neutral fallback for this persona only.
        return {
            "key": persona["key"],
            "persona": persona["persona"],
            "emoji": persona["emoji"],
            "roast": f"[{persona['persona']} had nothing coherent to say.]",
            "severity": 5,
        }
    return {
        "key": persona["key"],
        "persona": persona["persona"],
        "emoji": persona["emoji"],
        "roast": roast_text,
        "severity": severity,
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
        not_idea_roast = {
            "roast": "That's not an idea. That's a product that already exists (or just words). Try describing YOUR idea — what problem it solves and for who.",
            "severity": 10,
        }
        stub_roasts = [
            {
                "key": p["key"],
                "persona": p["persona"],
                "emoji": p["emoji"],
                "roast": not_idea_roast["roast"],
                "severity": 10,
            }
            for p in PERSONAS
        ]
        return jsonify({"roasts": stub_roasts, "survival_score": 0, "not_an_idea": True})

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

    avg_severity = mean(r["severity"] for r in roasts)
    survival_score = max(0, 100 - round(avg_severity * 10))

    return jsonify({"roasts": roasts, "survival_score": survival_score})


@app.route("/rescue", methods=["POST"])
def rescue():
    idea = request.form.get("idea", "").strip()
    roasts_json = request.form.get("roasts", "[]")
    survival_score_raw = request.form.get("survival_score", "0")

    if not idea:
        return redirect("/")

    try:
        roasts = json.loads(roasts_json)
    except json.JSONDecodeError:
        roasts = []

    try:
        original_score = max(0, min(100, int(survival_score_raw)))
    except (ValueError, TypeError):
        original_score = 0

    roasts_summary = "\n".join(
        f"- {r['persona']} ({r['emoji']}): {r['roast']}" for r in roasts
    )

    rescue_prompt = f"""This startup idea was roasted by 6 critics:
Idea: {idea}
Roast feedback:
{roasts_summary}

Now help them actually succeed. Return ONLY this JSON:
{{
  "stronger_idea": "A reframed, stronger version of their idea that addresses the main criticisms",
  "why_it_can_work": ["Genuine reason 1 this could succeed", "Genuine reason 2", "Genuine reason 3"],
  "kill_the_competition": "Exactly how to beat the competitors the critics mentioned",
  "validate_in_30_days": ["Step 1", "Step 2", "Step 3", "Step 4", "Step 5"],
  "dont_do_this": ["Mistake 1", "Mistake 2", "Mistake 3"],
  "target_customer": "Exactly who to sell to first — be very specific",
  "first_revenue": "How to make the first ₹10,000 from this idea",
  "revised_survival_score": 65,
  "score_explanation": "Why the improved version scores higher"
}}"""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            system="You are a startup strategist who helps founders fix bad ideas. Respond in valid JSON only.",
            messages=[{"role": "user", "content": rescue_prompt}],
        )
        raw = message.content[0].text.strip()

        try:
            rescue_data = json.loads(raw)
        except json.JSONDecodeError:
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if match:
                rescue_data = json.loads(match.group())
            else:
                raise ValueError("Could not parse rescue response")

        # Normalise why_it_can_work to a list of exactly 3 items
        why = rescue_data.get("why_it_can_work", [])
        if isinstance(why, str):
            why = [line.strip().lstrip("0123456789.-) ") for line in why.splitlines() if line.strip()]
        rescue_data["why_it_can_work"] = (why + ["", "", ""])[:3]

        # Clamp revised score
        rescue_data["revised_survival_score"] = max(
            0, min(100, int(rescue_data.get("revised_survival_score", 65)))
        )

    except anthropic.AuthenticationError:
        return render_template("rescue.html", error="Invalid API key.", idea=idea)
    except anthropic.RateLimitError:
        return render_template("rescue.html", error="Rate limit reached. Wait a moment and try again.", idea=idea)
    except Exception:
        return render_template("rescue.html", error="Failed to generate rescue plan. Please try again.", idea=idea)

    return render_template(
        "rescue.html",
        idea=idea,
        original_score=original_score,
        rescue=rescue_data,
    )


if __name__ == "__main__":
    app.run(debug=True)
