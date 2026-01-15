import discord
from discord.ext import commands
import os
import psycopg2
import asyncio

# =======================
# DATABASE
# =======================

DATABASE_URL = os.environ["DATABASE_URL"]

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS banned_words (
            guild_id BIGINT,
            word TEXT,
            PRIMARY KEY (guild_id, word)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_levels (
            guild_id BIGINT,
            user_id BIGINT,
            xp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            PRIMARY KEY (guild_id, user_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            guild_id BIGINT,
            channel_id BIGINT,
            user_id BIGINT,
            status TEXT DEFAULT 'open',
            PRIMARY KEY (channel_id)
        )
    """)

    conn.commit()
    cur.close()
    conn.close()

# =======================
# BOT CONFIG
# =======================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# =======================
# EVENTS
# =======================

@bot.event
async def on_ready():
    print(f"Bot connecté en tant que {bot.user}")
    init_db()
    try:
        synced = await bot.tree.sync()
        print(f"{len(synced)} slash commandes synchronisées")
    except Exception as e:
        print("Erreur sync slash:", e)

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return

    # mots bannis
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT word FROM banned_words WHERE guild_id = %s",
        (message.guild.id,)
    )
    banned_words = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()

    for word in banned_words:
        if word in message.content.lower():
            await message.delete()
            await message.channel.send(
                f"{message.author.mention} mot interdit détecté.",
                delete_after=5
            )
            return

    await handle_xp(message)
    await bot.process_commands(message)

# =======================
# XP SYSTEM
# =======================

async def handle_xp(message):
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO user_levels (guild_id, user_id, xp, level)
        VALUES (%s, %s, 10, 1)
        ON CONFLICT (guild_id, user_id)
        DO UPDATE SET xp = user_levels.xp + 10
    """, (message.guild.id, message.author.id))

    cur.execute("""
        SELECT xp, level FROM user_levels
        WHERE guild_id = %s AND user_id = %s
    """, (message.guild.id, message.author.id))

    xp, level = cur.fetchone()
    if xp >= level * 100:
        level += 1
        cur.execute("""
            UPDATE user_levels
            SET level = %s
            WHERE guild_id = %s AND user_id = %s
        """, (level, message.guild.id, message.author.id))
        await message.channel.send(
            f"🎉 {message.author.mention} est niveau {level} !"
        )

    conn.commit()
    cur.close()
    conn.close()

# =======================
# COMMANDS
# =======================

@bot.command()
@commands.has_permissions(manage_messages=True)
async def clear(ctx, amount: int):
    await ctx.channel.purge(limit=amount + 1)
    await ctx.send(f"{amount} messages supprimés", delete_after=5)

@bot.command()
@commands.has_permissions(manage_roles=True)
async def tempmute(ctx, member: discord.Member, duration: str, *, reason=None):
    muted_role = discord.utils.get(ctx.guild.roles, name="Muted")
    if not muted_role:
        muted_role = await ctx.guild.create_role(name="Muted")
        for channel in ctx.guild.channels:
            await channel.set_permissions(
                muted_role,
                send_messages=False,
                speak=False
            )

    unit = duration[-1]
    time = int(duration[:-1])

    seconds = time * {"m":60, "h":3600, "d":86400}.get(unit, 1)

    await member.add_roles(muted_role, reason=reason)
    await ctx.send(f"{member.mention} mute pour {duration}")

    await asyncio.sleep(seconds)
    await member.remove_roles(muted_role)

@bot.command()
@commands.has_permissions(manage_roles=True)
async def unmute(ctx, member: discord.Member):
    muted_role = discord.utils.get(ctx.guild.roles, name="Muted")
    if muted_role in member.roles:
        await member.remove_roles(muted_role)
        await ctx.send(f"{member.mention} démuté")

# =======================
# START BOT
# =======================

bot.run(os.environ["DISCORD_TOKEN"])
