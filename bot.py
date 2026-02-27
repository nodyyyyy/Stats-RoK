import os
import json
import re
import io
import asyncio
import time
import discord
import gspread
import pandas as pd
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
from discord.ext import commands
from google.oauth2.service_account import Credentials

# ================= ENV =================
TOKEN = os.environ["DISCORD_TOKEN"]
GOOGLE_CREDENTIALS = json.loads(os.environ["GOOGLE_CREDENTIALS"])
LINKS_SHEET_ID = os.environ["LINKS_SHEET_ID"]
ADMIN_ROLE_ID = int(os.environ.get("ADMIN_ROLE_ID", 0))

# Filler Bonus Settings
FILLER_REQUIRED_PERCENT = 0.02
FILLER_BONUS_MULTIPLIER = 0.50

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

# ================= CACHE =================
def blocking_refresh_cache():
    global sheet_cache, cache_timestamp

    try:
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
        print("Cache refreshed successfully.")
    except Exception as e:
        print(f"Error refreshing cache: {e}")

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

# ================= ANIMATED PROGRESS BAR (DISEÑO 400x120) =================
def create_animated_progress_bar(dkp_final=0, dead_final=0, duration=2.5, fps=30):
    frames = []
    total_frames = int(duration * fps)
    
    w, h = 400, 120
    bg_color = (48, 51, 57)  # Gris solicitado
    bar_bg = (35, 35, 40)
    
    try:
        # Intentar cargar fuentes, si no usa default
        font_main = ImageFont.truetype("arial.ttf", 14)
        font_bold = ImageFont.truetype("arialbd.ttf", 14)
    except:
        font_main = font_bold = ImageFont.load_default()

    for i in range(total_frames + 1):
        img = Image.new("RGB", (w, h), bg_color)
        draw = ImageDraw.Draw(img)
        progress = i / total_frames
        
        # BARRA DKP
        curr_dkp = dkp_final * progress
        draw.text((25, 12), "DKP Progress", fill="white", font=font_main)
        draw.rectangle((25, 32, 375, 47), fill=bar_bg)
        fill_dkp = int(350 * min(curr_dkp, 100) / 100)
        if fill_dkp > 0:
            draw.rectangle((25, 32, 25 + fill_dkp, 47), fill=(76, 175, 80))
        draw.text((345, 12), f"{int(curr_dkp)}%", fill="white", font=font_bold)

        # BARRA DEADS
        curr_dead = dead_final * progress
        draw.text((25, 62), "Deads Progress", fill="white", font=font_main)
        draw.rectangle((25, 82, 375, 97), fill=bar_bg)
        fill_dead = int(350 * min(curr_dead, 100) / 100)
        if fill_dead > 0:
            draw.rectangle((25, 82, 25 + fill_dead, 97), fill=(220, 53, 69))
        draw.text((345, 62), f"{int(curr_dead)}%", fill="white", font=font_bold)

        frames.append(img)

    # Pausa final
    for _ in range(fps * 2):
        frames.append(frames[-1])

    buf = BytesIO()
    frames[0].save(
        buf, format="GIF", save_all=True, append_images=frames[1:],
        duration=int(1000/fps), loop=0
    )
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
    await interaction.response.defer(ephemeral=False)

    sheets_dict = await get_sheets()
    links = sheets_dict.get("Links")

    rows = links[links["Discord ID"].astype(str) == str(interaction.user.id)]
    if rows.empty:
        await interaction.followup.send("You are not linked.")
        return

    main_id = str(rows.iloc[0]["Main ID"])
    filler_ids_raw = rows.iloc[0].get("Filler IDs", "")
    filler_ids_str = str(filler_ids_raw) if filler_ids_raw is not None else ""
    flinks = [fid.strip() for fid in filler_ids_str.split(",") if fid.strip()]

    all_stat_sheets = [name for name in sheets_dict.keys() if name != "Links"]
    ordered_sheets = [s for s in all_stat_sheets if s.lower() != "overall"]
    has_overall = any(s.lower() == "overall" for s in all_stat_sheets)
    if has_overall:
        ordered_sheets.append("Overall")

    if not ordered_sheets:
        await interaction.followup.send("No stat sheets found.")
        return

    main_name, main_power, main_current_power = "Unknown", 0, 0
    dkp_pct, dead_pct = 0, 0

    overall_df = sheets_dict.get("Overall")
    if overall_df is not None:
        main_row = overall_df[overall_df["ID"].astype(str) == main_id]
        if not main_row.empty:
            r = main_row.iloc[0]
            main_name = r.get('Name', 'Unknown')
            main_power = clean_number(r.get('Power', 0))
            main_current_power = clean_number(r.get('Current Power', 0))
            dkp = clean_number(r.get('DKP', 0))
            goal_dkp = clean_number(r.get('Goal DKP', 1))
            deads = clean_number(r.get('Deads', 0))
            required_deads = clean_number(r.get('Required Deads', r.get('Requiered Deads', 1)))
            dkp_pct = (dkp / goal_dkp * 100) if goal_dkp > 0 else 0
            dead_pct = (deads / required_deads * 100) if required_deads > 0 else 0

    embed = discord.Embed(title="📊 KVK STATISTIC", color=discord.Color.purple())
    embed.description = (
        f"👤 **Name:** {main_name}\n"
        f"🏰 **Power:** {fmt(main_power)}\n"
        f"⚡ **Current Power:** {fmt(main_current_power)}"
    )

    EMOJI_ZONE  = "<:KvK:1476664387358949541>"
    EMOJI_KP    = "🎯"
    EMOJI_T4    = "<:T4:1476664385106739320>"
    EMOJI_T5    = "<:T5:1476664389095522475>"
    EMOJI_DEADS = "💀"

    overall_field_added = False
    for sheet_name in ordered_sheets:
        df = sheets_dict.get(sheet_name)
        if df is None: continue
        row = df[df["ID"].astype(str) == main_id]
        if row.empty:
            if sheet_name.lower() == "overall":
                embed.add_field(name=f"{EMOJI_ZONE} {sheet_name}", value="No data found", inline=False)
                overall_field_added = True
            continue
        r = row.iloc[0]
        kp, t4, t5, deads = clean_number(r.get("KP", 0)), clean_number(r.get("T4 Kills", 0)), clean_number(r.get("T5 Kills", 0)), clean_number(r.get("Deads", 0))
        zone_block = f"▌\n▌ {EMOJI_KP} **{fmt(kp)}** {EMOJI_T4} {fmt(t4)} {EMOJI_T5} {fmt(t5)} \n▌ {EMOJI_DEADS} **{fmt(deads)}** \n▌\n\n"
        embed.add_field(name=f"{EMOJI_ZONE} {sheet_name}", value=zone_block, inline=False)
        if sheet_name.lower() == "overall": overall_field_added = True

    if has_overall and not overall_field_added:
        embed.add_field(name=f"{EMOJI_ZONE} Overall", value="No data found", inline=False)

    # Filler Bonus Logic
    total_bonus = 0
    bonus_lines = []
    if overall_df is not None and flinks:
        for fid in flinks:
            row_f = overall_df[overall_df["ID"].astype(str) == str(fid)]
            if row_f.empty:
                bonus_lines.append(f"🆔 `{fid}` — Not found")
                continue
            f = row_f.iloc[0]
            fname, power, deads_f = f.get("Name", "Unknown"), clean_number(f.get("Initial Power", f.get("Power", 0))), clean_number(f.get("Deads", 0))
            required = power * FILLER_REQUIRED_PERCENT
            progress_pct = min(max((deads_f / required * 100) if required > 0 else 0, 0), 100)
            bar = "█" * int(progress_pct / 10) + "─" * (10 - int(progress_pct / 10))
            bonus = (deads_f - required) * FILLER_BONUS_MULTIPLIER if progress_pct >= 100 else 0
            total_bonus += bonus
            bonus_text = f"✨ +**{fmt(bonus)}**" if progress_pct >= 100 else "(not qualified)"
            bonus_lines.append(f"🆔 `{fid}` — **{fname}**\n💀 **{fmt(deads_f)}** / {fmt(required)}  [{bar}] {int(progress_pct)}%\n{bonus_text}")

    if bonus_lines:
        embed.add_field(name="✨ Filler Bonus (Deads)", value="\n\n".join(bonus_lines) + f"\n\n**Total bonus:** +**{fmt(total_bonus)}**", inline=False)
    else:
        embed.add_field(name="✨ Filler Bonus (Deads)", value="No linked fillers.", inline=False)

    # GIF Generation
    gif_buf = await asyncio.to_thread(create_animated_progress_bar, dkp_pct, dead_pct)
    file = discord.File(gif_buf, filename="progress.gif")
    embed.set_image(url="attachment://progress.gif")

    await interaction.followup.send(embed=embed, file=file)

@bot.event
async def on_ready():
    await bot.tree.sync()
    print("Bot is ready.")

bot.run(TOKEN)
