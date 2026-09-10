"""
Localized bot status messages.

The pipeline's user-facing strings (welcome, status updates, success/failure)
live here. The Language section of config/profile.md selects between them.

To add a new language: copy one of the dicts below and translate.
"""
from typing import Dict

MESSAGES: Dict[str, Dict[str, str]] = {
    "en": {
        "welcome": (
            "👋 Hi! I'm your reels-to-email bot.\n\n"
            "Send me an Instagram Reel link (Share → Telegram → me), "
            "and I'll handle the rest:\n"
            "  • Download the video\n"
            "  • Transcribe the audio\n"
            "  • Write a full explanation\n"
            "  • Research the topic online\n"
            "  • Tag whether it's relevant to you\n"
            "  • Email everything to you\n\n"
            "Ready when you are 🚀"
        ),
        "not_instagram_url": (
            "🤔 I didn't see an Instagram link in that message.\n"
            "Send me a link to a Reel or post (instagram.com/reel/... or similar)."
        ),
        "got_it": "✅ Got it!\n⏳ Starting to process — I'll update you at each step.",
        "downloading": "⏬ Downloading audio...",
        "transcribing": "🗣️ Transcribing...",
        "explaining": "📝 Writing full explanation...",
        "researching": "🔎 Researching online...",
        "tagging": "🏷️ Tagging relevance...",
        "sending_email": "📨 Sending email...",
        "done": "✅ Done! Email sent. (Tagged: {relevance})",
        "relevant": "relevant",
        "not_relevant": "not relevant",
        "email_arrived": "📬 Email sent — check your inbox (also spam, the first time).",
        "rate_limited": "🚦 Too many requests right now — please try again a bit later.",
        "failed_generic": "⚠️ Something went wrong while processing. It has been logged — please try again later.",
        "failed_step": "❌ Failed at this step: {error_type}\n{error_message}",
        "processing_failed": (
            "⚠️ Processing failed.\n{error}\n\n"
            "If this keeps happening, check the logs for the full traceback."
        ),
    },
    "he": {
        "welcome": (
            "היי! 👋 אני הבוט של reels-to-email.\n\n"
            "שלחי לי קישור לרילס מאינסטגרם (Share → Telegram → אליי),\n"
            "ואני אטפל בכל השאר:\n"
            "  • אוריד את הוידאו\n"
            "  • אתמלל את האודיו\n"
            "  • אכתוב הסבר מלא\n"
            "  • אבצע מחקר אינטרנטי\n"
            "  • אסמן אם זה רלוונטי לך\n"
            "  • אשלח לך הכל למייל\n\n"
            "מוכן לעבודה 🚀"
        ),
        "not_instagram_url": (
            "🤔 לא זיהיתי קישור אינסטגרם בהודעה.\n"
            "שלחי לי קישור לרילס או פוסט (instagram.com/reel/... או דומה)."
        ),
        "got_it": "✅ קיבלתי את הקישור!\n⏳ מתחיל לעבד — אעדכן אותך בכל שלב.",
        "downloading": "⏬ מוריד את האודיו...",
        "transcribing": "🗣️ מתמלל...",
        "explaining": "📝 כותב הסבר מלא...",
        "researching": "🔎 מבצע מחקר אינטרנטי...",
        "tagging": "🏷️ מסווג רלוונטיות...",
        "sending_email": "📨 שולח מייל...",
        "done": "✅ סיימתי! המייל נשלח. (תיוג: {relevance})",
        "relevant": "רלוונטי",
        "not_relevant": "לא רלוונטי",
        "email_arrived": "📬 המייל בדרך אליך — בדקי את התיבה (כולל ספאם בפעם הראשונה).",
        "rate_limited": "🚦 יותר מדי בקשות כרגע — נסי שוב עוד קצת.",
        "failed_generic": "⚠️ משהו השתבש בעיבוד. זה נרשם בלוג — נסי שוב מאוחר יותר.",
        "failed_step": "❌ משהו נכשל בשלב הזה: {error_type}\n{error_message}",
        "processing_failed": (
            "⚠️ העיבוד נכשל.\n{error}\n\n"
            "אם זה ממשיך — בדוק את הלוגים."
        ),
    },
}


def t(key: str, language: str = "en", **kwargs) -> str:
    """Translate a message key. Falls back to English if the language is missing."""
    lang_dict = MESSAGES.get(language, MESSAGES["en"])
    template = lang_dict.get(key) or MESSAGES["en"].get(key) or key
    if kwargs:
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError):
            return template
    return template
