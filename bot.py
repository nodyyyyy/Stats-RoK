import os
import json
import re
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

# Filler bonus settings
FILLER_REQUIRED_PERCENT = 0.02
FILLER_BONUS_MULTIPLIER = 0.50

STATS_SHEET_ID = None
KVK_ACTIVE = False  # 🔥 Control de estado KvK

# ================= INTENTS =================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ================= CACHE =================
sheet_cache = {}
cache_timestamp = 0
CACHE_DURATION = 60


# ================= GOOGLE CLIENT =================
def get_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(
        GOOGLE_CREDENTIALS,
        scopes=scopes
    )
    return gspread.authorize(creds)


def extract_sheet_id(link):
    match = re.search(r"/d/([a-zA-Z0-9-_]+)", link)
    return match.group(1) if match else link.strip()


# ================= CACHE REFRESH =================
def blocking_refresh_cache():
    global sheet_cache, cache_timestamp

    try:
        client = get_client()
        new_cache = {}

        # -------- LINKS --------
        links_spreadsheet = client.open_by_key(LINKS_SHEET_ID)
        links_ws = links_spreadsheet.worksheet("Links")

        headers = links_ws.row_values(1)
        records = links_ws.get_all_records()

        new_cache["Links"] = (
            pd.DataFrame(records)
            if records
            else pd.DataFrame(columns=headers)
        )

        # -------- STATS --------
        if STATS_SHEET_ID:
            stats_spreadsheet = client.open_by_key(STATS_SHEET_ID)

            for ws in stats_spreadsheet.worksheets():

                # 🔹 SOLO REQ limitado a A-G
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


# ================= GIF PROGRESS BAR — PLAYER CARD =================

LOGO_PATH = os.path.join(os.path.dirname(__file__), "3558_KD_preview_rev_1.png")

def _load_logo(size: int) -> Image.Image:
    """Load the kingdom logo, crop to circle, resize to `size`x`size`."""
    try:
        logo = Image.open(LOGO_PATH).convert("RGBA")
    except Exception:
        # Fallback: solid purple circle
        logo = Image.new("RGBA", (size, size), (123, 44, 191, 255))

    logo = logo.resize((size, size), Image.LANCZOS)

    # Circular mask
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    logo.putalpha(mask)
    return logo


def _draw_rounded_bar(draw, x, y, w, h, r, fill):
    """Draw a rounded-corner rectangle."""
    r = min(r, w // 2, h // 2)
    draw.rectangle([x + r, y, x + w - r, y + h], fill=fill)
    draw.rectangle([x, y + r, x + w, y + h - r], fill=fill)
    draw.ellipse([x, y, x + 2 * r, y + 2 * r], fill=fill)
    draw.ellipse([x + w - 2 * r, y, x + w, y + 2 * r], fill=fill)
    draw.ellipse([x, y + h - 2 * r, x + 2 * r, y + h], fill=fill)
    draw.ellipse([x + w - 2 * r, y + h - 2 * r, x + w, y + h], fill=fill)


def create_animated_progress_bar(
    dkp_final=0,
    dead_final=0,
    player_name="Player",
    discord_name="",
    duration=2.0,
    fps=30,
):
    frames = []
    total_frames = int(duration * fps)

    W, H = 460, 160
    BG       = (15, 15, 24)
    CARD_BG  = (20, 20, 35)
    BAR_BG   = (30, 30, 50)
    PURPLE   = (155, 89, 255)
    PURPLE_D = (80, 30, 160)
    RED      = (231, 76, 60)
    RED_D    = (120, 20, 20)
    WHITE    = (255, 255, 255)
    GREY     = (140, 140, 160)
    DIVIDER  = (35, 35, 55)

    # Fonts
    try:
        font_name   = ImageFont.truetype("arialbd.ttf",  13)
        font_disc   = ImageFont.truetype("arial.ttf",    11)
        font_label  = ImageFont.truetype("arial.ttf",    11)
        font_pct    = ImageFont.truetype("arialbd.ttf",  12)
    except Exception:
        font_name = font_disc = font_label = font_pct = ImageFont.load_default()

    LOGO_SIZE = 56
    logo = _load_logo(LOGO_SIZE)

    # Layout constants
    PAD       = 14
    LOGO_X    = PAD
    LOGO_Y    = (H - LOGO_SIZE) // 2
    TEXT_X    = LOGO_X + LOGO_SIZE + 14
    BAR_X     = TEXT_X
    BAR_W     = W - BAR_X - PAD
    BAR_H     = 14
    BAR_R     = 7
    BAR_Y1    = 76   # DKP bar top
    BAR_Y2    = 118  # Deads bar top

    def ease(t):
        return t * t * (3 - 2 * t)

    for i in range(total_frames + 1):
        p  = ease(i / total_frames)

        img  = Image.new("RGB", (W, H), BG)
        draw = ImageDraw.Draw(img)

        # ── Card background ──
        _draw_rounded_bar(draw, 6, 6, W - 12, H - 12, 12, CARD_BG)

        # ── Left accent bar ──
        draw.rectangle([6, 6, 10, H - 6], fill=PURPLE)

        # ── Logo circle border ──
        border = 3
        draw.ellipse(
            [LOGO_X - border, LOGO_Y - border,
             LOGO_X + LOGO_SIZE + border, LOGO_Y + LOGO_SIZE + border],
            fill=PURPLE_D
        )
        img.paste(logo, (LOGO_X, LOGO_Y), logo)

        # ── Player name ──
        draw.text((TEXT_X, 18), player_name, fill=WHITE, font=font_name)

        # ── Discord name ──
        if discord_name:
            draw.text((TEXT_X, 36), f"@{discord_name}", fill=GREY, font=font_disc)

        # ── Divider ──
        div_y = 54
        draw.rectangle([TEXT_X, div_y, W - PAD, div_y + 1], fill=DIVIDER)

        # ─────── DKP BAR ───────
        curr_dkp = dkp_final * p
        pct_dkp  = int(curr_dkp)

        draw.text((TEXT_X, BAR_Y1 - 14), "DKP Progress", fill=GREY, font=font_label)

        pct_text = f"{pct_dkp}%"
        tw = draw.textlength(pct_text, font=font_pct)
        draw.text((W - PAD - tw, BAR_Y1 - 14), pct_text, fill=PURPLE, font=font_pct)

        # bg
        _draw_rounded_bar(draw, BAR_X, BAR_Y1, BAR_W, BAR_H, BAR_R, BAR_BG)
        # fill
        fill_w = int(BAR_W * min(curr_dkp, 100) / 100)
        if fill_w > 0:
            for px in range(fill_w):
                t = px / max(fill_w - 1, 1)
                r = int(PURPLE_D[0] + (PURPLE[0] - PURPLE_D[0]) * t)
                g = int(PURPLE_D[1] + (PURPLE[1] - PURPLE_D[1]) * t)
                b = int(PURPLE_D[2] + (PURPLE[2] - PURPLE_D[2]) * t)
                draw.rectangle([BAR_X + px, BAR_Y1, BAR_X + px, BAR_Y1 + BAR_H], fill=(r, g, b))
            # Clip to rounded shape by re-drawing bg outside
            _draw_rounded_bar(draw, BAR_X, BAR_Y1, BAR_W, BAR_H, BAR_R, (0, 0, 0, 0))
            # simpler: just draw gradient rect then round-mask with bg color on corners
            _draw_rounded_bar(draw, BAR_X, BAR_Y1, fill_w, BAR_H, BAR_R,
                              tuple(int(PURPLE_D[c] + (PURPLE[c] - PURPLE_D[c]) * (fill_w / BAR_W)) for c in range(3)))
        # shimmer
        if fill_w > 10:
            draw.rectangle([BAR_X + 6, BAR_Y1 + 3, BAR_X + fill_w - 4, BAR_Y1 + 5],
                           fill=(255, 255, 255, 40))

        # ─────── DEADS BAR ───────
        curr_dead = dead_final * p
        pct_dead  = int(curr_dead)

        draw.text((TEXT_X, BAR_Y2 - 14), "Deads Progress", fill=GREY, font=font_label)

        pct_text2 = f"{pct_dead}%"
        tw2 = draw.textlength(pct_text2, font=font_pct)
        draw.text((W - PAD - tw2, BAR_Y2 - 14), pct_text2, fill=RED, font=font_pct)

        _draw_rounded_bar(draw, BAR_X, BAR_Y2, BAR_W, BAR_H, BAR_R, BAR_BG)
        fill_w2 = int(BAR_W * min(curr_dead, 100) / 100)
        if fill_w2 > 0:
            _draw_rounded_bar(draw, BAR_X, BAR_Y2, fill_w2, BAR_H, BAR_R,
                              tuple(int(RED_D[c] + (RED[c] - RED_D[c]) * (fill_w2 / BAR_W)) for c in range(3)))
        if fill_w2 > 10:
            draw.rectangle([BAR_X + 6, BAR_Y2 + 3, BAR_X + fill_w2 - 4, BAR_Y2 + 5],
                           fill=(255, 255, 255, 40))

        frames.append(img)

    buffer = BytesIO()
    frames[0].save(
        buffer,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=int(1000 / fps),
        loop=1,
        optimize=False,
    )
    buffer.seek(0)
    return buffer

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

    index = rows.index[0]
    current = str(rows.iloc[0].get("Filler IDs", "") or "")
    fillers = [f.strip() for f in current.split(",") if f.strip()]

    if filler_id in fillers:
        await interaction.followup.send("Filler already linked.")
        return

    fillers.append(filler_id)

    await asyncio.to_thread(
        ws.update_cell,
        index + 2,
        3,
        ",".join(fillers)
    )

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

    await asyncio.to_thread(
        ws.update_cell,
        index + 2,
        3,
        ",".join(fillers)
    )

    await refresh_cache()
    await interaction.followup.send("Filler unlinked.")


@bot.tree.command(name="data")
async def data(interaction: discord.Interaction, link: str):
    await interaction.response.defer(ephemeral=True)

    if ADMIN_ROLE_ID and not any(
        r.id == ADMIN_ROLE_ID for r in interaction.user.roles
    ):
        await interaction.followup.send("No permission.")
        return

    global STATS_SHEET_ID
    STATS_SHEET_ID = extract_sheet_id(link)

    await refresh_cache()
    await interaction.followup.send("Stats sheet connected.")


# ================= KVK CONTROL =================

@bot.tree.command(name="kvk")
async def kvk(interaction: discord.Interaction, status: str):
    await interaction.response.defer(ephemeral=True)

    if ADMIN_ROLE_ID and not any(
        r.id == ADMIN_ROLE_ID for r in interaction.user.roles
    ):
        await interaction.followup.send("No permission.")
        return

    global KVK_ACTIVE

    status = status.lower()

    if status == "on":
        KVK_ACTIVE = True
        await interaction.followup.send("KvK activated.")
    elif status == "off":
        KVK_ACTIVE = False
        await interaction.followup.send("KvK deactivated.")
    else:
        await interaction.followup.send("Use: /kvk on  or  /kvk off")

# ================= MY_STATS =================

@bot.tree.command(name="my_stats")
async def my_stats(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)

    if not KVK_ACTIVE:
        embed = discord.Embed(
            title="⚔️ KvK Status",
            description="KvK has not started yet.",
            color=discord.Color.purple()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    sheets_dict = await get_sheets()
    links = sheets_dict.get("Links")

    if links is None or links.empty:
        await interaction.followup.send("Links sheet not loaded.")
        return

    rows = links[links["Discord ID"].astype(str) == str(interaction.user.id)]

    if rows.empty:
        await interaction.followup.send("You are not linked.")
        return

    main_id = str(rows.iloc[0]["Main ID"])
    row_index = rows.index[0] + 2
    ws = await get_links_ws()
    cell = await asyncio.to_thread(ws.cell, row_index, 3)
    filler_raw = cell.value or ""
    flinks = [fid.strip() for fid in str(filler_raw).split(",") if fid.strip()]

    overall_df = sheets_dict.get("Overall")
    req_df = sheets_dict.get("REQ")

    if overall_df is None:
        await interaction.followup.send("Overall sheet not loaded.")
        return

    # ================= MAIN =================

    main_name, main_power, main_current_power = "Unknown", 0, 0
    dkp_pct, dead_pct = 0, 0
    req_dkp = 0
    req_deads = 0

    m_row = overall_df[overall_df["ID"].astype(str) == main_id]

    if not m_row.empty:
        r = m_row.iloc[0]
        main_name = r.get("Name", "Unknown")
        main_power = clean_number(r.get("Initial Power", 0))
        main_current_power = clean_number(r.get("Current Power", 0))

        dkp = clean_number(r.get("DKP", 0))
        goal_dkp = clean_number(r.get("Goal DKP", 1))
        deads = clean_number(r.get("Deads", 0))
        req_deads_calc = clean_number(r.get("Required Deads", 1))

        if goal_dkp > 0:
            dkp_pct = dkp / goal_dkp * 100

        if req_deads_calc > 0:
            dead_pct = deads / req_deads_calc * 100

    # ===== REQUIRED FROM REQ =====

    if req_df is not None and not req_df.empty:
        req_row = req_df[req_df["ID"].astype(str) == main_id]

        if not req_row.empty:
            rr = req_row.iloc[0]
            req_dkp = clean_number(rr.get("Required DKP", 0))
            req_deads = clean_number(rr.get("Required Deads", 0))

    embed = discord.Embed(
        title="<:KvK:1476664387358949541>  KvK Statistics",
        color=0x7B2CBF
    )

    embed.description = (
        f"```ansi\n\u001b[1;35m{main_name}\u001b[0m```"
        f"┣ 🏰  **Power** \u2003`{fmt(main_power)}`\n"
        f"┗ ⚡  **Current Power** \u2003`{fmt(main_current_power)}`\n"
        f"\u200b\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋  **Requirements**\n"
        f"┣ 📌  **Required DKP** \u2003`{fmt(req_dkp)}`\n"
        f"┗ 💀  **Required Deads** \u2003`{fmt(req_deads)}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    # ================= ZONES =================

    EMOJI_ZONE = "<:KvK:1476664387358949541>"
    EMOJI_KP = "🎯"
    EMOJI_T4 = "<:T4:1476664385106739320>"
    EMOJI_T5 = "<:T5:1476664389095522475>"
    EMOJI_DEADS = "💀"

    stat_sheets = [
        name for name in sheets_dict.keys()
        if name not in ["Links", "REQ"]
    ]

    ordered_sheets = [
        s for s in stat_sheets if s.lower() != "overall"
    ]

    if any(s.lower() == "overall" for s in stat_sheets):
        ordered_sheets.append("Overall")

    for sheet_name in ordered_sheets:
        df = sheets_dict.get(sheet_name)

        if df is None or df.empty:
            continue

        row = df[df["ID"].astype(str) == main_id]

        if row.empty:
            continue

        r = row.iloc[0]

        kp = clean_number(r.get("KP", 0))
        t4 = clean_number(r.get("T4 Kills", 0))
        t5 = clean_number(r.get("T5 Kills", 0))
        ds = clean_number(r.get("Deads", 0))

        zone_block = (
            f"┣ {EMOJI_KP}  **KP** \u2003`{fmt(kp)}`\n"
            f"┣ {EMOJI_T4}  **T4 Kills** \u2003`{fmt(t4)}`\n"
            f"┣ {EMOJI_T5}  **T5 Kills** \u2003`{fmt(t5)}`\n"
            f"┗ {EMOJI_DEADS}  **Deads** \u2003`{fmt(ds)}`"
        )

        embed.add_field(
            name=f"╔ {EMOJI_ZONE}  {sheet_name}",
            value=zone_block,
            inline=False
        )

    # ================= FILLER BONUS (ORIGINAL LOGIC) =================

    total_bonus = 0
    bonus_lines = []

    if overall_df is not None and flinks:
        for fid in flinks:
            row_f = overall_df[
                overall_df["ID"].astype(str) == str(fid)
            ]

            if row_f.empty:
                continue

            f = row_f.iloc[0]

            fn = f.get("Name", "Unknown")
            p = clean_number(f.get("Initial Power", f.get("Power", 0)))
            df_ = clean_number(f.get("Deads", 0))
            req = p * FILLER_REQUIRED_PERCENT

            prog = (df_ / req * 100) if req > 0 else 0
            prog = min(max(prog, 0), 100)

            bonus = (df_ - req) * FILLER_BONUS_MULTIPLIER if prog >= 100 else 0
            total_bonus += bonus

            bar = "█" * int(prog / 10) + "─" * (10 - int(prog / 10))

            bonus_lines.append(
                f"┣ 🆔  `{fid}` — **{fn}**\n"
                f"┣ 💀  `{fmt(df_)}` / `{fmt(req)}`\n"
                f"┣ [{bar}] {int(prog)}%\n"
                f"┗ {f'✨  +`{fmt(bonus)}`' if prog >= 100 else '⚠️  Not qualified'}"
            )

    if bonus_lines:
        embed.add_field(
            name="╔ ✨  Filler Bonus (Deads)",
            value="\n\n".join(bonus_lines)
            + f"\n\n━━━━━━━━━━━━━━━━━━━━━━━━\n✨  **Total Bonus:** +`{fmt(total_bonus)}`",
            inline=False
        )

    gif_buf = await asyncio.to_thread(
        create_animated_progress_bar,
        dkp_pct,
        dead_pct,
        main_name,
        interaction.user.name,
    )

    file = discord.File(gif_buf, filename="progress.gif")
    embed.set_image(url="attachment://progress.gif")

    await interaction.followup.send(embed=embed, file=file)


# ================= REQ COMMAND =================

@bot.tree.command(name="req")
async def req(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=False)
    
    sheets_dict = await get_sheets()
    links = sheets_dict.get("Links")
    if links is None or links.empty:
        await interaction.followup.send("Links sheet not loaded.")
        return
    
    rows = links[links["Discord ID"].astype(str) == str(interaction.user.id)]
    if rows.empty:
        await interaction.followup.send("You are not linked.")
        return
    
    main_id = str(rows.iloc[0]["Main ID"])
    row_index = rows.index[0] + 2
    ws = await get_links_ws()
    cell = await asyncio.to_thread(ws.cell, row_index, 3)
    filler_raw = cell.value or ""
    filler_ids = [fid.strip() for fid in str(filler_raw).split(",") if fid.strip()]
    
    req_df = sheets_dict.get("REQ")
    if req_df is None:
        await interaction.followup.send("REQ sheet not loaded.")
        return
    
    req_row = req_df[req_df["ID"].astype(str) == main_id]
    if req_row.empty:
        await interaction.followup.send("No REQ data found for you.")
        return
    
    r = req_row.iloc[0]
    name = r.get("Name", "Unknown")
    power = clean_number(r.get("Power", 0))
    req_dkp = clean_number(r.get("Required DKP", 0))
    req_deads = clean_number(r.get("Required Deads", 0))
    
    pct_dkp = r.get("% DKP", "0%")
    pct_deads = r.get("% Deads", "0%")
    
    embed = discord.Embed(
        title="📋  Requirements",
        color=0x7B2CBF
    )

    embed.description = (
        f"```ansi\n\u001b[1;35m{name}\u001b[0m```"
        f"┗ 🏰  **Power** \u2003`{fmt(power)}`\n"
        f"\u200b\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌  **DKP**\n"
        f"┣ Required \u2003`{fmt(req_dkp)}`\n"
        f"┗ Progress \u2003`{pct_dkp}`\n"
        f"\u200b\n"
        f"💀  **Deads**\n"
        f"┣ Required \u2003`{fmt(req_deads)}`\n"
        f"┗ Progress \u2003`{pct_deads}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    
    # ================= FILLERS =================
    filler_blocks = []
    for fid in filler_ids:
        f_req_row = req_df[req_df["ID"].astype(str) == str(fid)]
        if f_req_row.empty:
            continue
        
        fr = f_req_row.iloc[0]
        f_name = fr.get("Name", "Unknown")
        f_power = clean_number(fr.get("Power", 0))
        
        # Required Deads = 2% del Power del filler
        f_required_deads = f_power * 0.02
        f_required_deads_fmt = fmt(round(f_required_deads))  # redondeamos para que se vea limpio
        
        filler_blocks.append(
            f"┣ 🆔  `{fid}` — **{f_name}**\n"
            f"┣ 🏰  **Power** \u2003`{fmt(f_power)}`\n"
            f"┗ 💀  **Required Deads** \u2003`{f_required_deads_fmt}` *(2%)*"
        )
    
    if filler_blocks:
        embed.add_field(
            name="╔ 🧩  Fillers",
            value="\n\n".join(filler_blocks),
            inline=False
        )
    # Si no hay fillers válidos, el campo simplemente no aparece
    
    await interaction.followup.send(embed=embed)

# ================= READY =================

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Bot online as {bot.user}")


bot.run(TOKEN)
