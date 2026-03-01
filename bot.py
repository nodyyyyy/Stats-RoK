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

FILLER_REQUIRED_PERCENT = 0.02
FILLER_BONUS_MULTIPLIER = 0.50

STATS_SHEET_ID = None

# ================= INTENTS =================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

sheet_cache = {}
cache_timestamp = 0
CACHE_DURATION = 60

# ================= GOOGLE =================
def get_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(
        GOOGLE_CREDENTIALS, scopes=scopes
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

        # LINKS (normal)
        links_spreadsheet = client.open_by_key(LINKS_SHEET_ID)
        links_ws = links_spreadsheet.worksheet("Links")
        headers = links_ws.row_values(1)
        records = links_ws.get_all_records()
        new_cache["Links"] = (
            pd.DataFrame(records) if records else pd.DataFrame(columns=headers)
        )

        # STATS
        if STATS_SHEET_ID:
            stats_spreadsheet = client.open_by_key(STATS_SHEET_ID)

            for ws in stats_spreadsheet.worksheets():

                # SOLO REQ limitado a A-G
                if ws.title.strip().lower() == "req":
                    values = ws.get("A1:G")
                    if not values:
                        new_cache[ws.title] = pd.DataFrame()
                        continue

                    headers = values[0]
                    rows = values[1:]
                    df = pd.DataFrame(rows, columns=headers)
                    new_cache[ws.title] = df

                else:
                    headers = ws.row_values(1)
                    records = ws.get_all_records()
                    new_cache[ws.title] = (
                        pd.DataFrame(records)
                        if records
                        else pd.DataFrame(columns=headers)
                    )

        sheet_cache.clear()
        sheet_cache.update(new_cache)
        cache_timestamp = time.monotonic()

    except Exception as e:
        print(f"Error refreshing cache: {e}")

async def refresh_cache():
    await asyncio.to_thread(blocking_refresh_cache)

async def get_sheets():
    global cache_timestamp
    if time.monotonic() - cache_timestamp > CACHE_DURATION:
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
    value = str(value).replace(".", "").replace(",", "").replace(" ", "")
    try:
        return float(value)
    except:
        return 0

# ================= GIF =================
def create_animated_progress_bar(dkp_final=0, dead_final=0, duration=2.5, fps=30):
    frames = []
    total_frames = int(duration * fps)
    w, h = 400, 120
    bg_color = (48, 51, 57)
    bar_bg = (35, 35, 40)

    try:
        font_main = ImageFont.truetype("arial.ttf", 14)
        font_bold = ImageFont.truetype("arialbd.ttf", 14)
    except:
        font_main = font_bold = ImageFont.load_default()

    for i in range(total_frames + 1):
        img = Image.new("RGB", (w, h), bg_color)
        draw = ImageDraw.Draw(img)
        progress = i / total_frames

        curr_dkp = dkp_final * progress
        draw.text((25, 12), "DKP Progress", fill="white", font=font_main)
        draw.rectangle((25, 32, 375, 47), fill=bar_bg)
        fill_dkp = int(350 * min(curr_dkp, 100) / 100)
        if fill_dkp > 0:
            draw.rectangle((25, 32, 25 + fill_dkp, 47), fill=(76, 175, 80))
        draw.text((345, 12), f"{int(curr_dkp)}%", fill="white", font=font_bold)

        curr_dead = dead_final * progress
        draw.text((25, 62), "Deads Progress", fill="white", font=font_main)
        draw.rectangle((25, 82, 375, 97), fill=bar_bg)
        fill_dead = int(350 * min(curr_dead, 100) / 100)
        if fill_dead > 0:
            draw.rectangle((25, 82, 25 + fill_dead, 97), fill=(220, 53, 69))
        draw.text((345, 62), f"{int(curr_dead)}%", fill="white", font=font_bold)

        frames.append(img)

    buf = BytesIO()
    frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=int(1000 / fps),
        loop=1,
    )
    buf.seek(0)
    return buf

# ================= COMMANDS =================

@bot.tree.command(name="link")
async def link(interaction: discord.Interaction, rok_id: str):
    await interaction.response.defer(ephemeral=True)
    sheets = await get_sheets()
    df = sheets.get("Links")
    ws = await get_links_ws()

    if df is not None and not df.empty:
        if str(interaction.user.id) in df["Discord ID"].astype(str).values:
            await interaction.followup.send("Already linked.")
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

    if df is None or df.empty:
        await interaction.followup.send("Not linked.")
        return

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

    index, current = rows.index[0], str(rows.iloc[0].get("Filler IDs", "") or "")
    fillers = [f.strip() for f in current.split(",") if f.strip()]

    if filler_id in fillers:
        await interaction.followup.send("Filler already linked.")
        return

    fillers.append(filler_id)
    await asyncio.to_thread(ws.update_cell, index + 2, 3, ",".join(fillers))
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

    index, current = rows.index[0], str(rows.iloc[0].get("Filler IDs", "") or "")
    fillers = [f.strip() for f in current.split(",") if f.strip()]

    if filler_id not in fillers:
        await interaction.followup.send("Filler not found.")
        return

    fillers.remove(filler_id)
    await asyncio.to_thread(ws.update_cell, index + 2, 3, ",".join(fillers))
    await refresh_cache()
    await interaction.followup.send("Filler unlinked.")


@bot.tree.command(name="data")
async def data(interaction: discord.Interaction, link: str):
    await interaction.response.defer(ephemeral=True)

    if ADMIN_ROLE_ID and not any(r.id == ADMIN_ROLE_ID for r in interaction.user.roles):
        await interaction.followup.send("No permission.")
        return

    global STATS_SHEET_ID
    STATS_SHEET_ID = extract_sheet_id(link)
    await refresh_cache()
    await interaction.followup.send("Stats sheet connected.")


# ================= MY_STATS =================

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
    flinks = [fid.strip() for fid in str(filler_ids_raw).split(",") if fid.strip()]

    all_stat_sheets = [name for name in sheets_dict.keys() if name != "Links"]
    ordered_sheets = [s for s in all_stat_sheets if s.lower() not in ["overall", "req"]]
    if any(s.lower() == "overall" for s in all_stat_sheets):
        ordered_sheets.append("Overall")

    main_name, main_power, main_current_power = "Unknown", 0, 0
    dkp_pct, dead_pct = 0, 0

    overall_df = sheets_dict.get("Overall")
    if overall_df is not None:
        m_row = overall_df[overall_df["ID"].astype(str) == main_id]
        if not m_row.empty:
            r = m_row.iloc[0]
            main_name = r.get("Name", "Unknown")
            main_power = clean_number(r.get("Power", 0))
            main_current_power = clean_number(r.get("Current Power", 0))

            dkp = clean_number(r.get("DKP", 0))
            goal_dkp = clean_number(r.get("Goal DKP", 1))
            deads = clean_number(r.get("Deads", 0))
            req_deads_calc = clean_number(r.get("Required Deads", 1))

            dkp_pct = (dkp / goal_dkp * 100) if goal_dkp > 0 else 0
            dead_pct = (deads / req_deads_calc * 100) if req_deads_calc > 0 else 0

    # ===== REQ =====
    req_dkp = 0
    req_deads = 0

    req_df = sheets_dict.get("REQ")
    if req_df is not None:
        req_row = req_df[req_df["ID"].astype(str) == main_id]
        if not req_row.empty:
            rr = req_row.iloc[0]
            req_dkp = clean_number(rr.get("Required DKP", 0))
            req_deads = clean_number(rr.get("Required Deads", 0))

    embed = discord.Embed(title="📊 KVK STATISTIC", color=discord.Color.purple())
    embed.description = (
        f"👤 **Name:** {main_name}\n"
        f"🏰 **Power:** {fmt(main_power)}\n"
        f"⚡ **Current Power:** {fmt(main_current_power)}\n\n"
        f"📌 **Required DKP:** {fmt(req_dkp)}\n"
        f"💀 **Required Deads:** {fmt(req_deads)}"
    )

    # ===== ZONES =====
    EMOJI_ZONE = "<:KvK:1476664387358949541>"
    EMOJI_KP = "🎯"
    EMOJI_T4 = "<:T4:1476664385106739320>"
    EMOJI_T5 = "<:T5:1476664389095522475>"
    EMOJI_DEADS = "💀"

    for sheet_name in ordered_sheets:
        df = sheets_dict.get(sheet_name)
        if df is None:
            continue

        row = df[df["ID"].astype(str) == main_id]
        if not row.empty:
            r = row.iloc[0]
            kp = clean_number(r.get("KP", 0))
            t4 = clean_number(r.get("T4 Kills", 0))
            t5 = clean_number(r.get("T5 Kills", 0))
            ds = clean_number(r.get("Deads", 0))

            zone_block = (
                f"┣ {EMOJI_KP} **KP:** {fmt(kp)}\n"
                f"┣ {EMOJI_T4} **T4 Kills:** {fmt(t4)}\n"
                f"┣ {EMOJI_T5} **T5 Kills:** {fmt(t5)}\n"
                f"┗ {EMOJI_DEADS} **Deads:** {fmt(ds)}\n\n"
            )

            embed.add_field(name=f"{EMOJI_ZONE} {sheet_name}", value=zone_block, inline=False)

    # ===== FILLER BONUS =====
    total_bonus, bonus_lines = 0, []

    if overall_df is not None and flinks:
        for fid in flinks:
            row_f = overall_df[overall_df["ID"].astype(str) == str(fid)]
            if not row_f.empty:
                f = row_f.iloc[0]
                fn = f.get("Name", "Unknown")
                p = clean_number(f.get("Initial Power", f.get("Power", 0)))
                df_ = clean_number(f.get("Deads", 0))
                req = p * FILLER_REQUIRED_PERCENT

                prog = min(max((df_ / req * 100) if req > 0 else 0, 0), 100)
                bonus = (df_ - req) * FILLER_BONUS_MULTIPLIER if prog >= 100 else 0

                total_bonus += bonus
                bar = "█" * int(prog / 10) + "─" * (10 - int(prog / 10))

                bonus_lines.append(
                    f"🆔 `{fid}` — **{fn}**\n"
                    f"💀 **{fmt(df_)}** / {fmt(req)}  [{bar}] {int(prog)}%\n"
                    f"{f'✨ +**{fmt(bonus)}**' if prog >= 100 else '(not qualified)'}"
                )

    if bonus_lines:
        embed.add_field(
            name="✨ Filler Bonus (Deads)",
            value="\n\n".join(bonus_lines) + f"\n\n**Total bonus:** +**{fmt(total_bonus)}**",
            inline=False
        )

    gif_buf = await asyncio.to_thread(create_animated_progress_bar, dkp_pct, dead_pct)
    file = discord.File(gif_buf, filename="progress.gif")
    embed.set_image(url="attachment://progress.gif")

    await interaction.followup.send(embed=embed, file=file)


# ================= REQ COMMAND =================

@bot.tree.command(name="req")
async def req(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)

    sheets_dict = await get_sheets()
    links = sheets_dict.get("Links")

    rows = links[links["Discord ID"].astype(str) == str(interaction.user.id)]
    if rows.empty:
        await interaction.followup.send("You are not linked.")
        return

    main_id = str(rows.iloc[0]["Main ID"])
    req_df = sheets_dict.get("REQ")

    if req_df is None:
        await interaction.followup.send("REQ sheet not found.")
        return

    req_row = req_df[req_df["ID"].astype(str) == main_id]
    if req_row.empty:
        await interaction.followup.send("No REQ data found for you.")
        return

    r = req_row.iloc[0]

    embed = discord.Embed(title="📋 REQ REQUIREMENTS", color=discord.Color.gold())
    embed.add_field(name="👤 Name", value=r.get("Name", "Unknown"), inline=False)
    embed.add_field(name="🏰 Power", value=fmt(clean_number(r.get("Power", 0))), inline=True)
    embed.add_field(name="🎯 Required DKP", value=fmt(clean_number(r.get("Required DKP", 0))), inline=True)
    embed.add_field(name="📊 % DKP", value=str(r.get("% DKP", "0")), inline=True)
    embed.add_field(name="💀 Required Deads", value=fmt(clean_number(r.get("Required Deads", 0))), inline=True)
    embed.add_field(name="📊 % Deads", value=str(r.get("% Deads", "0")), inline=True)

    await interaction.followup.send(embed=embed)


@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Bot online as {bot.user}")

bot.run(TOKEN)
