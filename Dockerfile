# Dockerfile — how a container platform (Render, for the bot's free web service) builds and runs the bot.

FROM python:3.12-slim

# Install ffmpeg — required by yt-dlp to extract audio from Instagram videos
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory inside the container
WORKDIR /app

# Install Python dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy the rest of the project
COPY . .

# Least privilege: run as a non-root user. It only needs to read /app and
# write temporary audio (removed after each run).
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

# One image, two ways to receive messages (src/webhook_mode.py):
#  - TELEGRAM_WEBHOOK_SECRET set: webhook mode. The bot listens on $PORT (Render sets it) and
#    Telegram's request is what wakes a sleeping free web service. Needs a public https address
#    (RENDER_EXTERNAL_URL, or TELEGRAM_WEBHOOK_BASE_URL).
#  - Not set: long-polling (outbound only, no port). For a long-lived process and for local use.
CMD ["python", "src/main.py"]
