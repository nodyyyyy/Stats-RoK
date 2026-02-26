import os
import json
import re
import io
import asyncio
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
STATS_SHEET_ID = None

ADMIN_ROLE_ID = int(os.environ.get("ADMIN_ROLE_ID", 0))

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

def refresh_cache():
    global sheet_cache, cache_timestamp

    client = get_client()
    sheet_cache = {}

    # Stats file
    if STATS_SHEET_ID:
        stats_spreadsheet = client.open_by_key(STATS_SHEET_ID)
        for ws in stats_spreadsheet.worksheets():
            sheet_cache[ws.title] = pd.DataFrame(ws.get_all_records())

    # Links file
    links_spreadsheet = client.open_by_key(LINKS_SHEET_ID)
    links_ws = links_spreadsheet.worksheet("Links")
    records = links_ws.get_all_records()
if records:
    sheet_cache["Links"] = pd.DataFrame(records)
else:
    headers = links_ws.row_values(1)
    sheet_cache["Links"] = pd.DataFrame(columns=headers)

    cache_timestamp = asyncio.get_event_loop().time()

def get_sheets():
    global cache_timestamp
    now = asyncio.get_event_loop().time()
    if now - cache_timestamp > CACHE_DURATION:
        refresh_cache()
    return sheet_cache

# ================= UTIL =================

def fmt(v):
    try:
        return f"{int(float(v)):,}"
    except:
        return "0"

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

# ================= LINKS =================

def get_links_ws():
    client = get_client()
    return client.open_by_key(LINKS_SHEET_ID).worksheet("Links")

@bot.tree.command(name="link")
async def link(interaction: discord.Interaction, rok_id: str):

    sheets = get_sheets()
    df = sheets.get("Links")
    ws = get_links_ws()

    if str(interaction.user.id) in df["Discord ID"].astype(str).values:
        await interaction.response.send_message("Already linked.", ephemeral=True)
        return

    if rok_id in df["Main ID"].astype(str).values:
        await interaction.response.send_message("RoK ID already linked.", ephemeral=True)
        return

    ws.append_row([str(interaction.user.id), rok_id, ""])
    refresh_cache()

    await interaction.response.send_message("Linked successfully.", ephemeral=True)

@bot.tree.command(name="unlink")
async def unlink(interaction: discord.Interaction):

    sheets = get_sheets()
    df = sheets.get("Links")
    ws = get_links_ws()

    rows = df[df["Discord ID"].astype(str) == str(interaction.user.id)]
    if rows.empty:
        await interaction.response.send_message("Not linked.", ephemeral=True)
        return

    row_index = rows.index[0] + 2
    ws.delete_rows(row_index)
    refresh_cache()

    await interaction.response.send_message("Unlinked successfully.", ephemeral=True)

@bot.tree.command(name="link_filler")
async def link_filler(interaction: discord.Interaction, filler_id: str):

    sheets = get_sheets()
    df = sheets.get("Links")
    ws = get_links_ws()

    row = df[df["Discord ID"].astype(str) == str(interaction.user.id)]
    if row.empty:
        await interaction.response.send_message("Link your main first.", ephemeral=True)
        return

    index = row.index[0]
    current = str(row.iloc[0]["Filler IDs"] or "")
    fillers = [f.strip() for f in current.split(",") if f.strip()]

    if filler_id in fillers:
        await interaction.response.send_message("Filler already linked.", ephemeral=True)
        return

    fillers.append(filler_id)
    ws.update_cell(index+2, 3, ",".join(fillers))
    refresh_cache()

    await interaction.response.send_message("Filler linked.", ephemeral=True)

@bot.tree.command(name="unlink_filler")
async def unlink_filler(interaction: discord.Interaction, filler_id: str):

    sheets = get_sheets()
    df = sheets.get("Links")
    ws = get_links_ws()

    row = df[df["Discord ID"].astype(str) == str(interaction.user.id)]
    if row.empty:
        await interaction.response.send_message("Not linked.", ephemeral=True)
        return

    index = row.index[0]
    current = str(row.iloc[0]["Filler IDs"] or "")
    fillers = [f.strip() for f in current.split(",") if f.strip()]

    if filler_id not in fillers:
        await interaction.response.send_message("Filler not found.", ephemeral=True)
        return

    fillers.remove(filler_id)
    ws.update_cell(index+2, 3, ",".join(fillers))
    refresh_cache()

    await interaction.response.send_message("Filler unlinked.", ephemeral=True)

# ================= STATS =================

@bot.tree.command(name="my_stats")
async def my_stats(interaction: discord.Interaction):

    await interaction.response.defer(ephemeral=True)

    sheets = get_sheets()
    links = sheets.get("Links")

    row = links[links["Discord ID"].astype(str) == str(interaction.user.id)]
    if row.empty:
        await interaction.followup.send("You are not linked.")
        return

    main_id = str(row.iloc[0]["Main ID"])
    overall = sheets.get("Overall")

    if overall is None:
        await interaction.followup.send("Stats sheet not connected.")
        return

    stats = overall[overall["ID"].astype(str) == main_id]
    if stats.empty:
        await interaction.followup.send("Stats not found.")
        return

    r = stats.iloc[0]

    name = r.get("Name", "Unknown")
    power = r.get("Power", 0)
    current_power = r.get("Current Power", 0)

    required_deads = r.get("Required Deads", r.get("Requiered Deads", 1))

    dkp_pct = float(r.get("DKP",0)) / float(r.get("Goal DKP",1)) * 100
    dead_pct = float(r.get("Deads",0)) / float(required_deads or 1) * 100

    embed = discord.Embed(
        title="📊 KVK STATISTIC",
        color=discord.Color.dark_teal()
    )

    embed.description = (
        f"👤 **Name:** {name}\n"
        f"🏰 **Power:** {fmt(power)}\n"
        f"⚡ **Current Power:** {fmt(current_power)}"
    )

    embed.add_field(name="🎯 KP", value=fmt(r.get("KP",0)), inline=True)
    embed.add_field(name="<:T4:1476664385106739320> T4", value=fmt(r.get("T4 Kills",0)), inline=True)
    embed.add_field(name="<:T5:1476664389095522475> T5", value=fmt(r.get("T5 Kills",0)), inline=True)
    embed.add_field(name="💀 Deads", value=fmt(r.get("Deads",0)), inline=True)

    img = create_progress_bar(dkp_pct, dead_pct)
    file = discord.File(img, "progress.png")
    embed.set_image(url="attachment://progress.png")

    await interaction.followup.send(embed=embed, file=file)

@bot.tree.command(name="data")
async def data(interaction: discord.Interaction, link: str):

    if ADMIN_ROLE_ID:
        if not any(role.id == ADMIN_ROLE_ID for role in interaction.user.roles):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return

    global STATS_SHEET_ID
    STATS_SHEET_ID = extract_sheet_id(link)
    refresh_cache()

    await interaction.response.send_message("Stats sheet connected.", ephemeral=True)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print("Bot is ready.")

bot.run(TOKEN)
