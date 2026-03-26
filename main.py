import discord
from discord.ext import commands
from discord import app_commands
import json
import os
import asyncio
from datetime import datetime, timedelta
from keep_alive import keep_alive

# ============================================================
#  CONFIG — Yahan apni real values daalo
# ============================================================
BOT_TOKEN        = os.environ.get("BOT_TOKEN")
TASKS_CHANNEL_ID = int(os.environ.get("TASKS_CHANNEL_ID", 0))
ADMIN_ROLE_ID    = int(os.environ.get("ADMIN_ROLE_ID", 0))
COOLDOWN_MINUTES = 5
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# active_tasks = { message_id: { ...task data... } }
active_tasks = {}

# Cooldown tracker = { user_id: last_claim_datetime }
user_cooldowns = {}

# Race condition fix: asyncio lock per task
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


# ── /task — Admin task banaye ─────────────────────────────────

@tree.command(name="task", description="Naya task post karo (Admin only)")
@app_commands.describe(
    title="Task ka naam",
    reward="Reward (e.g. $2 ya 50 points)",
    instructions="Kya karna hai",
    link="Task link (optional)",
    time_limit="Minutes mein expire time (0 = kabhi nahi)"
)
async def create_task(
    interaction: discord.Interaction,
    title: str,
    reward: str,
    instructions: str,
    link: str = "N/A",
    time_limit: int = 0
):
    # Admin check — role ID se (naam se nahi)
    admin_role = interaction.guild.get_role(ADMIN_ROLE_ID)
    if admin_role not in interaction.user.roles:
        await interaction.response.send_message("❌ Sirf Admin yeh command use kar sakta hai!", ephemeral=True)
        return

    # Channel ID se dhundho (naam se nahi)
    channel = interaction.guild.get_channel(TASKS_CHANNEL_ID)
    if not channel:
        await interaction.response.send_message("❌ Tasks channel nahi mila! TASKS_CHANNEL_ID check karo.", ephemeral=True)
        return

    embed = discord.Embed(title="🔥 New Task Available!", color=discord.Color.orange())
    embed.add_field(name="📌 Task", value=title, inline=False)
    embed.add_field(name="💰 Reward", value=reward, inline=True)
    if time_limit > 0:
        expire_ts = int((datetime.utcnow() + timedelta(minutes=time_limit)).timestamp())
        embed.add_field(name="⏱ Expires", value=f"<t:{expire_ts}:R>", inline=True)
    embed.set_footer(text="React ✅ to claim — First come first serve!")

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

    await interaction.response.send_message(f"✅ Task posted in {channel.mention}!", ephemeral=True)

    # Auto-expire logic
    if time_limit > 0:
        await asyncio.sleep(time_limit * 60)
        if msg.id in active_tasks and not active_tasks[msg.id]["claimed"]:
            expired_embed = discord.Embed(
                title="⌛ Task Expired",
                description=f"**{title}** — Koi claim nahi kar paya.",
                color=discord.Color.red()
            )
            await msg.edit(embed=expired_embed)
            await msg.clear_reactions()
            del active_tasks[msg.id]
            if msg.id in claim_locks:
                del claim_locks[msg.id]
            save_tasks()


# ── Reaction listener — first claim wins ─────────────────────

@bot.event
async def on_reaction_add(reaction: discord.Reaction, user: discord.Member):
    # Bot reactions ignore karo
    if user.bot:
        return

    # Sirf ✅ pe kaam karo
    if str(reaction.emoji) != "✅":
        return

    msg_id = reaction.message.id
    if msg_id not in active_tasks:
        return

    # ── Race condition fix: per-task asyncio Lock ──
    if msg_id not in claim_locks:
        claim_locks[msg_id] = asyncio.Lock()

    async with claim_locks[msg_id]:

        task = active_tasks[msg_id]

        # Dobara check — lock ke andar
        if task["claimed"]:
            await reaction.remove(user)
            return

        # Cooldown check
        if user.id in user_cooldowns:
            elapsed = datetime.utcnow() - user_cooldowns[user.id]
            wait = timedelta(minutes=COOLDOWN_MINUTES)
            if elapsed < wait:
                mins_left = int((wait - elapsed).total_seconds() // 60) + 1
                try:
                    await user.send(f"⏳ Cooldown active! **{mins_left} minute** baad try karo.")
                except discord.Forbidden:
                    pass
                await reaction.remove(user)
                return

        # ✅ CLAIM — lock ke andar instantly mark karo
        task["claimed"] = True
        task["claimed_by"] = str(user.id)
        active_tasks[msg_id] = task
        user_cooldowns[user.id] = datetime.utcnow()
        save_tasks()

    # Lock ke baahir — Discord updates (slow operations)
    claimed_embed = discord.Embed(title="✅ Task Claimed!", color=discord.Color.green())
    claimed_embed.add_field(name="📌 Task", value=task["title"], inline=False)
    claimed_embed.add_field(name="💰 Reward", value=task["reward"], inline=True)
    claimed_embed.add_field(name="👤 Claimed By", value=user.mention, inline=True)
    claimed_embed.set_footer(text="Yeh task ab available nahi hai.")

    await reaction.message.edit(embed=claimed_embed)
    await reaction.message.clear_reactions()

    # Winner ko DM
    dm_embed = discord.Embed(title="🎉 Tune Task Claim Kar Liya!", color=discord.Color.gold())
    dm_embed.add_field(name="📌 Task", value=task["title"], inline=False)
    dm_embed.add_field(name="🔗 Link", value=task["link"], inline=False)
    dm_embed.add_field(name="📝 Instructions", value=task["instructions"], inline=False)
    dm_embed.add_field(name="💰 Reward", value=task["reward"], inline=True)
    dm_embed.set_footer(text="Kaam complete karne ke baad admin ko update dena.")

    try:
        await user.send(embed=dm_embed)
    except discord.Forbidden:
        # DM band hai — channel mein warn karo
        await reaction.message.channel.send(
            f"{user.mention} tera DM band hai! Settings → Privacy → Allow DMs from server members ✅",
            delete_after=15
        )


# ── /deltask — Admin task hataye ─────────────────────────────

@tree.command(name="deltask", description="Task delete karo (Admin only)")
@app_commands.describe(message_id="Task message ka ID (right-click → Copy ID)")
async def delete_task(interaction: discord.Interaction, message_id: str):
    admin_role = interaction.guild.get_role(ADMIN_ROLE_ID)
    if admin_role not in interaction.user.roles:
        await interaction.response.send_message("❌ Sirf Admin!", ephemeral=True)
        return

    msg_id = int(message_id)
    active_tasks.pop(msg_id, None)
    claim_locks.pop(msg_id, None)
    save_tasks()

    channel = interaction.guild.get_channel(TASKS_CHANNEL_ID)
    if channel:
        try:
            msg = await channel.fetch_message(msg_id)
            await msg.delete()
        except Exception:
            pass

    await interaction.response.send_message("🗑️ Task delete kar diya!", ephemeral=True)


# ── /tasks — Available tasks list ────────────────────────────

@tree.command(name="tasks", description="Abhi kitne tasks available hain dekho")
async def list_tasks(interaction: discord.Interaction):
    unclaimed = [(mid, t) for mid, t in active_tasks.items() if not t["claimed"]]
    if not unclaimed:
        await interaction.response.send_message("📭 Abhi koi task available nahi.", ephemeral=True)
        return

    embed = discord.Embed(title="📋 Available Tasks", color=discord.Color.blue())
    for _, t in unclaimed:
        embed.add_field(name=f"📌 {t['title']}", value=f"💰 {t['reward']}", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ── Run ───────────────────────────────────────────────────────

keep_alive()
bot.run(BOT_TOKEN)
