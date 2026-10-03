"""
Send the final processed email via Resend.

The explanation and research come back from the LLMs as markdown
(headings, bold, lists, links). We convert them to HTML so the email
renders as a proper styled document — no raw `##` or `**` artifacts.

dir="auto" lets each block pick LTR/RTL by its actual text content.
"""
import base64
import html as html_lib
import logging
import os
import re
from typing import Sequence

import markdown as md_lib
import nh3
import resend

logger = logging.getLogger(__name__)


# Markdown features we want enabled. `extra` covers tables, fenced code,
# definition lists, footnotes, abbreviations. `sane_lists` keeps nested
# lists predictable. `nl2br` turns single line breaks into <br> so the
# LLM's mid-paragraph wrapping shows the way it was written.
_MD_EXTENSIONS = ["extra", "sane_lists", "nl2br"]

# The explanation/research/reason text is produced by LLMs from an untrusted
# video transcript, then embedded as HTML in the email. Sanitize to a small
# allow-list so no <script>, event handler, or javascript: URL can ride along.
_SAFE_TAGS = {
    "p", "br", "hr", "strong", "b", "em", "i", "u", "s", "blockquote",
    "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "a", "code", "pre", "span", "div",
    "table", "thead", "tbody", "tr", "th", "td",
}
_SAFE_ATTRS = {"a": {"href", "title"}}


def _markdown_to_html(text: str) -> str:
    """Convert markdown to HTML, falling back to escaped plain text on failure."""
    if not text:
        return ""
    try:
        rendered = md_lib.markdown(text, extensions=_MD_EXTENSIONS, output_format="html5")
        # nh3 drops disallowed tags/attributes and unsafe URL schemes.
        return nh3.clean(rendered, tags=_SAFE_TAGS, attributes=_SAFE_ATTRS)
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

    # The "[Reels]" tag is the stable string Gmail filters can match on,
    # so all messages from this pipeline land in one label automatically.
    relevance_mark = "✓" if relevant else "⏭"
    snippet = explanation.replace("\n", " ").strip()[:70]
    if len(explanation) > 70:
        snippet += "..."
    subject_line = f"[Reels] {relevance_mark} {snippet}"

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
            "subject": subject_line,
            "html": html,
        }
    )

    email_id = response.get("id", "unknown") if isinstance(response, dict) else getattr(response, "id", "unknown")
    logger.info(f"Email sent: {email_id}")
    return email_id


# --- Carousel email ---

_MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
_MD_MARKERS = re.compile(r"[#*`>]")


def _carousel_snippet(content: str) -> str:
    # A heading makes a poor subject line; prefer the first lines of prose.
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    prose = [ln for ln in lines if not ln.startswith("#")]
    text = _MD_MARKERS.sub("", " ".join(prose or lines))
    text = " ".join(text.split())
    return text[:70] + ("..." if len(text) > 70 else "")


def render_carousel_email(
    *,
    instagram_url: str,
    author: str,
    slide_count: int,
    content: str,
    structure: str,
    brief: str,
    research: str,
    relevant: bool,
    reason: str,
    image_cids: Sequence[str] = (),
) -> tuple[str, str]:
    """Build (subject, html) for a carousel email. Pure: no network, no env."""
    content_html = _markdown_to_html(content)
    structure_html = _markdown_to_html(structure)
    brief_html = _markdown_to_html(brief)
    research_html = _markdown_to_html(research)
    reason_html = _markdown_to_html(reason).strip()

    badge_bg = "#dcfce7" if relevant else "#f1f5f9"
    badge_color = "#15803d" if relevant else "#475569"
    # For a carousel the tag judges the topic only - the breakdown of how it
    # is built is useful even when the topic is not the reader's.
    badge_text = "✓ התוכן רלוונטי" if relevant else "⏭ התוכן לא רלוונטי"
    badge = (
        f'<span style="display:inline-block;background:{badge_bg};color:{badge_color};'
        f'padding:5px 14px;border-radius:999px;font-size:13px;font-weight:600;letter-spacing:0.2px;">'
        f"{badge_text}</span>"
    )

    # Keep the "[Reels]" prefix first: the owner's Gmail filter matches it.
    relevance_mark = "✓" if relevant else "⏭"
    handle = re.sub(r"[^\w.]", "", author)[:30]
    who = f"@{handle}: " if handle else ""
    subject_line = f"[Reels] 🎠 {relevance_mark} {who}{_carousel_snippet(content)}"
    subject_line = subject_line.replace("\r", " ").replace("\n", " ")

    slides_card = ""
    if image_cids:
        imgs = "".join(
            f'<img src="cid:{html_lib.escape(cid, quote=True)}" width="150" alt="slide {n}" '
            f'style="border-radius:8px;margin:0 4px 8px 0;border:1px solid #e2e8f0;">'
            for n, cid in enumerate(image_cids, start=1)
        )
        slides_card = f"""
  <!-- Slides card -->
  <div dir="auto" style="{_EMAIL_CSS['card']}">
    <h2 style="{_EMAIL_CSS['section_title']}">השקפים</h2>
    <div>{imgs}</div>
  </div>
"""

    brief_card = ""
    if brief.strip():
        brief_card = f"""
  <!-- Reproduction brief card -->
  <div dir="auto" style="{_EMAIL_CSS['card']}background:#f5f3ff;border:1px solid #c7d2fe;">
    <h2 style="{_EMAIL_CSS['section_title']}">בריף לשחזור - להעתקה לסוכן AI</h2>
    <div style="font-size:12px;color:#94a3b8;margin:-6px 0 14px;">אפשר להעתיק את החלק הזה כמו שהוא לסוכן שבונה קרוסלות.</div>
    <div dir="auto" class="r2e-content" style="{_EMAIL_CSS['content']}">{brief_html}</div>
  </div>
"""

    research_card = ""
    if research.strip():
        research_card = f"""
  <!-- Research card -->
  <div dir="auto" style="{_EMAIL_CSS['card']}">
    <h2 style="{_EMAIL_CSS['section_title']}">מחקר והרחבה</h2>
    <div dir="auto" class="r2e-content" style="{_EMAIL_CSS['content']}">{research_html}</div>
  </div>
"""

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
    <div style="{_EMAIL_CSS['label']}">Instagram Carousel</div>
    <a href="{html_lib.escape(instagram_url)}" style="{_EMAIL_CSS['link']}">{html_lib.escape(instagram_url)}</a>
    <div dir="auto" style="font-size:14px;color:#475569;margin-top:10px;">@{html_lib.escape(author)} · {int(slide_count)} שקפים</div>
    <div style="margin-top:14px;">{badge}</div>
    <div dir="auto" class="r2e-content" style="font-size:14px;color:#475569;margin-top:8px;line-height:1.55;">{reason_html}</div>
  </div>
{slides_card}
  <!-- Content card -->
  <div dir="auto" style="{_EMAIL_CSS['card']}">
    <h2 style="{_EMAIL_CSS['section_title']}">תוכן הקרוסלה</h2>
    <div dir="auto" class="r2e-content" style="{_EMAIL_CSS['content']}">{content_html}</div>
  </div>

  <!-- Structure card -->
  <div dir="auto" style="{_EMAIL_CSS['card']}">
    <h2 style="{_EMAIL_CSS['section_title']}">איך הקרוסלה בנויה</h2>
    <div dir="auto" class="r2e-content" style="{_EMAIL_CSS['content']}">{structure_html}</div>
  </div>
{brief_card}{research_card}
  <!-- Footer -->
  <div style="{_EMAIL_CSS['footer']}">
    נשלח אוטומטית · reels-to-email
  </div>

</div>

</body>
</html>"""
    return subject_line, html


def send_carousel_email(
    *,
    instagram_url: str,
    author: str,
    slide_count: int,
    content: str,
    structure: str,
    brief: str,
    research: str,
    relevant: bool,
    reason: str,
    images: Sequence[tuple[str, bytes, str]] = (),
) -> str:
    """Send a carousel email with the slides inline. Returns the Resend email ID.

    `images` is a sequence of (filename, data, media_type). If the send with
    attachments is refused by the server, it is retried once without them so
    the text still arrives.
    """
    _resend_setup()
    from_email = os.getenv("RESEND_FROM_EMAIL", "onboarding@resend.dev")
    target_email = os.getenv("TARGET_EMAIL")

    if not target_email or target_email == "PASTE_YOUR_EMAIL_HERE":
        raise RuntimeError("TARGET_EMAIL missing from .env")

    attachments = []
    total = 0
    for n, (filename, data, media_type) in enumerate(images, start=1):
        if total + len(data) > _MAX_ATTACHMENT_BYTES:
            break
        total += len(data)
        attachments.append(
            {
                "filename": filename,
                "content": base64.b64encode(data).decode("ascii"),
                "content_type": media_type,
                "content_id": f"slide-{n:02d}",
            }
        )
    if len(attachments) < len(images):
        logger.info("Attached %d of %d slide images (size cap)", len(attachments), len(images))

    fields = dict(
        instagram_url=instagram_url,
        author=author,
        slide_count=slide_count,
        content=content,
        structure=structure,
        brief=brief,
        research=research,
        relevant=relevant,
        reason=reason,
    )

    def _send(subject: str, html: str, with_attachments: list) -> object:
        payload = {
            "from": f"Reels Pipeline <{from_email}>",
            "to": [target_email],
            "subject": subject,
            "html": html,
        }
        if with_attachments:
            payload["attachments"] = with_attachments
        return resend.Emails.send(payload)

    logger.info("Sending carousel email (%d slide images attached)...", len(attachments))
    subject, html = render_carousel_email(**fields, image_cids=[a["content_id"] for a in attachments])
    try:
        response = _send(subject, html, attachments)
    except Exception as e:
        # Retry only when the server answered and refused. After a network error
        # or timeout the first email may have gone out, and a retry would duplicate it.
        refused = isinstance(e, resend.exceptions.ResendError) and getattr(e, "error_type", "") != "HttpClientError"
        if not attachments or not refused:
            raise
        logger.warning("Send with attachments was refused (%s) - retrying without them", type(e).__name__)
        subject, html = render_carousel_email(**fields, image_cids=())
        response = _send(subject, html, [])

    email_id = response.get("id", "unknown") if isinstance(response, dict) else getattr(response, "id", "unknown")
    logger.info(f"Email sent: {email_id}")
    return email_id


# --- Alert email ---

# A separate monitoring job searches the inbox for this exact literal; do not reword it.
_ALERT_SUBJECT_PREFIX = "⚠️ reels-to-email ALERT: "
_ALERT_SUBJECT_DETAIL_MAX = 150


def send_alert_email(detail: str, lines: Sequence[str]) -> str | None:
    """Send a short alert email. Returns the Resend email ID, or None on ANY failure.

    Never raises: it runs while the bot is already unhealthy, and an alert
    failure must not take anything else down with it.
    """
    try:
        _resend_setup()
        from_email = os.getenv("RESEND_FROM_EMAIL", "onboarding@resend.dev")
        target_email = os.getenv("TARGET_EMAIL")
        if not target_email or target_email == "PASTE_YOUR_EMAIL_HERE":
            raise RuntimeError("TARGET_EMAIL missing")

        clean_detail = str(detail).replace("\r", " ").replace("\n", " ")
        subject = _ALERT_SUBJECT_PREFIX + clean_detail[:_ALERT_SUBJECT_DETAIL_MAX]

        paragraphs = "\n".join(
            f'    <p dir="auto" style="margin:0 0 12px;">{html_lib.escape(str(line))}</p>'
            for line in lines
        )
        html = f"""<!DOCTYPE html>
<html dir="auto">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>reels-to-email ALERT</title>
</head>
<body style="{_EMAIL_CSS['body']}">

<div dir="auto" style="{_EMAIL_CSS['wrapper']}">

  <div dir="auto" style="{_EMAIL_CSS['card']}">
{paragraphs}
  </div>

  <div style="{_EMAIL_CSS['footer']}">
    נשלח אוטומטית · reels-to-email
  </div>

</div>

</body>
</html>"""

        response = resend.Emails.send(
            {
                "from": f"Reels Pipeline <{from_email}>",
                "to": [target_email],
                "subject": subject,
                "html": html,
            }
        )
        email_id = response.get("id", "unknown") if isinstance(response, dict) else getattr(response, "id", "unknown")
        logger.info(f"Alert email sent: {email_id}")
        return email_id
    except Exception as e:
        logger.warning("Alert email was not sent (%s)", type(e).__name__)
        return None


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
