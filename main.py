import discord
from discord.ext import commands
import os
import psycopg2
import asyncio
from datetime import datetime, timedelta
from flask import Flask
import os

app = Flask(__name__)

@app.route("/")
def home():
    return "App OK sur Replit 🚀"

# Configuration de la base de données
DATABASE_URL = os.environ['DATABASE_URL']

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    # Table des mots bannis
    cur.execute('''
        CREATE TABLE IF NOT EXISTS banned_words (
            guild_id BIGINT,
            word TEXT,
            PRIMARY KEY (guild_id, word)
        )
    ''')
    # Table des infractions
    cur.execute('''
        CREATE TABLE IF NOT EXISTS infractions (
            guild_id BIGINT,
            user_id BIGINT,
            count INTEGER DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        )
    ''')
    # Table des niveaux
    cur.execute('''
        CREATE TABLE IF NOT EXISTS user_levels (
            guild_id BIGINT,
            user_id BIGINT,
            xp INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            PRIMARY KEY (guild_id, user_id)
        )
    ''')
    # Table des tickets
    cur.execute('''
        CREATE TABLE IF NOT EXISTS tickets (
            guild_id BIGINT,
            channel_id BIGINT,
            user_id BIGINT,
            status TEXT DEFAULT 'open',
            PRIMARY KEY (channel_id)
        )
    ''')
    conn.commit()
    cur.close()
    conn.close()

# Configuration du Bot
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    if bot.user:
        print(f'Bot connecté en tant que {bot.user.name}')
    init_db()
    try:
        synced = await bot.tree.sync()
        print(f"Synchronisation de {len(synced)} commande(s) slash")
    except Exception as e:
        print(f"Erreur de synchronisation : {e}")

# Logs et Configuration
@bot.tree.command(name="config", description="Configurer les modules du bot")
@commands.has_permissions(administrator=True)
async def config(interaction: discord.Interaction, module: str, status: bool):
    etat = "activé" if status else "désactivé"
    await interaction.response.send_message(f"Le module {module} a été {etat}. (Simulation)")

# Système de niveaux (XP)
async def handle_xp(message):
    conn = get_db_connection()
    cur = conn.cursor()
    guild_id = message.guild.id
    user_id = message.author.id

    cur.execute('INSERT INTO user_levels (guild_id, user_id, xp, level) VALUES (%s, %s, 10, 1) ON CONFLICT (guild_id, user_id) DO UPDATE SET xp = user_levels.xp + 10', (guild_id, user_id))

    cur.execute('SELECT xp, level FROM user_levels WHERE guild_id = %s AND user_id = %s', (guild_id, user_id))
    result = cur.fetchone()
    if result:
        xp, level = result
        next_level_xp = level * 100
        if xp >= next_level_xp:
            new_level = level + 1
            cur.execute('UPDATE user_levels SET level = %s WHERE guild_id = %s AND user_id = %s', (new_level, guild_id, user_id))
            await message.channel.send(f'Félicitations {message.author.mention}, tu as atteint le niveau {new_level} !')

    conn.commit()
    cur.close()
    conn.close()

# Sondages
@bot.command()
@commands.has_permissions(manage_messages=True)
async def poll(ctx, *, question):
    await ctx.message.delete()
    # Utilisation d'un message vide ou invisible pour le mention @here si nécessaire
    # Mais Discord ne permet pas de "masquer" une mention @here tout en notifiant.
    # Une astuce courante est de mettre la mention dans un texte très discret.
    message = await ctx.send(f"||@here||\n📊 **Sondage :** {question}")
    await message.add_reaction("✅")
    await message.add_reaction("❌")

# Modération : Mots bannis
@bot.tree.command(name="bannedword", description="Ajouter un mot à la liste des mots interdits")
async def bannedword(interaction: discord.Interaction, word: str):
    # Vérification du rôle admin via l'ID secret
    admin_role_id = int(os.environ.get('ADMIN_ROLE_ID', 0))
    has_role = any(role.id == admin_role_id for role in interaction.user.roles)

    if not has_role and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Vous n'avez pas la permission d'utiliser cette commande.", ephemeral=True)
        return

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute('INSERT INTO banned_words (guild_id, word) VALUES (%s, %s) ON CONFLICT DO NOTHING', (interaction.guild_id, word.lower()))
        conn.commit()
        await interaction.response.send_message(f'Le mot "{word}" a été ajouté à la liste noire.')
    except Exception as e:
        await interaction.response.send_message(f'Erreur lors de l\'ajout du mot : {e}')
    finally:
        cur.close()
        conn.close()

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Empêcher les messages en MP (on ne répond plus, on ignore)
    if message.guild is None:
        return

    # Vérification des mots bannis
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT word FROM banned_words WHERE guild_id = %s', (message.guild.id,))
    banned_words = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()

    for word in banned_words:
        if word in message.content.lower():
            await message.delete()
            await message.channel.send(f'{message.author.mention}, votre message contenait un mot interdit et a été supprimé.', delete_after=5)
            return

    await handle_xp(message)
    await bot.process_commands(message)

# Autres commandes de modération
@bot.command()
@commands.has_permissions(manage_messages=True)
async def clear(ctx, amount: int):
    await ctx.channel.purge(limit=amount + 1)
    await ctx.send(f'{amount} messages supprimés.', delete_after=5)

@bot.command()
@commands.has_permissions(manage_roles=True)
async def tempmute(ctx, member: discord.Member, duration: str, *, reason=None):
    muted_role = discord.utils.get(ctx.guild.roles, name="Muted")
    if not muted_role:
        muted_role = await ctx.guild.create_role(name="Muted")
        for channel in ctx.guild.channels:
            await channel.set_permissions(muted_role, speak=False, send_messages=False)

    unit = duration[-1]
    try:
        amount = int(duration[:-1])
    except ValueError:
        await ctx.send("Format de durée invalide. Utilisez par exemple 10m, 1h.")
        return

    if unit == 'm':
        seconds = amount * 60
    elif unit == 'h':
        seconds = amount * 3600
    elif unit == 'd':
        seconds = amount * 86400
    else:
        seconds = amount

    await member.add_roles(muted_role, reason=reason)
    await ctx.send(f'{member.mention} a été muet pendant {duration}. Raison : {reason}')

    await asyncio.sleep(seconds)
    await member.remove_roles(muted_role)
    await ctx.send(f'{member.mention} n\'est plus muet.')

@bot.command()
@commands.has_permissions(manage_roles=True)
async def unmute(ctx, member: discord.Member):
    muted_role = discord.utils.get(ctx.guild.roles, name="Muted")
    if muted_role and muted_role in member.roles:
        await member.remove_roles(muted_role)
        await ctx.send(f'{member.mention} a été démuété par un modérateur.')
    else:
        await ctx.send(f'{member.mention} n\'est pas muet.')

# Système de Ticket
@bot.command()
async def ticket(ctx):
    await ctx.message.delete()
    guild = ctx.guild
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        ctx.author: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
    }
    channel = await guild.create_text_channel(f'ticket-{ctx.author.name}', overwrites=overwrites)

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('INSERT INTO tickets (guild_id, channel_id, user_id) VALUES (%s, %s, %s)', (guild.id, channel.id, ctx.author.id))
    conn.commit()
    cur.close()
    conn.close()

    await channel.send(f"Bienvenue {ctx.author.mention} ! Un membre du staff va s'occuper de vous. Utilisez `!close` pour fermer ce ticket.")
    try:
        await ctx.author.send(f"Votre ticket a été créé : {channel.mention}")
    except discord.Forbidden:
        pass

@bot.command()
async def close(ctx):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('SELECT channel_id FROM tickets WHERE channel_id = %s', (ctx.channel.id,))
    if cur.fetchone():
        await ctx.send("Fermeture du ticket dans 3 secondes...")
        await asyncio.sleep(3)
        cur.execute('DELETE FROM tickets WHERE channel_id = %s', (ctx.channel.id,))
        conn.commit()
        await ctx.channel.delete()
    else:
        await ctx.send("Ce salon n'est pas un ticket.")
    cur.close()
    conn.close()

# Lancement du Bot
import threading

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

def run_bot():
    while True:
        try:
            bot.run(os.environ["DISCORD_TOKEN"])
        except Exception as e:
            print("BOT CRASH :", e)
            asyncio.sleep(5)

if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    run_bot()
