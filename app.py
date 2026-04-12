import os
import json
import re
from statistics import mean
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Flask, render_template, request, jsonify
import anthropic
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# ── Persona definitions ───────────────────────────────────────────────────────

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
            "where severity 10 means the idea is completely dead on arrival."
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
            "where severity 10 means absolutely nobody you know would ever pay for this."
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
            "where severity 10 means your questions have completely exposed how shaky the foundation is."
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
            "where severity 10 means this has already been tried and definitively failed."
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
            "where severity 10 means you could crush this idea completely within a quarter."
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
    if len(idea) > 500:
        return jsonify({"error": "Idea must be 500 characters or less."}), 400

    try:
        with ThreadPoolExecutor(max_workers=5) as executor:
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


if __name__ == "__main__":
    app.run(debug=True)
