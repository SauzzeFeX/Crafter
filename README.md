# Crafter Bot

This project provides a Discord bot to manage crafting requests. Users can submit requests, and crafters can accept them and update their progress.

## Setup
1. Create a virtual environment and install dependencies:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
2. Copy `.env.example` to `.env` and fill in your Discord token and IDs.
3. Run the bot with:
   ```bash
   python bot.py
   ```

The `CRAFT_ROLE_ID` is the ID of the role whose members will receive direct messages when a new request is made. `LOG_CHANNEL_ID` is the channel where requests are logged.

