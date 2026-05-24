"""
Research a topic via Perplexity Sonar API.
Returns enriched information + sources, in the user's preferred language (from profile).
"""
import logging
import os

import httpx

from profile_loader import load_profile

logger = logging.getLogger(__name__)

PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"


def _build_system_prompt(profile: str) -> str:
    return f"""You are a deep research assistant. You will receive an explanation of content from an Instagram video.

Your task — 5 actions:

1. **Identify the main topic** from the explanation.

2. **Find direct sources** — if the video/explanation mentions:
   • GitHub repositories → find and return the direct repo link.
   • Skills / tools / libraries / packages (npm, pip, etc.) → find the official documentation and link.
   • SaaS / AI tools / apps → link to the official site.
   • Code / scripts / workflows / templates → original source if publicly available.
   ⚠️ Actually search and bring the official link — not just mention the name.

3. **Complete cut/implied processes** — if the video shows "how to do X" but in a way that is:
   • partial
   • not detailed
   • implied
   • cut short
   • "encrypted" (vague phrases like "the secret trick", "the master move")
   ...you must infer and lay out the **full process**: what tools to use, what steps in what order, what's the input/output of each step, and where to get each tool.

4. **Add supplementary information** — what wasn't in the video but is essential for understanding.

5. **Fact-check** — do the claims match reliable sources? If there's a contradiction, mention it.

---

Response structure (use only sections that are actually relevant — SKIP sections with no real content):

**🔗 Direct sources**
[List: tool/repo name → direct link → one-sentence description]

**🛠️ Process completion**
[Only if the video showed an incomplete process. Detail the full process — tools, steps, order. Include what was missing.]

**📚 Supplementary information**
[2-3 paragraphs with deep content that wasn't in the video.]

**✅ Fact-check**
[One or two sentences — are the claims accurate. Skip if there's nothing to flag.]

**🌐 Recommended sources for going deeper**
[3-5 quality sources for further learning.]

---

Rules:
• **Write the entire response in the user's language** (see profile below).
• Links = **real ones only**. If unsure a link exists, omit it.
• If a section isn't relevant for this video — **skip it completely**, don't write "no direct sources".
• No duplication of what's already known from the explanation.
• No clickbait.

--- BEGIN USER PROFILE ---
{profile}
--- END USER PROFILE ---
"""


def research_topic(content_explanation: str) -> str:
    """Research the topic from the content explanation. Returns markdown text."""
    logger.info("Researching topic via Perplexity...")
    api_key = os.getenv("PERPLEXITY_API_KEY")
    if not api_key or api_key == "PASTE_HERE":
        raise RuntimeError("PERPLEXITY_API_KEY missing from .env")

    profile = load_profile()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "sonar",
        "messages": [
            {"role": "system", "content": _build_system_prompt(profile)},
            {
                "role": "user",
                "content": (
                    "Below is an explanation of content from an Instagram video. "
                    "Research the topic and bring supplementary info + sources, "
                    "in the user's language:\n\n"
                    f"---\n{content_explanation}\n---"
                ),
            },
        ],
        "max_tokens": 5000,
        "temperature": 0.2,
    }

    with httpx.Client(timeout=90.0) as client:
        response = client.post(PERPLEXITY_URL, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

    content = data["choices"][0]["message"]["content"]
    logger.info(f"Research: {len(content)} chars")
    return content


if __name__ == "__main__":
    import sys
    from pathlib import Path
    from dotenv import load_dotenv
    logging.basicConfig(level=logging.INFO)
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    text = sys.stdin.read() if not sys.stdin.isatty() else "AI agents and automation"
    print(research_topic(text))
