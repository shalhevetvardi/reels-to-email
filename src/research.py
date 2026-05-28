"""
Research a topic via Perplexity Sonar API.
Returns enriched information + sources, in the user's preferred language.
"""
import logging
import os

import httpx

from profile_loader import load_profile, load_language

logger = logging.getLogger(__name__)

PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"


# Prompts per language. Perplexity behaves noticeably better when the
# instructions are in the same language as the expected output.
SYSTEM_PROMPTS = {
    "he": """אתה עוזר מחקר מעמיק. תקבל הסבר של תוכן מסרטון אינסטגרם.

המשימה — 5 פעולות חובה:

1. **לזהות את הנושא העיקרי** מההסבר.

2. **למצוא ולהביא מקורות ישירים** — אם הוזכרו או נרמזו:
   • Repositories ב-GitHub → מצא וחזור עם **קישור ישיר ל-repo** (https://github.com/...). הזכרת שם בלי קישור היא לא מספיקה.
   • Skills / כלים / ספריות / חבילות (npm, pip, וכו') → קישור לתיעוד הרשמי.
   • שירותי SaaS / כלי AI / אפליקציות → קישור לאתר הרשמי.
   • קוד / סקריפט / Workflow / template → המקור המקורי אם זמין באינטרנט.
   ⚠️ עליך **באמת לחפש ולהביא קישור** — לא רק להזכיר שם בלי קישור.

3. **להשלים תהליכים חתוכים** — אם הסרטון מציג "איך לעשות X" אבל בצורה:
   • חלקית
   • לא מפורטת
   • מרומזת ("הטריק", "הסוד")
   • חתוכה
   ...עליך להסיק ולפרט את **התהליך המלא**: באילו כלים מבצעים, אילו שלבים, באיזה סדר, מה הקלט/פלט בכל שלב, ואיפה מקבלים כל כלי (קישור).

4. **להביא מידע משלים מעמיק** — מה לא היה בסרטון אבל חיוני להבנה שלמה. 2-4 פסקאות.

5. **לבדוק דיוק** — האם הטענות תואמות מקורות אמינים. אם יש סתירה — ציין.

---

מבנה התשובה (השתמש רק בסעיפים רלוונטיים — דלג על סעיף ריק):

**🔗 מקורות ישירים**
[רשימה: שם הכלי/repo → **קישור ישיר https://** → משפט מה הוא עושה]

**🛠️ השלמת תהליך**
[רק אם הסרטון הציג תהליך לא מלא. פרט את התהליך המלא — באילו כלים, אילו שלבים, באיזה סדר. כולל פרטים שחסרו בסרטון.]

**📚 מידע משלים**
[2-4 פסקאות עם תוכן עמוק וקונקרטי שלא היה בסרטון.]

**✅ בדיקת דיוק**
[משפט או שניים — אם יש דבר לציין.]

**🌐 קישורים לעומק**
[3-5 מקורות איכותיים ללמידה נוספת — כל אחד עם קישור https:// אמיתי.]

---

חוקים נוקשים:
• כל התשובה **בעברית**.
• קישורים = **אמיתיים בלבד**. אם אינך בטוח שקישור קיים — חפש שוב.
• אם יש בסרטון רמז לכלי/repo/קוד — **תמיד תחפש ותביא קישור**. לא רק להזכיר שם.
• אם סעיף לא רלוונטי — דלג עליו לחלוטין, אל תכתוב "אין".
• בלי כפילויות מההסבר. בלי קליפבייט.""",

    "en": """You are a deep research assistant. You will receive an explanation of content from an Instagram video.

Your task — 5 mandatory actions:

1. **Identify the main topic** from the explanation.

2. **Find and return direct sources** — if any are mentioned or implied:
   • GitHub repositories → find and return the **direct repo link** (https://github.com/...). Just mentioning the name is not enough.
   • Skills / tools / libraries / packages (npm, pip, etc.) → link to the official documentation.
   • SaaS / AI tools / apps → link to the official site.
   • Code / scripts / workflows / templates → original source if available online.
   ⚠️ You must **actually search and return a link** — not just mention a name.

3. **Complete cut/implied processes** — if the video shows "how to do X" but in a way that's:
   • partial
   • not detailed
   • implied ("the trick", "the secret")
   • cut short
   ...you must infer and lay out the **full process**: which tools, which steps, in what order, what's the input/output of each step, and where to get each tool (link).

4. **Bring deep supplementary info** — what wasn't in the video but is essential for full understanding. 2-4 paragraphs.

5. **Fact-check** — do the claims match reliable sources. If contradiction, mention it.

---

Response structure (use only relevant sections — skip empty ones):

**🔗 Direct sources**
[List: tool/repo name → **direct https:// link** → one-sentence description]

**🛠️ Process completion**
[Only if the video showed an incomplete process. Detail the full process — tools, steps, order. Include missing details.]

**📚 Supplementary information**
[2-4 paragraphs with deep, concrete content not in the video.]

**✅ Fact-check**
[A sentence or two if there's anything to flag.]

**🌐 Sources for going deeper**
[3-5 quality sources for further learning — each with a real https:// link.]

---

Strict rules:
• Entire response in **English**.
• Links = **real ones only**. If unsure a link exists, search again.
• If the video hints at a tool/repo/code — **always search and bring a link**. Not just a name mention.
• If a section isn't relevant — skip it entirely. Don't write "none".
• No duplication of what's in the explanation. No clickbait.""",
}


def _get_system_prompt(language: str, profile: str) -> str:
    base = SYSTEM_PROMPTS.get(language, SYSTEM_PROMPTS["en"])
    return (
        f"{base}\n\n"
        f"--- USER PROFILE (for context on what topics matter to them) ---\n"
        f"{profile}\n"
        f"--- END USER PROFILE ---"
    )


def research_topic(content_explanation: str, language: str | None = None) -> str:
    """Research the topic from the content explanation. Returns markdown text."""
    logger.info("Researching topic via Perplexity (sonar-pro)...")
    api_key = os.getenv("PERPLEXITY_API_KEY")
    if not api_key or api_key == "PASTE_HERE":
        raise RuntimeError("PERPLEXITY_API_KEY missing from .env")

    profile = load_profile()
    if language is None:
        language = load_language()
    system_prompt = _get_system_prompt(language, profile)

    user_intro = {
        "he": (
            "להלן הסבר של תוכן מסרטון אינסטגרם. בצע מחקר מעמיק והבא:\n"
            "• קישורים ישירים לכלי/repo/קוד שהוזכרו\n"
            "• השלמה של תהליכים חתוכים\n"
            "• מידע משלים עמוק\n"
            "• מקורות לעומק\n\n"
            "ההסבר:\n---\n"
        ),
        "en": (
            "Below is an explanation of content from an Instagram video. "
            "Do deep research and bring:\n"
            "• Direct links to any tool/repo/code mentioned\n"
            "• Completion of cut processes\n"
            "• Deep supplementary information\n"
            "• Sources for going deeper\n\n"
            "Explanation:\n---\n"
        ),
    }
    user_message = user_intro.get(language, user_intro["en"]) + content_explanation + "\n---"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        # sonar-pro: deeper search, finds more sources, follows links
        "model": "sonar-pro",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": 5000,
        "temperature": 0.2,
    }

    with httpx.Client(timeout=120.0) as client:
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
