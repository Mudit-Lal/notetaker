# VPS Setup Guide — Notetaker

Complete guide to deploy the notetaker system from scratch on a VPS.

## 1. Choose a VPS Provider

Any of these work great for a single-user app like this:

| Provider | Cheapest Plan | Notes |
|----------|--------------|-------|
| **Hetzner** | ~$4/mo (CX22) | Best price/performance, EU and US regions |
| **DigitalOcean** | $6/mo (Basic) | Great UI, lots of tutorials |
| **Linode (Akamai)** | $5/mo (Nanode) | Reliable, good docs |
| **Vultr** | $6/mo (Cloud) | Many regions |

**Minimum specs:** 1 vCPU, 1 GB RAM, 20 GB SSD
**Recommended:** 2 vCPU, 2 GB RAM (more comfortable for Docker)
**OS:** Ubuntu 24.04 LTS

### Steps:
1. Create an account at your chosen provider
2. Create a new server/droplet with Ubuntu 24.04 LTS
3. Add your SSH key during creation (or set a root password)
4. Note the server's IP address

## 2. Initial Server Setup

SSH into your new server:

```bash
ssh root@YOUR_SERVER_IP
```

Run initial setup:

```bash
# Update system
apt update && apt upgrade -y

# Install Docker and essential tools
apt install -y docker.io docker-compose-v2 git ufw nginx certbot python3-certbot-nginx

# Set up firewall
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable

# Create a non-root user for running the app
adduser notetaker
usermod -aG docker notetaker

# Enable Docker to start on boot
systemctl enable docker
```

## 3. Create the Telegram Bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Choose a display name (e.g. "My Notes")
4. Choose a username (must end in `bot`, e.g. `mudit_notes_bot`)
5. **Copy the bot token** — you'll need this for `.env`

### Set bot commands (optional but recommended):

Send to @BotFather:
```
/setcommands
```

Then select your bot and paste:
```
recent - Show recent notes
search - Search your notes
actions - Show pending action items
domains - List notes by life area
note - View a specific note by ID
tags - Browse tag hierarchy
tag - Filter notes by tag
```

### Get your Telegram user ID (for security):

Search for **@userinfobot** on Telegram and send it any message. It will reply with your user ID. Save this — you can use it later to restrict bot access.

## 4. Get API Keys

### OpenAI (for Whisper transcription):
1. Go to https://platform.openai.com/api-keys
2. Create a new API key
3. Add credits ($5 is plenty — Whisper costs ~$0.006/min of audio)

### Anthropic (for Claude AI processing):
1. Go to https://console.anthropic.com/settings/keys
2. Create a new API key
3. Add credits ($5 minimum — each note costs ~$0.003 to process)

## 5. Deploy the Application

Switch to the notetaker user:

```bash
su - notetaker
```

Clone and configure:

```bash
cd ~
git clone YOUR_REPO_URL notetaker
cd notetaker

# Create environment file
cp .env.example .env
nano .env
```

Fill in the `.env` file:

```bash
# Paste your bot token from step 3
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz

# Paste your OpenAI key from step 4
OPENAI_API_KEY=sk-...

# Paste your Anthropic key from step 4
ANTHROPIC_API_KEY=sk-ant-...

# Generate a secure secret key (run the command below to get one)
API_SECRET_KEY=PASTE_OUTPUT_HERE

# Leave these as defaults
API_ACCESS_TOKEN_EXPIRE_MINUTES=1440
GOOGLE_CALENDAR_ENABLED=false
DATABASE_PATH=data/notetaker.db
API_HOST=0.0.0.0
API_PORT=8000
```

Generate the secret key:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Start the app:

```bash
docker compose up -d
```

Verify it's running:

```bash
# Check logs
docker compose logs -f

# Test the API
curl http://localhost:8000/api/v1/health
# Should return: {"status":"ok"}
```

Now open Telegram and send `/start` to your bot — it should respond!

## 6. Set Up Nginx Reverse Proxy + SSL

This gives you HTTPS access to the REST API at `https://notes.muditlal.com`.

### DNS Setup

Point your domain to the server. Add an **A record** in your DNS provider:

| Type | Name | Value |
|------|------|-------|
| A | notes | YOUR_SERVER_IP |

Wait a few minutes for DNS propagation, then verify:

```bash
dig notes.muditlal.com +short
# Should return your server's IP
```

### Nginx Config

An nginx config is included in the repo at `deploy/nginx/notetaker.conf`. Copy it to the server:

```bash
# Switch back to root
exit

# Copy the included config
cp /home/notetaker/notetaker/deploy/nginx/notetaker.conf /etc/nginx/sites-available/notetaker
```

Enable and get SSL:

```bash
ln -s /etc/nginx/sites-available/notetaker /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

# Get free SSL certificate (auto-configures HTTPS)
certbot --nginx -d notes.muditlal.com
```

Verify it works:

```bash
curl https://notes.muditlal.com/api/v1/health
# Should return: {"status":"ok"}
```

**Note:** If you don't have the domain pointed yet, the API is still accessible at `http://YOUR_SERVER_IP:8000`.

## 7. Register an API User

```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username": "mudit", "password": "your-secure-password"}'
```

This returns a JWT token you can use for API calls:

```bash
# Example: create a note via API
curl -X POST http://localhost:8000/api/v1/notes \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{"raw_text": "Test note from API"}'
```

## 8. MCP Server Setup (for Claude Desktop / Claude Code)

Add this to your Claude MCP settings (`~/.claude/mcp.json` or Claude Desktop settings):

```json
{
  "mcpServers": {
    "notetaker": {
      "command": "docker",
      "args": ["compose", "-f", "/home/notetaker/notetaker/docker-compose.yml", "run", "--rm", "mcp"],
      "env": {}
    }
  }
}
```

Or if running locally (not in Docker):

```json
{
  "mcpServers": {
    "notetaker": {
      "command": "python",
      "args": ["-m", "src.main", "mcp"],
      "cwd": "/path/to/notetaker",
      "env": {
        "DATABASE_PATH": "data/notetaker.db"
      }
    }
  }
}
```

## 9. Optional: Google Calendar Integration

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project
3. Enable the **Google Calendar API**
4. Go to **Credentials** → **Create Credentials** → **OAuth 2.0 Client ID**
5. Choose **Desktop app** as the application type
6. Download the JSON file and save it as `credentials.json` in the project root
7. Run the auth flow once (this opens a browser):
   ```bash
   python3 -c "from src.calendar.gcal import GoogleCalendarClient; c = GoogleCalendarClient(); c._get_service()"
   ```
8. This creates `token.json` — copy both files to the server
9. Set `GOOGLE_CALENDAR_ENABLED=true` in `.env`
10. Restart: `docker compose restart`

## 10. Maintenance

### Backups
```bash
# Manual backup
docker compose exec notetaker cp /app/data/notetaker.db /app/data/backup_$(date +%F).db

# Auto backup via cron (as notetaker user)
crontab -e
# Add: 0 3 * * * docker compose -f ~/notetaker/docker-compose.yml exec -T notetaker cp /app/data/notetaker.db /app/data/backup_$(date +\%F).db
```

### Updates
```bash
cd ~/notetaker
git pull
docker compose build
docker compose up -d
```

### View logs
```bash
docker compose logs --tail=100 -f
```

### Access the database directly
```bash
docker compose exec notetaker sqlite3 /app/data/notetaker.db
```

### Check tag vocabulary
```bash
docker compose exec notetaker sqlite3 /app/data/notetaker.db "SELECT name, usage_count FROM tags ORDER BY usage_count DESC;"
```

## Cost Estimate

For a single user taking ~10-20 notes/day:

| Service | Monthly Cost |
|---------|-------------|
| VPS (Hetzner CX22) | ~$4 |
| OpenAI Whisper (~5 min voice/day) | ~$1 |
| Anthropic Claude (~20 notes/day) | ~$2 |
| **Total** | **~$7/month** |
