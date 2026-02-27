import os
import json
import re
import io
import asyncio
import time
import discord
import gspread
import pandas as pd
from PIL import Image, ImageDraw
from discord.ext import commands
from google.oauth2.service_account import Credentials

# ================= ENV =================
TOKEN = os.environ["DISCORD_TOKEN"]
GOOGLE_CREDENTIALS = json.loads(os.environ["GOOGLE_CREDENTIALS"])
LINKS_SHEET_ID = os.environ["LINKS_SHEET_ID"]
ADMIN_ROLE_ID = int(os.environ.get("ADMIN_ROLE_ID", 0))

STATS_SHEET_ID = None

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

sheet_cache = {}
cache_timestamp = 0
CACHE_DURATION = 60

# ================= GOOGLE =================
def get_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_info(
        GOOGLE_CREDENTIALS,
        scopes=scopes
    )
    return gspread.authorize(creds)

def extract_sheet_id(link):
    match = re.search(r"/d/([a-zA-Z0-9-_]+)", link)
    return match.group(1) if match else link.strip()

# ================= CACHE (THREAD SAFE) =================
def blocking_refresh_cache():
    global sheet_cache, cache_timestamp

    client = get_client()
    new_cache = {}

    # LINKS
    links_spreadsheet = client.open_by_key(LINKS_SHEET_ID)
    links_ws = links_spreadsheet.worksheet("Links")

    headers = links_ws.row_values(1)
    records = links_ws.get_all_records()

    if records:
        new_cache["Links"] = pd.DataFrame(records)
    else:
        new_cache["Links"] = pd.DataFrame(columns=headers)

    # STATS
    if STATS_SHEET_ID:
        stats_spreadsheet = client.open_by_key(STATS_SHEET_ID)
        for ws in stats_spreadsheet.worksheets():
            headers = ws.row_values(1)
            records = ws.get_all_records()
            if records:
                new_cache[ws.title] = pd.DataFrame(records)
            else:
                new_cache[ws.title] = pd.DataFrame(columns=headers)

    sheet_cache.clear()
    sheet_cache.update(new_cache)

    cache_timestamp = time.monotonic()

async def refresh_cache():
    await asyncio.to_thread(blocking_refresh_cache)

async def get_sheets():
    global cache_timestamp
    now = time.monotonic()
    if now - cache_timestamp > CACHE_DURATION:
        await refresh_cache()
    return sheet_cache

async def get_links_ws():
    def open_ws():
        client = get_client()
        return client.open_by_key(LINKS_SHEET_ID).worksheet("Links")
    return await asyncio.to_thread(open_ws)

# ================= UTIL =================
def fmt(v):
    try:
        return f"{int(float(v)):,}"
    except:
        return "0"

def clean_number(value):
    if value is None:
        return 0
    value = str(value)
    value = value.replace(".", "").replace(",", "").replace(" ", "")
    try:
        return float(value)
    except:
        return 0

def create_progress_bar(dkp_pct, dead_pct):
    width, height = 520, 150
    img = Image.new("RGB", (width, height), (32, 34, 37))
    draw = ImageDraw.Draw(img)

    def bar(y, pct, color):
        draw.rounded_rectangle((60, y, 460, y+35), 18, fill=(70,70,70))
        fill = int(400 * min(pct,100) / 100)
        draw.rounded_rectangle((60, y, 60+fill, y+35), 18, fill=color)

    bar(35, dkp_pct, (0,200,0))
    bar(95, dead_pct, (200,0,0))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

# ================= LINK COMMANDS =================
@bot.tree.command(name="link")
async def link(interaction: discord.Interaction, rok_id: str):
    await interaction.response.defer(ephemeral=True)
    sheets = await get_sheets()
    df = sheets.get("Links")
    ws = await get_links_ws()

    if "Discord ID" in df.columns:
        if str(interaction.user.id) in df["Discord ID"].astype(str).values:
            await interaction.followup.send("Already linked.")
            return

    if "Main ID" in df.columns:
        if rok_id in df["Main ID"].astype(str).values:
            await interaction.followup.send("RoK ID already linked.")
            return

    await asyncio.to_thread(ws.append_row, [str(interaction.user.id), rok_id, ""])
    await refresh_cache()
    await interaction.followup.send("Linked successfully.")

@bot.tree.command(name="unlink")
async def unlink(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    sheets = await get_sheets()
    df = sheets.get("Links")
    ws = await get_links_ws()

    rows = df[df["Discord ID"].astype(str) == str(interaction.user.id)]
    if rows.empty:
        await interaction.followup.send("Not linked.")
        return

    row_index = rows.index[0] + 2
    await asyncio.to_thread(ws.delete_rows, row_index)
    await refresh_cache()
    await interaction.followup.send("Unlinked successfully.")

@bot.tree.command(name="link_filler")
async def link_filler(interaction: discord.Interaction, filler_id: str):
    await interaction.response.defer(ephemeral=True)
    sheets = await get_sheets()
    df = sheets.get("Links")
    ws = await get_links_ws()

    rows = df[df["Discord ID"].astype(str) == str(interaction.user.id)]
    if rows.empty:
        await interaction.followup.send("Link your main first.")
        return

    index = rows.index[0]
    current = str(rows.iloc[0].get("Filler IDs", "") or "")
    fillers = [f.strip() for f in current.split(",") if f.strip()]

    if filler_id in fillers:
        await interaction.followup.send("Filler already linked.")
        return

    fillers.append(filler_id)
    await asyncio.to_thread(ws.update_cell, index+2, 3, ",".join(fillers))
    await refresh_cache()
    await interaction.followup.send("Filler linked.")

@bot.tree.command(name="unlink_filler")
async def unlink_filler(interaction: discord.Interaction, filler_id: str):
    await interaction.response.defer(ephemeral=True)
    sheets = await get_sheets()
    df = sheets.get("Links")
    ws = await get_links_ws()

    rows = df[df["Discord ID"].astype(str) == str(interaction.user.id)]
    if rows.empty:
        await interaction.followup.send("Not linked.")
        return

    index = rows.index[0]
    current = str(rows.iloc[0].get("Filler IDs", "") or "")
    fillers = [f.strip() for f in current.split(",") if f.strip()]

    if filler_id not in fillers:
        await interaction.followup.send("Filler not found.")
        return

    fillers.remove(filler_id)
    await asyncio.to_thread(ws.update_cell, index+2, 3, ",".join(fillers))
    await refresh_cache()
    await interaction.followup.send("Filler unlinked.")

# ================= STATS =================
@bot.tree.command(name="data")
async def data(interaction: discord.Interaction, link: str):
    await interaction.response.defer(ephemeral=True)
    if ADMIN_ROLE_ID:
        if not any(role.id == ADMIN_ROLE_ID for role in interaction.user.roles):
            await interaction.followup.send("No permission.")
            return

    global STATS_SHEET_ID
    STATS_SHEET_ID = extract_sheet_id(link)
    await refresh_cache()
    await interaction.followup.send("Stats sheet connected.")

@bot.tree.command(name="my_stats")
async def my_stats(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    sheets_dict = await get_sheets()
    links = sheets_dict.get("Links")

    rows = links[links["Discord ID"].astype(str) == str(interaction.user.id)]
    if rows.empty:
        await interaction.followup.send("You are not linked.")
        return

    main_id = str(rows.iloc[0]["Main ID"])

    # ─── Obtener todas las pestañas de stats (excepto Links) ──────────────
    all_stat_sheets = [name for name in sheets_dict.keys() if name != "Links"]

    # Orden: todo lo que no sea Overall primero, Overall al final
    ordered_sheets = [s for s in all_stat_sheets if s.lower() != "overall"]
    if "Overall" in [s.lower() for s in all_stat_sheets]:
        ordered_sheets.append("Overall")

    if not ordered_sheets:
        await interaction.followup.send("No se encontraron hojas de estadísticas.")
        return

    # ─── Datos principales para descripción y barra (de Overall) ──────────
    overall_df = sheets_dict.get("Overall")
    main_name = "Unknown"
    main_power = 0
    main_current_power = 0
    dkp_pct = 0
    dead_pct = 0

    if overall_df is not None:
        main_row = overall_df[overall_df["ID"].astype(str) == main_id]
        if not main_row.empty:
            r_main = main_row.iloc[0]
            main_name = r_main.get('Name', 'Unknown')
            main_power = clean_number(r_main.get('Power', 0))
            main_current_power = clean_number(r_main.get('Current Power', 0))
            dkp = clean_number(r_main.get('DKP', 0))
            goal_dkp = clean_number(r_main.get('Goal DKP', 1))
            deads_main = clean_number(r_main.get('Deads', 0))
            required_deads = clean_number(r_main.get('Required Deads', r_main.get('Requiered Deads', 1)))

            dkp_pct = (dkp / goal_dkp * 100) if goal_dkp > 0 else 0
            dead_pct = (deads_main / required_deads * 100) if required_deads > 0 else 0

    embed = discord.Embed(title="📊 KVK STATISTIC", color=discord.Color.dark_teal())

    embed.description = (
        f"👤 **Name:** {main_name}\n"
        f"🏰 **Power:** {fmt(main_power)}\n"
        f"⚡ **Current Power:** {fmt(main_current_power)}"
    )

    # ─── Emojis ───────────────────────────────────────────────────────────
    EMOJI_ZONE  = "<:KvK:1476664387358949541>"
    EMOJI_KP    = "🎯"
    EMOJI_T4    = "<:T4:1476664385106739320>"
    EMOJI_T5    = "<:T5:1476664389095522475>"
    EMOJI_DEADS = "💀"

    # ─── Campos por cada pestaña/zone ─────────────────────────────────────
    for sheet_name in ordered_sheets:
        df = sheets_dict.get(sheet_name)
        if df is None:
            continue

        row = df[df["ID"].astype(str) == main_id]
        if row.empty:
            continue

        r = row.iloc[0]

        kp    = clean_number(r.get("KP", 0))
        t4    = clean_number(r.get("T4 Kills", 0))
        t5    = clean_number(r.get("T5 Kills", 0))
        deads = clean_number(r.get("Deads", 0))

        zone_block = (
            f"{EMOJI_KP} {fmt(kp)} "
            f"{EMOJI_T4} {fmt(t4)} "
            f"{EMOJI_T5} {fmt(t5)}\n"
            f"{EMOJI_DEADS} {fmt(deads)}"
        )

        embed.add_field(
            name=f"{EMOJI_ZONE} {sheet_name}",
            value=zone_block,
            inline=False
        )

    # ─── Barra de progreso ────────────────────────────────────────────────
    img = create_progress_bar(dkp_pct, dead_pct)
    file = discord.File(img, "progress.png")
    embed.set_image(url="attachment://progress.png")

    await interaction.followup.send(embed=embed, file=file)

# ================= READY =================
@bot.event
async def on_ready():
    await bot.tree.sync()
    print("Bot is ready.")

bot.run(TOKEN)
