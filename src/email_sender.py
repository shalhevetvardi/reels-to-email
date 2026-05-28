"""
Send the final processed email via Resend.

The explanation and research come back from the LLMs as markdown
(headings, bold, lists, links). We convert them to HTML so the email
renders as a proper styled document — no raw `##` or `**` artifacts.

dir="auto" lets each block pick LTR/RTL by its actual text content.
"""
import html as html_lib
import logging
import os

import markdown as md_lib
import resend

logger = logging.getLogger(__name__)


# Markdown features we want enabled. `extra` covers tables, fenced code,
# definition lists, footnotes, abbreviations. `sane_lists` keeps nested
# lists predictable. `nl2br` turns single line breaks into <br> so the
# LLM's mid-paragraph wrapping shows the way it was written.
_MD_EXTENSIONS = ["extra", "sane_lists", "nl2br"]


def _markdown_to_html(text: str) -> str:
    """Convert markdown to HTML, falling back to escaped plain text on failure."""
    if not text:
        return ""
    try:
        return md_lib.markdown(text, extensions=_MD_EXTENSIONS, output_format="html5")
    except Exception as e:
        logger.warning(f"Markdown conversion failed, falling back to plain: {e}")
        return html_lib.escape(text).replace("\n", "<br>")


def _resend_setup() -> None:
    resend.api_key = os.getenv("RESEND_API_KEY")


# Inline CSS only — most mail clients strip <style> tags.
# Designed for readability in both RTL (Hebrew) and LTR (English) — dir="auto".
_EMAIL_CSS = {
    "body": "margin:0;padding:0;background:#f6f7f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;",
    "wrapper": "max-width:680px;margin:0 auto;padding:32px 20px;color:#1f2937;",
    "card": "background:#ffffff;border-radius:14px;padding:28px;margin-bottom:18px;box-shadow:0 1px 3px rgba(15,23,42,0.06),0 1px 2px rgba(15,23,42,0.04);",
    "label": "font-size:11px;letter-spacing:0.6px;text-transform:uppercase;color:#94a3b8;margin-bottom:8px;font-weight:600;",
    "link": "color:#4f46e5;text-decoration:none;font-size:14px;word-break:break-all;",
    "section_title": "font-size:18px;font-weight:700;color:#0f172a;margin:0 0 16px;padding-bottom:10px;border-bottom:1px solid #e2e8f0;",
    "content": "font-size:15px;line-height:1.75;color:#1f2937;",
    "footer": "text-align:center;font-size:12px;color:#94a3b8;margin-top:24px;",
}

# Per-element styling inside the rendered markdown — applied via a tiny
# scoped <style> block. Some clients (Gmail web) honor it; Apple Mail does.
_CONTENT_STYLE_BLOCK = """<style>
.r2e-content h1, .r2e-content h2, .r2e-content h3, .r2e-content h4 {
  color: #0f172a; line-height: 1.35; margin: 22px 0 10px;
}
.r2e-content h1 { font-size: 22px; font-weight: 700; }
.r2e-content h2 { font-size: 19px; font-weight: 700; }
.r2e-content h3 { font-size: 16px; font-weight: 600; }
.r2e-content h4 { font-size: 15px; font-weight: 600; color: #334155; }
.r2e-content p { margin: 0 0 14px; }
.r2e-content strong { color: #0f172a; font-weight: 700; }
.r2e-content em { color: #334155; font-style: italic; }
.r2e-content a { color: #4f46e5; text-decoration: underline; word-break: break-all; }
.r2e-content ul, .r2e-content ol { margin: 0 0 16px; padding-inline-start: 22px; }
.r2e-content li { margin: 6px 0; }
.r2e-content blockquote {
  margin: 14px 0; padding: 10px 16px; border-inline-start: 3px solid #c7d2fe;
  background: #f5f3ff; color: #374151; border-radius: 4px;
}
.r2e-content code {
  background: #f1f5f9; padding: 2px 6px; border-radius: 4px;
  font-family: ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;
  font-size: 13.5px;
}
.r2e-content pre {
  background: #0f172a; color: #e2e8f0; padding: 14px 16px;
  border-radius: 8px; overflow-x: auto; font-size: 13px;
}
.r2e-content pre code { background: transparent; padding: 0; color: inherit; }
.r2e-content hr { border: 0; border-top: 1px solid #e2e8f0; margin: 22px 0; }
.r2e-content table { border-collapse: collapse; margin: 14px 0; width: 100%; }
.r2e-content th, .r2e-content td { border: 1px solid #e2e8f0; padding: 8px 12px; text-align: start; }
.r2e-content th { background: #f8fafc; font-weight: 700; }
</style>"""


def send_email(
    instagram_url: str,
    explanation: str,
    research: str,
    relevant: bool,
    reason: str,
) -> str:
    """Send the final email. Returns the Resend email ID."""
    _resend_setup()
    from_email = os.getenv("RESEND_FROM_EMAIL", "onboarding@resend.dev")
    target_email = os.getenv("TARGET_EMAIL")

    if not target_email or target_email == "PASTE_YOUR_EMAIL_HERE":
        raise RuntimeError("TARGET_EMAIL missing from .env")

    explanation_html = _markdown_to_html(explanation)
    research_html = _markdown_to_html(research)
    reason_html = _markdown_to_html(reason).strip()

    badge_bg = "#dcfce7" if relevant else "#f1f5f9"
    badge_color = "#15803d" if relevant else "#475569"
    badge_text = "✓ רלוונטי" if relevant else "⏭ לא רלוונטי"
    badge = (
        f'<span style="display:inline-block;background:{badge_bg};color:{badge_color};'
        f'padding:5px 14px;border-radius:999px;font-size:13px;font-weight:600;letter-spacing:0.2px;">'
        f"{badge_text}</span>"
    )

    subject_prefix = "✓" if relevant else "⏭"
    snippet = explanation.replace("\n", " ").strip()[:80]
    if len(explanation) > 80:
        snippet += "..."

    html = f"""<!DOCTYPE html>
<html dir="auto">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reels to Email</title>
{_CONTENT_STYLE_BLOCK}
</head>
<body style="{_EMAIL_CSS['body']}">

<div dir="auto" style="{_EMAIL_CSS['wrapper']}">

  <!-- Header card -->
  <div dir="auto" style="{_EMAIL_CSS['card']}">
    <div style="{_EMAIL_CSS['label']}">Instagram Reel</div>
    <a href="{html_lib.escape(instagram_url)}" style="{_EMAIL_CSS['link']}">{html_lib.escape(instagram_url)}</a>
    <div style="margin-top:14px;">{badge}</div>
    <div dir="auto" class="r2e-content" style="font-size:14px;color:#475569;margin-top:8px;line-height:1.55;">{reason_html}</div>
  </div>

  <!-- Explanation card -->
  <div dir="auto" style="{_EMAIL_CSS['card']}">
    <h2 style="{_EMAIL_CSS['section_title']}">תוכן הסרטון</h2>
    <div dir="auto" class="r2e-content" style="{_EMAIL_CSS['content']}">{explanation_html}</div>
  </div>

  <!-- Research card -->
  <div dir="auto" style="{_EMAIL_CSS['card']}">
    <h2 style="{_EMAIL_CSS['section_title']}">מחקר והרחבה</h2>
    <div dir="auto" class="r2e-content" style="{_EMAIL_CSS['content']}">{research_html}</div>
  </div>

  <!-- Footer -->
  <div style="{_EMAIL_CSS['footer']}">
    נשלח אוטומטית · reels-to-email
  </div>

</div>

</body>
</html>"""

    logger.info(f"Sending email to {target_email}...")
    response = resend.Emails.send(
        {
            "from": f"Reels Pipeline <{from_email}>",
            "to": [target_email],
            "subject": f"{subject_prefix} {snippet}",
            "html": html,
        }
    )

    email_id = response.get("id", "unknown") if isinstance(response, dict) else getattr(response, "id", "unknown")
    logger.info(f"Email sent: {email_id}")
    return email_id


if __name__ == "__main__":
    from pathlib import Path
    from dotenv import load_dotenv
    logging.basicConfig(level=logging.INFO)
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    print(send_email(
        instagram_url="https://www.instagram.com/reel/TEST/",
        explanation="## כותרת לדוגמה\n\nזה תוכן עם **מודגש** ו-[קישור](https://example.com).\n\n- פריט 1\n- פריט 2",
        research="**מקור:** [GitHub](https://github.com/x)\n\nתיאור משלים.",
        relevant=True,
        reason="זה רלוונטי כי...",
    ))
