"""
Send the final processed email via Resend.

The HTML uses dir="auto" so it works for both RTL (Hebrew, Arabic) and
LTR (English, etc.) content without changes — the browser/client picks
direction based on the actual text content.
"""
import html as html_lib
import logging
import os

import resend

logger = logging.getLogger(__name__)


def _escape_for_html(text: str) -> str:
    """HTML-escape text while preserving newlines as <br>."""
    return html_lib.escape(text).replace("\n", "<br>")


def _resend_setup() -> None:
    resend.api_key = os.getenv("RESEND_API_KEY")


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

    badge_text = "✅ Relevant" if relevant else "⏭️ Not relevant"
    badge_bg = "#dcfce7" if relevant else "#f1f5f9"
    badge_color = "#15803d" if relevant else "#475569"
    badge = (
        f'<span style="background:{badge_bg};color:{badge_color};'
        f'padding:4px 12px;border-radius:999px;font-size:13px;font-weight:600;">'
        f"{badge_text}</span>"
    )

    subject_prefix = "✅" if relevant else "⏭️"
    snippet = explanation.replace("\n", " ").strip()[:80]
    if len(explanation) > 80:
        snippet += "..."

    html = f"""<!DOCTYPE html>
<html dir="auto">
<head>
<meta charset="utf-8">
<title>Reels to Email</title>
</head>
<body style="margin:0;padding:0;background:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;">

<div dir="auto" style="max-width:720px;margin:0 auto;padding:32px 24px;line-height:1.7;color:#1e293b;">

  <!-- Header -->
  <div dir="auto" style="background:#fff;border-radius:12px;padding:20px;margin-bottom:20px;box-shadow:0 1px 3px rgba(0,0,0,0.05);">
    <div style="font-size:12px;color:#94a3b8;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px;">Instagram</div>
    <a href="{html_lib.escape(instagram_url)}" style="color:#6366f1;text-decoration:none;font-size:14px;word-break:break-all;">{html_lib.escape(instagram_url)}</a>
    <div style="margin-top:12px;">{badge}</div>
    <div dir="auto" style="font-size:14px;color:#475569;margin-top:6px;">{html_lib.escape(reason)}</div>
  </div>

  <!-- Explanation -->
  <div dir="auto" style="background:#fff;border-radius:12px;padding:24px;margin-bottom:20px;box-shadow:0 1px 3px rgba(0,0,0,0.05);">
    <h2 style="font-size:18px;color:#0f172a;margin:0 0 16px;padding-bottom:8px;border-bottom:1px solid #e2e8f0;">📝 Content</h2>
    <div dir="auto" style="font-size:15px;color:#1e293b;">{_escape_for_html(explanation)}</div>
  </div>

  <!-- Research -->
  <div dir="auto" style="background:#fff;border-radius:12px;padding:24px;margin-bottom:20px;box-shadow:0 1px 3px rgba(0,0,0,0.05);">
    <h2 style="font-size:18px;color:#0f172a;margin:0 0 16px;padding-bottom:8px;border-bottom:1px solid #e2e8f0;">🔎 Research</h2>
    <div dir="auto" style="font-size:15px;color:#1e293b;">{_escape_for_html(research)}</div>
  </div>

  <!-- Footer -->
  <div style="text-align:center;font-size:12px;color:#94a3b8;margin-top:24px;">
    Sent by reels-to-email pipeline 🤖
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
        explanation="Sample content explanation.",
        research="**Supplementary:**\nlorem ipsum.",
        relevant=True,
        reason="It matches the profile.",
    ))
