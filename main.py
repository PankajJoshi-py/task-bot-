import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import asyncio
from datetime import datetime, timedelta
from keep_alive import keep_alive

# ============================================================
# CONFIG
# ============================================================
BOT_TOKEN        = os.environ.get("BOT_TOKEN")
TASKS_CHANNEL_ID = int(os.environ.get("TASKS_CHANNEL_ID", 0))
ADMIN_ROLE_ID    = int(os.environ.get("ADMIN_ROLE_ID", 0))
COOLDOWN_MINUTES = 5
# ============================================================

# Safety checks
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN missing in environment variables")

if TASKS_CHANNEL_ID == 0:
    raise ValueError("❌ TASKS_CHANNEL_ID not set")

if ADMIN_ROLE_ID == 0:
    raise ValueError("❌ ADMIN_ROLE_ID not set")

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

active_tasks = {}
user_cooldowns = {}
claim_locks = {}

# ── Helpers ──────────────────────────────────────────────────

def save_tasks():
    with open("tasks.json", "w") as f:
        json.dump({str(k): v for k, v in active_tasks.items()}, f, indent=2)

def load_tasks():
    global active_tasks
    if os.path.exists("tasks.json"):
        with open("tasks.json", "r") as f:
            active_tasks = {int(k): v for k, v in json.load(f).items()}

# ── Startup ──────────────────────────────────────────────────

@bot.event
async def on_ready():
    load_tasks()
    await tree.sync()
    print(f"✅ Bot online: {bot.user}")
    print(f"📋 Tasks loaded: {len(active_tasks)}")

# ── /task ────────────────────────────────────────────────────

@tree.command(name="task", description="Create task (Admin only)")
@app_commands.describe(
    title="Task name",
    reward="Reward",
    instructions="Instructions",
    link="Task link",
    time_limit="Minutes (0 = no expiry)"
)
async def create_task(interaction: discord.Interaction, title: str, reward: str, instructions: str, link: str = "N/A", time_limit: int = 0):

    admin_role = interaction.guild.get_role(ADMIN_ROLE_ID)
    if admin_role not in interaction.user.roles:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return

    channel = interaction.guild.get_channel(TASKS_CHANNEL_ID)
    if not channel:
        await interaction.response.send_message("❌ Tasks channel not found!", ephemeral=True)
        return

    embed = discord.Embed(title="🔥 New Task", color=discord.Color.orange())
    embed.add_field(name="📌 Task", value=title, inline=False)
    embed.add_field(name="💰 Reward", value=reward, inline=True)

    if time_limit > 0:
        expire_ts = int((datetime.utcnow() + timedelta(minutes=time_limit)).timestamp())
        embed.add_field(name="⏱ Expires", value=f"<t:{expire_ts}:R>", inline=True)

    embed.set_footer(text="React ✅ to claim")

    msg = await channel.send(embed=embed)
    await msg.add_reaction("✅")

    active_tasks[msg.id] = {
        "title": title,
        "reward": reward,
        "link": link,
        "instructions": instructions,
        "claimed": False,
        "claimed_by": None,
        "time_limit": time_limit
    }
    save_tasks()

    await interaction.response.send_message("✅ Task posted!", ephemeral=True)

    if time_limit > 0:
        await asyncio.sleep(time_limit * 60)
        if msg.id in active_tasks and not active_tasks[msg.id]["claimed"]:
            await msg.edit(embed=discord.Embed(title="⌛ Task Expired", color=discord.Color.red()))
            await msg.clear_reactions()
            del active_tasks[msg.id]
            claim_locks.pop(msg.id, None)
            save_tasks()

# ── Reaction Claim ───────────────────────────────────────────

@bot.event
async def on_reaction_add(reaction: discord.Reaction, user: discord.Member):

    if user.bot:
        return

    if str(reaction.emoji) != "✅":
        return

    msg_id = reaction.message.id
    if msg_id not in active_tasks:
        return

    if msg_id not in claim_locks:
        claim_locks[msg_id] = asyncio.Lock()

    async with claim_locks[msg_id]:
        task = active_tasks[msg_id]

        if task["claimed"]:
            await reaction.remove(user)
            return

        if user.id in user_cooldowns:
            elapsed = datetime.utcnow() - user_cooldowns[user.id]
            if elapsed < timedelta(minutes=COOLDOWN_MINUTES):
                await reaction.remove(user)
                return

        task["claimed"] = True
        task["claimed_by"] = str(user.id)
        user_cooldowns[user.id] = datetime.utcnow()
        save_tasks()

    embed = discord.Embed(title="✅ Claimed", color=discord.Color.green())
    embed.add_field(name="📌 Task", value=task["title"])
    embed.add_field(name="👤 User", value=user.mention)

    await reaction.message.edit(embed=embed)
    await reaction.message.clear_reactions()

    try:
        await user.send(f"🎉 You claimed: {task['title']}")
    except:
        pass

# ── Run ─────────────────────────────────────────────────────

keep_alive()
bot.run(BOT_TOKEN)
