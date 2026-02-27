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

    client = get_client()
    new_cache = {}

    links_spreadsheet = client.open_by_key(LINKS_SHEET_ID)
    links_ws = links_spreadsheet.worksheet("Links")

    headers = links_ws.row_values(1)
    records = links_ws.get_all_records()

    if records:
        new_cache["Links"] = pd.DataFrame(records)
    else:
        new_cache["Links"] = pd.DataFrame(columns=headers)

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

# ================= ANIMATED PROGRESS BAR (GIF - solo una vez) =================
def create_animated_progress_bar(dkp_final=0, dead_final=0, duration=1.8, fps=30):
    frames = []
    total_frames = int(duration * fps)
    
    width, height = 600, 240
    try:
        font_label = ImageFont.truetype("arial.ttf", 24)
        font_pct = ImageFont.truetype("arialbd.ttf", 32)
    except:
        font_label = font_pct = ImageFont.load_default()

    for i in range(total_frames + 1):
        img = Image.new("RGB", (width, height), (10, 31, 63))  # azul oscuro reino
        draw = ImageDraw.Draw(img)
        
        progress = i / total_frames
        
        # Fondo sutil degradado azul → morado
        for y in range(height):
            r = 10 + int(y / height * 40)
            g = 31 + int(y / height * 20)
            b = 63 + int(y / height * 60)
            draw.line((0, y, width, y), fill=(r,g,b))

        # Barra DKP (dorado)
        dkp_pct = dkp_final * progress
        draw.rounded_rectangle((80, 40, width-80, 110), radius=35, fill=(20, 30, 60))
        fill_w = int((width-160) * min(dkp_pct, 100) / 100)
        draw.rounded_rectangle((80, 40, 80+fill_w, 110), radius=35, fill=(255, 215, 0))  # dorado
        # glow
        draw.rounded_rectangle((78, 38, 82+fill_w, 112), radius=37, outline=(255, 215, 0, 120), width=4)

        pct_text = f"{int(dkp_pct)}%"
        bbox = draw.textbbox((0,0), pct_text, font=font_pct)
        tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
        draw.text(((width-tw)//2, 55), pct_text, fill="white", font=font_pct)
        
        label_bbox = draw.textbbox((0,0), "DKP Progress", font=font_label)
        lw = label_bbox[2] - label_bbox[0]
        draw.text(((width-lw)//2, 5), "DKP Progress", fill=(255, 215, 0), font=font_label)

        # Barra Deads (rojo intenso)
        dead_pct = dead_final * progress
        draw.rounded_rectangle((80, 140, width-80, 210), radius=35, fill=(30, 10, 20))
        fill_w = int((width-160) * min(dead_pct, 100) / 100)
        draw.rounded_rectangle((80, 140, 80+fill_w, 210), radius=35, fill=(200, 20, 60))  # rojo reino
        # glow
        draw.rounded_rectangle((78, 138, 82+fill_w, 212), radius=37, outline=(200, 20, 60, 120), width=4)

        pct_text = f"{int(dead_pct)}%"
        bbox = draw.textbbox((0,0), pct_text, font=font_pct)
        tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
        draw.text(((width-tw)//2, 155), pct_text, fill="white", font=font_pct)
        
        label_bbox = draw.textbbox((0,0), "Deads Progress", font=font_label)
        lw = label_bbox[2] - label_bbox[0]
        draw.text(((width-lw)//2, 105), "Deads Progress", fill=(200, 20, 60), font=font_label)

        frames.append(img)

    buf = BytesIO()
    frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=int(1000 / fps),
        loop=1   # Solo reproduce UNA VEZ y se queda en el final
    )
    buf.seek(0)
    
    return buf

# ================= LINK COMMANDS (sin cambios) =================
# ... (tus comandos link, unlink, link_filler, unlink_filler siguen igual)

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

    main_name = "Unknown"
    main_power = 0
    main_current_power = 0
    dkp_pct = 0
    dead_pct = 0

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
        if df is None:
            continue

        row = df[df["ID"].astype(str) == main_id]
        if row.empty:
            if sheet_name.lower() == "overall":
                embed.add_field(
                    name=f"{EMOJI_ZONE} {sheet_name}",
                    value="No data found in this sheet",
                    inline=False
                )
                overall_field_added = True
            continue

        r = row.iloc[0]

        kp    = clean_number(r.get("KP", 0))
        t4    = clean_number(r.get("T4 Kills", 0))
        t5    = clean_number(r.get("T5 Kills", 0))
        deads = clean_number(r.get("Deads", 0))

        zone_block = (
        
            f"▌ {EMOJI_KP} **{fmt(kp)}** {EMOJI_T4} {fmt(t4)} {EMOJI_T5} {fmt(t5)} \n"
            f"▌ {EMOJI_DEADS} **{fmt(deads)}** \n"
            f"\n"
            f"\n"
        )

        embed.add_field(
            name=f"{EMOJI_ZONE} {sheet_name}",
            value=zone_block,
            inline=False
        )

        if sheet_name.lower() == "overall":
            overall_field_added = True

    if has_overall and not overall_field_added:
        embed.add_field(
            name=f"{EMOJI_ZONE} Overall",
            value="No data found in Overall",
            inline=False
        )

    # Filler Bonus
    total_bonus = 0
    bonus_lines = []

    if overall_df is not None and flinks:
        for fid in flinks:
            row_f = overall_df[overall_df["ID"].astype(str) == str(fid)]
            if row_f.empty:
                bonus_lines.append(f"🆔 `{fid}` — Not found in Overall")
                continue

            f = row_f.iloc[0]
            fname = f.get("Name", "Unknown")
            power = clean_number(f.get("Initial Power", f.get("Power", 0)))
            deads_f = clean_number(f.get("Deads", 0))

            required = power * FILLER_REQUIRED_PERCENT

            progress_pct = (deads_f / required * 100) if required > 0 else 0
            progress_pct = min(max(progress_pct, 0), 100)

            filled = int(progress_pct / 10)
            bar = "█" * filled + "─" * (10 - filled)
            bar_display = f"[{bar}] {int(progress_pct)}%"

            bonus = 0
            if progress_pct >= 100:
                excess = deads_f - required
                bonus = excess * FILLER_BONUS_MULTIPLIER
                total_bonus += bonus
                bonus_text = f"✨ +**{fmt(bonus)}**"
            else:
                bonus_text = "(does not qualify yet)"

            bonus_lines.append(
                f"🆔 `{fid}` — **{fname}**\n"
                f"💀 **{fmt(deads_f)}** / {fmt(required)}  {bar_display}\n"
                f"{bonus_text}"
            )

    if bonus_lines:
        embed.add_field(
            name="✨ Filler Bonus (Deads)",
            value="\n\n".join(bonus_lines) + f"\n\n**Total bonus:** +**{fmt(total_bonus)}**",
            inline=False
        )
    elif flinks:
        embed.add_field(
            name="✨ Filler Bonus (Deads)",
            value="No fillers qualify for bonus yet.",
            inline=False
        )
    else:
        embed.add_field(
            name="✨ Filler Bonus (Deads)",
            value="No linked fillers.",
            inline=False
        )

    # Animated GIF (solo una vez)
    gif_buf = create_animated_progress_bar(dkp_final=dkp_pct, dead_final=dead_pct)
    file = discord.File(gif_buf, filename="progress.gif")
    embed.set_image(url="attachment://progress.gif")

    await interaction.followup.send(embed=embed, file=file)

# ================= READY =================
@bot.event
async def on_ready():
    await bot.tree.sync()
    print("Bot is ready.")

bot.run(TOKEN)
