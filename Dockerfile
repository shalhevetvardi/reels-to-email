# Dockerfile — how Railway (or any container platform) builds and runs the bot.

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

# The bot runs as a long-lived process and doesn't expose a port —
# it talks to Telegram via long-polling (outbound only).
CMD ["python", "src/main.py"]
