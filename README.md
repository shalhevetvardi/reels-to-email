# reels-to-email

> An automated pipeline that turns saved Instagram Reels into rich, researched emails.

You send a Reel link to your Telegram bot → a few seconds later you get an email with:
- 📝 A full, edited explanation of the video (not a summary — the actual content)
- 🔎 Web research: official sources for any tools/repos mentioned + supplementary information
- 🏷️ A tag: "relevant to me" or "not relevant", based on your profile
- 🖼️ Carousels and image posts too: what the slides say, how the carousel is built, and a reproduction brief you can hand to an AI agent

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
│   ├── pipeline.py         # Orchestrates the stages (video flow + carousel flow)
│   ├── profile_loader.py   # Reads config/profile.md
│   ├── download.py         # Downloads audio with yt-dlp + ffmpeg
│   ├── transcribe.py       # Transcribes with OpenAI Whisper
│   ├── explain.py          # Writes explanation with Claude Sonnet
│   ├── research.py         # Researches topic via Perplexity Sonar
│   ├── tag.py              # Tags relevance with Claude Haiku
│   ├── instagram_post.py   # Probes posts, downloads carousel slide images
│   ├── carousel_analyze.py # Analyses carousel slides with Claude vision
│   ├── email_sender.py     # Sends final email via Resend
│   └── health.py           # Daily API-key self-check and failure alerts
├── tests/                  # pytest suite (no network)
└── data/                   # Temporary audio files and email previews (git-ignored)
```

---

## 🖼️ Carousels and image posts

Send a post link (`instagram.com/p/...`) the same way you send a Reel. If it turns out to be a carousel or a single-image post, the email contains:

- **The content** - what the slides say and teach
- **How it is built** - the hook, a slide-by-slide breakdown, visual language and copy style
- **A reproduction brief** - a copy-paste-ready brief for an AI agent that builds a similar carousel on your own topics
- **The slides** - shown inline in the email
- Research and a relevance tag, like for Reels

Reels are unchanged: they go straight to the video flow. If a post link turns out to be a single video, it also takes the video flow. Anything you type next to the link in Telegram is passed to the analysis as a note.

Optional settings (in `.env`):

| Variable | Default | What it does |
|---|---|---|
| `CAROUSEL_MODEL` | `claude-sonnet-5-5` | Claude model used for the slide analysis |
| `CAROUSEL_MAX_SLIDES` | `20` | Maximum number of slides analysed per post |

A carousel costs noticeably more than a reel, because the slides are read as images and the answer is long. In a test run, a 10-slide carousel used about 23k input tokens for the slides (sent once, then read from the prompt cache by the next two calls) and about 19k output tokens across the three calls - check your model's current pricing.

To preview a carousel email without sending it:

```bash
python run_once.py "https://www.instagram.com/p/.../" --no-send --note "focus on the hook"
```

The email is saved as a single file, `data/preview-<time>.html`, with the slides embedded.

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

> **Required:** set `ALLOWED_CHAT_IDS` to your own Telegram chat id (get it from `@userinfobot`).
> The bot is fail-closed - until this is set it ignores every message, including yours.

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

## 🩺 Self-check

The bot checks by itself, once a day, that the four API keys it depends on (OpenAI, Anthropic, Perplexity, Resend) are still accepted. The first check runs a minute after the bot starts.

- The checks are free, read-only calls (a model list, a domain list, and one deliberately empty request that is refused before anything runs). Nothing is spent and nothing is sent to the providers except the key itself.
- Only a definite refusal (or a key that is not set) raises an alert. A timeout or a provider outage is not treated as a bad key.
- When a key is refused you get a Telegram message in every allowlisted chat, and an email whose subject starts with `reels-to-email ALERT` (so a mailbox filter or a monitoring job can find it). If the refused key is the Resend one, only the Telegram message is sent. The same problem is not repeated more than once every 20 hours.
- When a link fails for a different reason, the bot checks the keys again right away; if they are fine it emails the error type only (never the message), at most once an hour.
- `HEALTH_CHECK_INTERVAL_HOURS` sets the interval (default `24`); `0` turns the self-check off.

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
| Alert email / Telegram message about a rejected key | The provider no longer accepts that API key (revoked or expired) | Create a new key at the provider, replace it in the host's variables (`.env` / Railway), then redeploy |
| Bot silent to everyone (incl. you) | `ALLOWED_CHAT_IDS` not set (fail-closed) | Add your Telegram chat id to `ALLOWED_CHAT_IDS` in `.env` / Railway, then redeploy |

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
