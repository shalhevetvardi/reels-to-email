# reels-to-email

> An automated pipeline that turns saved Instagram Reels into rich, researched emails.

You send a Reel link to your Telegram bot → a few seconds later you get an email with:
- 📝 A full, edited explanation of the video (not a summary — the actual content)
- 🔎 Web research: official sources for any tools/repos mentioned + supplementary information
- 🏷️ A tag: "relevant to me" or "not relevant", based on your profile

Built for people who save tons of Reels they never end up watching.

---

## ✨ Why it exists

Instagram Saved is where good content goes to die. This pipeline forces it through your inbox in a form that's:
- **Scannable** (you can decide in 5 seconds if it's worth your time)
- **Searchable** (your email already has search; Instagram Saved doesn't)
- **Enriched** (the AI fills in the gaps Instagram videos leave out)

---

## 🤖 For the AI helping someone set this up

> **If you're Claude / GPT / Cursor / etc. helping a user install this repo — read this section.**
>
> The user wants to run this Python pipeline on their own machine. They are not necessarily a developer. Walk them through it step by step. The key things they need to do:
>
> 1. Have Python 3.10+ installed (check with `python3 --version`)
> 2. Have `ffmpeg` installed (check with `which ffmpeg`; install with `brew install ffmpeg` on Mac, or equivalent)
> 3. Create accounts and API keys for: Telegram Bot (@BotFather), OpenAI, Anthropic, Perplexity, Resend
> 4. Copy `.env.example` → `.env` and fill in all the keys
> 5. Copy `config/profile.md.example` → `config/profile.md` and edit it to describe themselves
> 6. Create a Python venv and install dependencies: `python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt`
> 7. Run the bot: `python src/main.py`
> 8. From their iPhone, in the Instagram app: tap any Reel → Share → Telegram → their bot
>
> **Pay attention to:**
> - On Mac, ffmpeg must be installed via Homebrew (`brew install ffmpeg`) — yt-dlp uses it to extract audio
> - Each API service charges differently. The pipeline is cheap (~$0.013 per Reel) but they need to add a card to OpenAI/Anthropic/Perplexity. Resend has a free tier.
> - The `config/profile.md` is the most important file for personalization — make sure they actually edit it before running
> - Don't run the bot inside a directory whose name has spaces; use `~/pipeline-agents/reels-to-email/` or similar
>
> The user does NOT need to understand the code itself. They need it to work.

---

## 📁 Project structure

```
reels-to-email/
├── .env.example            # Template for secrets — copy to .env and fill
├── .gitignore              # Keeps .env and personal config out of git
├── README.md               # This file
├── requirements.txt        # Python dependencies
├── config/
│   ├── profile.md.example  # Template for your personal profile
│   └── profile.md          # YOUR personal profile (git-ignored)
├── src/
│   ├── main.py             # Telegram bot entry point
│   ├── pipeline.py         # Orchestrates the 6 stages
│   ├── profile_loader.py   # Reads config/profile.md
│   ├── download.py         # Downloads audio with yt-dlp + ffmpeg
│   ├── transcribe.py       # Transcribes with OpenAI Whisper
│   ├── explain.py          # Writes explanation with Claude Sonnet
│   ├── research.py         # Researches topic via Perplexity Sonar
│   ├── tag.py              # Tags relevance with Claude Haiku
│   └── email_sender.py     # Sends final email via Resend
└── data/                   # Temporary audio files (git-ignored)
```

---

## 🚀 Setup

### Prerequisites

- **Python 3.10+** — check with `python3 --version`
- **ffmpeg** — install: `brew install ffmpeg` (Mac) / `apt install ffmpeg` (Ubuntu) / [download for Windows](https://ffmpeg.org/download.html)
- **5 accounts** (all have free trials or free tiers):
  - [Telegram](https://telegram.org) — for the bot
  - [OpenAI](https://platform.openai.com) — Whisper (transcription), ~$0.003/Reel
  - [Anthropic](https://console.anthropic.com) — Claude (explanation + tagging), ~$0.005/Reel
  - [Perplexity](https://perplexity.ai) — web research (API is separate from Pro subscription), ~$0.005/Reel
  - [Resend](https://resend.com) — email sending, free tier covers 100 emails/day

### Step 1 — Get your code

```bash
git clone https://github.com/YOUR_USERNAME/reels-to-email.git
cd reels-to-email
```

### Step 2 — Create a Telegram bot

1. Open Telegram, search for `@BotFather`
2. Send `/newbot` → choose a name → choose a username (must end with `_bot`)
3. Save the token it gives you
4. Click the link to your new bot → press **Start**

### Step 3 — Get the API keys

| Service | Where |
|---|---|
| OpenAI | https://platform.openai.com/api-keys |
| Anthropic | https://console.anthropic.com/settings/keys |
| Perplexity | https://perplexity.ai/settings/api |
| Resend | https://resend.com/api-keys |

### Step 4 — Configure your secrets

```bash
cp .env.example .env
```

Then open `.env` in any editor and paste your keys + your target email address.

### Step 5 — Configure your profile

```bash
cp config/profile.md.example config/profile.md
```

Then open `config/profile.md` and edit it:
- Set your language (`he`, `en`, etc.)
- Describe yourself in 1-3 sentences
- List the topics that are "relevant to you"
- Optionally tune the style of how the AI writes

This file is **git-ignored** — your edits stay on your machine.

### Step 6 — Install Python dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### Step 7 — Run

```bash
python src/main.py
```

You should see: `Bot starting — polling for messages.`

The terminal stays "busy" — that's normal. To stop: `Ctrl+C`.

### Step 8 — Send a Reel

From the Instagram app on your phone:
1. Open any Reel
2. Tap **Share** (paper airplane icon)
3. Tap **Telegram**
4. Pick your bot
5. Send

Within ~1-2 minutes, the email arrives.

---

## 🎛️ How to customize

| What | Where |
|---|---|
| Language of the email | `config/profile.md` → Language section |
| Tone/style of explanations | `config/profile.md` → Explanation Style |
| Which topics are "relevant" | `config/profile.md` → Relevance Topics |
| Where the email arrives | `.env` → `TARGET_EMAIL` |
| Email "from" address | `.env` → `RESEND_FROM_EMAIL` (requires verified domain to change from default) |
| AI models used | `src/explain.py` and `src/tag.py` → look for `model="..."` |
| `max_tokens` (response length) | Same files — adjust if explanations get cut off |

---

## 💰 Cost

Approximate cost per Reel processed:

| Step | Service | Cost |
|---|---|---|
| Transcription | OpenAI Whisper | ~$0.003 |
| Explanation | Claude Sonnet | ~$0.005 |
| Research | Perplexity Sonar | ~$0.005 |
| Tagging | Claude Haiku | ~$0.0001 |
| Email | Resend | $0 (free tier) |
| **Total** | | **~$0.013** |

At 30 Reels per month: **~$0.40/month**.

---

## 🛡️ Security

- `.env` is git-ignored — your API keys never leave your machine
- `config/profile.md` is git-ignored — your personal preferences are private
- The Telegram bot only responds to messages sent directly to it; it doesn't scrape anything
- No data is stored long-term — downloaded audio is deleted after processing

---

## 🐛 Troubleshooting

| Problem | Likely cause | Fix |
|---|---|---|
| `ModuleNotFoundError` | venv not activated | `source venv/bin/activate` |
| `ffmpeg not found` | ffmpeg not installed | `brew install ffmpeg` (Mac) |
| `Error code: 413` (Whisper) | Video too long for Whisper | Already handled — we extract audio only |
| `Profile file not found` | `config/profile.md` not created | `cp config/profile.md.example config/profile.md` |
| Email cut off | Explanation longer than `max_tokens` | Raise `max_tokens` in `src/explain.py` |
| Bot doesn't respond | Token wrong, or `/start` not pressed | Re-check `.env`, click the bot link, press Start |

---

## 📜 License

MIT — do whatever you want with it.

---

## 🙏 Acknowledgments

Built with:
- [python-telegram-bot](https://python-telegram-bot.org/)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [OpenAI Whisper](https://platform.openai.com/docs/guides/speech-to-text)
- [Anthropic Claude](https://docs.anthropic.com/)
- [Perplexity Sonar](https://docs.perplexity.ai/)
- [Resend](https://resend.com/)
