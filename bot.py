import os
import uuid
from datetime import datetime
from dotenv import load_dotenv
import discord
from discord.ext import commands

load_dotenv()
TOKEN           = os.getenv('DISCORD_TOKEN')
CRAFT_ROLE_ID   = int(os.getenv('CRAFT_ROLE_ID', '0'))
LOG_CHANNEL_ID  = int(os.getenv('LOG_CHANNEL_ID', '0'))

# Intents
intents = discord.Intents.default()
intents.message_content = True
intents.members         = True

bot = commands.Bot(command_prefix='!', intents=intents)

# Per-request stores
bot.request_info:     dict[str, dict]      = {}  # request_id -> {'user','item','notes'}
bot.pending_requests: dict[str, discord.User] = {}  # request_id -> crafter
bot.log_messages:     dict[str, discord.Message] = {}  # request_id -> log embed msg
bot.dm_messages:      dict[str, discord.Message] = {}  # request_id -> requester DM embed
bot.crafter_messages: dict[str, discord.Message] = {}  # request_id -> crafter-DM msg

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

def choose_color(status: str) -> discord.Color:
    if status == "Pending":
        return discord.Color.blue()
    if status in ("Accepted", "In Progress"):
        return discord.Color.gold()
    if status == "Ready for Pickup":
        return discord.Color.green()
    return discord.Color.light_gray()

def make_log_embed(rid: str, user: discord.User, item: str, notes: str, status: str, accepter: discord.User = None):
    e = discord.Embed(
        title=f"🔨 Craft Request {rid}",
        color=choose_color(status),
        timestamp=datetime.utcnow()
    )
    e.add_field(name="User", value=user.mention, inline=True)
    e.add_field(name="Item", value=item, inline=True)
    if notes:
        e.add_field(name="Notes", value=notes, inline=False)
    e.add_field(name="Status", value=status, inline=True)
    if accepter:
        e.add_field(name="Accepted by", value=accepter.mention, inline=True)
    e.set_footer(text=f"Request ID: {rid}")
    return e

def make_dm_embed(rid: str, status: str, accepter: discord.User = None, note: str = None):
    titles = {
        "Accepted":         "🪵 Crafting Accepted",
        "In Progress":      "⛏️ Craft In Progress",
        "Ready for Pickup": "🎁 Craft Ready"
    }
    descs = {
        "Accepted":         f"{accepter.mention} accepted and is on it!",
        "In Progress":      f"{accepter.mention} is now working on your request.",
        "Ready for Pickup": f"{accepter.mention} has completed it—ready for pickup!"
    }
    e = discord.Embed(
        title=titles.get(status, "🪵 Request Update"),
        description=descs.get(status, ""),
        color=choose_color(status),
        timestamp=datetime.utcnow()
    )
    if note:
        e.add_field(name="Crafter's Note", value=note, inline=False)
    e.set_footer(text=f"Request ID: {rid}")
    return e

class AcceptView(discord.ui.View):
    def __init__(self, request_id: str):
        super().__init__(timeout=None)
        self.request_id = request_id

    @discord.ui.button(label="🎁 Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        info    = bot.request_info.get(self.request_id)
        log_msg = bot.log_messages.get(self.request_id)
        if not info or not log_msg:
            return await interaction.response.send_message("❗ Data missing.", ephemeral=True)
        if self.request_id in bot.pending_requests:
            existing = bot.pending_requests[self.request_id]
            return await interaction.response.send_message(
                f"❗ Already accepted by {existing.mention}.", ephemeral=True
            )

        # record accepter
        bot.pending_requests[self.request_id] = interaction.user

        # update log embed -> Accepted
        await log_msg.edit(embed=make_log_embed(
            self.request_id, info['user'], info['item'], info['notes'],
            status="Accepted", accepter=interaction.user
        ))

        # DM requester (store message)
        try:
            dm_chan = await info['user'].create_dm()
            dm_msg  = await dm_chan.send(embed=make_dm_embed(self.request_id, "Accepted", interaction.user))
            bot.dm_messages[self.request_id] = dm_msg
        except discord.Forbidden:
            pass

        # confirm & swap to status buttons in crafter DM
        await interaction.response.send_message("🎁 Accepted; requester notified.", ephemeral=True)
        await interaction.message.edit(view=StatusView(self.request_id))
        bot.crafter_messages[self.request_id] = interaction.message

class StatusView(discord.ui.View):
    def __init__(self, request_id: str):
        super().__init__(timeout=None)
        self.request_id = request_id

    @discord.ui.button(label="⛏️ In Progress", style=discord.ButtonStyle.primary)
    async def in_progress(self, interaction: discord.Interaction, button: discord.ui.Button):
        await update_status(interaction, self.request_id, "In Progress")

    @discord.ui.button(label="🎁 Ready for Pickup", style=discord.ButtonStyle.success)
    async def ready(self, interaction: discord.Interaction, button: discord.ui.Button):
        # open modal for personal note
        await interaction.response.send_modal(CompletionModal(self.request_id))

async def update_status(interaction: discord.Interaction, rid: str, status: str, note: str = None):
    info        = bot.request_info.get(rid)
    log_msg     = bot.log_messages.get(rid)
    dm_msg      = bot.dm_messages.get(rid)
    crafter_msg = bot.crafter_messages.get(rid)
    accepter    = bot.pending_requests.get(rid)

    if not info or not log_msg:
        return await interaction.response.send_message("❗ Data missing.", ephemeral=True)
    if interaction.user != accepter:
        return await interaction.response.send_message("❗ Only the accepter can update.", ephemeral=True)

    # edit log embed
    new_log = make_log_embed(rid, info['user'], info['item'], info['notes'], status, accepter)
    if note:
        new_log.add_field(name="Crafter's Note", value=note, inline=False)
    await log_msg.edit(embed=new_log)

    # edit requester DM
    if dm_msg:
        try:
            await dm_msg.edit(embed=make_dm_embed(rid, status, accepter, note))
        except discord.Forbidden:
            pass

    # remove buttons from crafter DM on final
    if status == "Ready for Pickup" and crafter_msg:
        await crafter_msg.edit(view=None)

    await interaction.response.send_message(f"🎁 Status updated to **{status}**.", ephemeral=True)

class CompletionModal(discord.ui.Modal):
    def __init__(self, request_id: str):
        super().__init__(title="Completion Note")
        self.request_id = request_id
        self.note = discord.ui.TextInput(label="Personal note (optional)", style=discord.TextStyle.long, required=False)
        self.add_item(self.note)

    async def on_submit(self, interaction: discord.Interaction):
        await update_status(interaction, self.request_id, "Ready for Pickup", note=self.note.value)

class CraftModal(discord.ui.Modal, title="🪵 Crafting Request"):
    item    = discord.ui.TextInput(label="Item", placeholder="What item?")
    notes   = discord.ui.TextInput(label="Notes (optional)", style=discord.TextStyle.long, required=False)
    confirm = discord.ui.TextInput(label="Confirm In-Game Order", placeholder="Type YES", max_length=3)

    async def on_submit(self, interaction: discord.Interaction):
        if self.confirm.value.strip().lower() != "yes":
            return await interaction.response.send_message(
                "❗ Type **YES** to confirm you’ve placed the order in-game.", ephemeral=True
            )

        rid       = str(uuid.uuid4())[:8]
        user      = interaction.user
        item_val  = self.item.value
        notes_val = self.notes.value or ""
        bot.request_info[rid] = {'user': user, 'item': item_val, 'notes': notes_val}

        # post initial log embed
        embed = make_log_embed(rid, user, item_val, notes_val, status="Pending")
        log_chan = bot.get_channel(LOG_CHANNEL_ID)
        if log_chan:
            msg = await log_chan.send(embed=embed)
            bot.log_messages[rid] = msg

        # DM crafters
        guild = interaction.guild
        role  = guild.get_role(CRAFT_ROLE_ID) if guild else None
        sent = 0
        if role:
            for m in role.members:
                if m.bot: continue
                try:
                    dm = await m.create_dm()
                    await dm.send(embed=embed, view=AcceptView(rid))
                    sent += 1
                except discord.Forbidden:
                    pass

        await interaction.response.send_message(
            f"🎁 Sent to **{sent}** crafter(s). (ID: {rid})", ephemeral=True
        )

class CraftView(discord.ui.View):
    @discord.ui.button(label='Request Craft', style=discord.ButtonStyle.primary, emoji="🪵")
    async def request_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CraftModal())

@bot.command()
async def craft(ctx: commands.Context):
    """Submit a new crafting request."""
    embed = discord.Embed(
        title="Need something crafted?",
        description="Click below to submit your crafting request.",
        color=discord.Color.green(),
        timestamp=datetime.utcnow()
    )
    embed.set_footer(text="Craft System")
    await ctx.send(embed=embed, view=CraftView())

if __name__ == '__main__':
    if not TOKEN:
        raise RuntimeError('DISCORD_TOKEN not set')
    bot.run(TOKEN)

