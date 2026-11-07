import asyncio
import hashlib
import smtplib
import hmac
import html
import json
import random
import re
import secrets
import sqlite3
import sys
import time
from email.message import EmailMessage
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from engine.poker_core import Card, Deck, evaluate_7_cards  # noqa: E402

app = FastAPI(title="Stratos Poker Engine - Enterprise Arena")

# The same page is served from this server (localhost / Tailscale Funnel) and from the
# stratos.games box, which calls this server across origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://stratos.games",
        "https://www.stratos.games",
        "https://stratos-technologies-fzco.github.io",
        "https://stratos-family-staging.vercel.app",     # Stratos staging (07-Nov-2026)
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

DB_PATH = str(BASE_DIR / "poker_arena.db")
STATIC_DIR = BASE_DIR / "static"

START_CHIPS = 1000
TURN_SECONDS = 60
AWAY_TURN_SECONDS = 5          # a player who lost connection is auto-checked/folded quickly
DAILY_BONUS_STEP = 100         # day 1 = Rs. 100 ... day 5+ of a streak = Rs. 500
DAILY_BONUS_MAX_DAYS = 5
REVIEW_SECONDS = 60            # after every hand: all cards face up for 60 s so everyone can study it

HAND_NAMES = ["High Card", "One Pair", "Two Pair", "Three of a Kind", "Straight",
              "Flush", "Full House", "Four of a Kind", "Straight Flush"]

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")


# ---------------------------------------------------------------- database

def db() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH, timeout=10)


def init_db():
    conn = db()
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                player_id TEXT PRIMARY KEY,
                player_name TEXT NOT NULL,
                chips INTEGER NOT NULL DEFAULT 1000,
                hands_played INTEGER NOT NULL DEFAULT 0,
                hands_won INTEGER NOT NULL DEFAULT 0,
                total_winnings INTEGER NOT NULL DEFAULT 0
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS hand_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                table_id TEXT NOT NULL,
                hand_timestamp INTEGER NOT NULL,
                winner_id TEXT NOT NULL,
                winner_name TEXT NOT NULL,
                pot_won INTEGER NOT NULL,
                winning_hand TEXT NOT NULL,
                board_cards TEXT NOT NULL
            );
        """)
        # Registration columns (added 07-Nov-2026); old rows keep NULLs.
        cols = {r[1] for r in cur.execute("PRAGMA table_info(users)")}
        for name, decl in [("email", "TEXT"), ("phone", "TEXT"), ("token", "TEXT"),
                           ("created_at", "INTEGER"), ("last_bonus_day", "TEXT"),
                           ("streak", "INTEGER NOT NULL DEFAULT 0"),
                           # PIN sign-in (added 07-Nov-2026): hashed PIN + hashed recovery key
                           ("pin_hash", "TEXT"), ("recovery_hash", "TEXT"),
                           ("pin_fails", "INTEGER NOT NULL DEFAULT 0"), ("pin_locked_until", "INTEGER")]:
            if name not in cols:
                cur.execute(f"ALTER TABLE users ADD COLUMN {name} {decl}")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS email_otps (
                email TEXT PRIMARY KEY,
                code_hash TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                sent_at INTEGER NOT NULL,
                hour_start INTEGER NOT NULL,
                hour_count INTEGER NOT NULL DEFAULT 0
            );
        """)
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_users_token ON users(token)")
        conn.commit()
    finally:
        conn.close()


def clean_name(raw) -> str:
    name = re.sub(r"[^\w .-]", "", str(raw or "")).strip()
    return re.sub(r"\s+", " ", name)[:15].strip()


def clean_phone(raw) -> str:
    text = str(raw or "").strip()
    digits = re.sub(r"\D", "", text)
    if not 8 <= len(digits) <= 15:
        return ""
    return ("+" if text.startswith("+") else "") + digits


def same_phone(a: str, b: str) -> bool:
    da, dbb = re.sub(r"\D", "", a or ""), re.sub(r"\D", "", b or "")
    return bool(da) and da[-10:] == dbb[-10:]


PIN_RE = re.compile(r"^\d{4,6}$")
PIN_MAX_FAILS = 5              # wrong PINs / reset attempts before the account is locked for a while
PIN_LOCK_SECONDS = 15 * 60


def hash_secret(secret: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode(), salt, 200_000)
    return salt.hex() + "$" + digest.hex()


def check_secret(secret: str, stored: Optional[str]) -> bool:
    if not stored or "$" not in stored:
        return False
    salt_hex, digest_hex = stored.split("$", 1)
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode(), bytes.fromhex(salt_hex), 200_000)
    return hmac.compare_digest(digest.hex(), digest_hex)


def new_recovery_key() -> str:
    """12 letters/digits shown once, e.g. K7QM-3XWD-P9TA; used to reset a forgotten PIN."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def clean_recovery_key(raw) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(raw or "")).upper()


def find_user_row(conn, email: str = "", phone: str = ""):
    """Looks a player up by email id, else by phone (last 10 digits). Returns (row, error)."""
    cols = "player_id, email, phone, pin_hash, recovery_hash, pin_fails, pin_locked_until, token"
    if email:
        row = conn.execute(f"SELECT {cols} FROM users WHERE email = ?", (email,)).fetchone()
        return row, None if row else "No player is registered with this email id."
    if phone:
        rows = [r for r in conn.execute(f"SELECT {cols} FROM users WHERE phone IS NOT NULL").fetchall()
                if same_phone(r[2], phone)]
        if len(rows) > 1:
            return None, "More than one player uses this phone number. Please use your email id."
        return (rows[0], None) if rows else (None, "No player is registered with this phone number.")
    return None, "Enter your email id or phone number."


def locked_message(row) -> Optional[str]:
    until = row[6] or 0
    if until > time.time():
        minutes = max(1, int((until - time.time()) // 60) + 1)
        return f"Too many wrong tries. Please wait {minutes} min and try again."
    return None


def note_failure(conn, player_id: str, fails: int):
    fails += 1
    locked = int(time.time()) + PIN_LOCK_SECONDS if fails >= PIN_MAX_FAILS else None
    conn.execute("UPDATE users SET pin_fails = ?, pin_locked_until = ? WHERE player_id = ?",
                 (0 if locked else fails, locked, player_id))
    conn.commit()


# ---- email OTP (one-time code sent from the owner's Gmail)
# email_otp.json (never committed) = {"sender": "...@gmail.com", "app_password": "...."}.
# Made by setup_email_otp.bat. Read on every request, so it works without a restart.
# While the file is missing, sign-in works as before (email + phone).
EMAIL_OTP_FILE = BASE_DIR / "email_otp.json"
OTP_SECONDS = 10 * 60          # a code is valid for 10 minutes
OTP_RESEND_SECONDS = 60        # one code per email id per minute
OTP_PER_HOUR = 5               # codes per email id per hour
OTP_MAX_TRIES = 5              # wrong codes before the code is thrown away
OTP_GLOBAL_PER_HOUR = 100      # all emails per hour (stays far below Gmail's daily limit)
_otp_sent_times: List[float] = []


def email_otp_config() -> Optional[dict]:
    try:
        cfg = json.loads(EMAIL_OTP_FILE.read_text(encoding="utf-8-sig"))
    except Exception:
        return None
    if isinstance(cfg, dict) and EMAIL_RE.match(str(cfg.get("sender", ""))) and cfg.get("app_password"):
        return cfg
    return None


def send_otp_mail(cfg: dict, to: str, code: str):
    msg = EmailMessage()
    msg["Subject"] = f"Stratos Poker code: {code}"
    msg["From"] = f"Stratos Poker <{cfg['sender']}>"
    msg["To"] = to
    msg.set_content(f"Your Stratos Poker code is {code}\n\n"
                    f"It works for 10 minutes. If you did not ask for it, ignore this email.")
    with smtplib.SMTP(cfg.get("smtp_host", "smtp.gmail.com"), int(cfg.get("smtp_port", 587)), timeout=20) as s:
        s.starttls()
        s.login(cfg["sender"], str(cfg["app_password"]).replace(" ", ""))
        s.send_message(msg)


async def issue_otp(email: str) -> Optional[str]:
    """Emails a fresh 6-digit code. Returns an error message, or None when sent."""
    cfg = email_otp_config()
    if not cfg:
        return "Email codes are not set up on the server."
    now = int(time.time())
    conn = db()
    try:
        row = conn.execute("SELECT sent_at, hour_start, hour_count FROM email_otps WHERE email = ?",
                           (email,)).fetchone()
        hour_start, hour_count = (row[1], row[2]) if row else (now, 0)
        if row and now - row[0] < OTP_RESEND_SECONDS:
            return f"A code was just sent. Please wait {OTP_RESEND_SECONDS - (now - row[0])} s before asking again."
        if now - hour_start >= 3600:
            hour_start, hour_count = now, 0
        if hour_count >= OTP_PER_HOUR:
            return "Too many codes for this email id. Please try again in an hour."
    finally:
        conn.close()
    _otp_sent_times[:] = [x for x in _otp_sent_times if now - x < 3600]
    if len(_otp_sent_times) >= OTP_GLOBAL_PER_HOUR:
        return "The server is busy sending codes. Please try again later."

    code = f"{secrets.randbelow(1_000_000):06d}"
    try:
        await asyncio.to_thread(send_otp_mail, cfg, email, code)
    except Exception as e:
        print(f"[otp] email to {email} failed: {e!r}")
        return "Could not send the email. Please check the email id and try again."
    _otp_sent_times.append(now)
    conn = db()
    try:
        conn.execute("""INSERT OR REPLACE INTO email_otps (email, code_hash, expires_at, attempts, sent_at,
                                                           hour_start, hour_count)
                        VALUES (?, ?, ?, 0, ?, ?, ?)""",
                     (email, hash_secret(code), now + OTP_SECONDS, now, hour_start, hour_count + 1))
        conn.commit()
    finally:
        conn.close()
    return None


def check_otp(email: str, code: str) -> Optional[str]:
    """Returns an error message, or None when the code is right (the code is then used up)."""
    code = re.sub(r"\D", "", str(code or ""))
    conn = db()
    try:
        row = conn.execute("SELECT code_hash, expires_at, attempts FROM email_otps WHERE email = ?",
                           (email,)).fetchone()
        if not row or row[0] == "":
            return "Ask for a new code first."
        if row[1] < time.time():
            return "This code has expired. Ask for a new code."
        if row[2] >= OTP_MAX_TRIES:
            return "Too many wrong codes. Ask for a new code."
        if not check_secret(code, row[0]):
            conn.execute("UPDATE email_otps SET attempts = attempts + 1 WHERE email = ?", (email,))
            conn.commit()
            return "Wrong code."
        conn.execute("UPDATE email_otps SET code_hash = '', expires_at = 0 WHERE email = ?", (email,))
        conn.commit()
        return None
    finally:
        conn.close()


async def otp_step(email: str, code):
    """Shared by sign-up/sign-in and Forgot PIN. Returns None when the email is proven
    (or email codes are off), otherwise the response to send back."""
    if not email_otp_config():
        return None
    if not str(code or "").strip():
        err = await issue_otp(email)
        if err:
            busy = "wait" in err or "Too many" in err or "busy" in err
            return bad(err, 429 if busy else 503)
        return JSONResponse({"otp_sent": True,
                             "message": f"We emailed a 6-digit code to {email}. Enter it below."})
    err = check_otp(email, code)
    return bad(err, 401) if err else None


def user_by_token(token: str) -> Optional[Tuple[str, str]]:
    if not token or len(token) < 20:
        return None
    conn = db()
    try:
        row = conn.execute("SELECT player_id, player_name FROM users WHERE token = ?", (token,)).fetchone()
    finally:
        conn.close()
    return (row[0], row[1]) if row else None


def profile_with_daily_bonus(player_id: str) -> dict:
    """Returns the player's profile; the first visit of each day adds the streak bonus."""
    today = date.today()
    conn = db()
    try:
        row = conn.execute("""SELECT player_name, chips, hands_played, hands_won, total_winnings,
                                     last_bonus_day, streak FROM users WHERE player_id = ?""",
                           (player_id,)).fetchone()
        if row is None:
            return {}
        name, chips, played, won, winnings, last_day, streak = row
        bonus = 0
        if last_day != today.isoformat():
            streak = (streak or 0) + 1 if last_day == (today - timedelta(days=1)).isoformat() else 1
            bonus = DAILY_BONUS_STEP * min(streak, DAILY_BONUS_MAX_DAYS)
            chips += bonus
            conn.execute("UPDATE users SET chips = ?, last_bonus_day = ?, streak = ? WHERE player_id = ?",
                         (chips, today.isoformat(), streak, player_id))
            conn.commit()
            for t in TABLES.values():          # keep a seated player's table stack in step
                p = t.players.get(player_id)
                if p:
                    p["chips"] += bonus
    finally:
        conn.close()
    return {"player_id": player_id, "name": name, "chips": chips, "hands_played": played,
            "hands_won": won, "total_winnings": winnings, "streak": streak or 0, "daily_bonus": bonus}


def load_user_stats(player_id: str) -> dict:
    conn = db()
    try:
        row = conn.execute("SELECT chips, hands_played, hands_won, total_winnings FROM users WHERE player_id = ?",
                           (player_id,)).fetchone()
        if row is None:
            return {"chips": START_CHIPS, "hands_played": 0, "hands_won": 0, "total_winnings": 0}
        chips, played, won, winnings = row
        if chips <= 0:
            chips = START_CHIPS
            conn.execute("UPDATE users SET chips = ? WHERE player_id = ?", (chips, player_id))
            conn.commit()
        return {"chips": chips, "hands_played": played, "hands_won": won, "total_winnings": winnings}
    finally:
        conn.close()


def update_user_chips(player_id: str, chips: int):
    if player_id.startswith("bot_"):
        return
    conn = db()
    try:
        conn.execute("UPDATE users SET chips = ? WHERE player_id = ?", (chips, player_id))
        conn.commit()
    finally:
        conn.close()


def record_hand_completion(table_id: str, winner_id: str, winner_name: str, pot: int, hand_desc: str,
                           board: list, dealt_pids: list):
    conn = db()
    try:
        conn.execute("""
            INSERT INTO hand_history (table_id, hand_timestamp, winner_id, winner_name, pot_won, winning_hand, board_cards)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (table_id, int(time.time()), winner_id, winner_name, pot, hand_desc, " ".join(board)))
        for pid in dealt_pids:
            if not pid.startswith("bot_"):
                conn.execute("UPDATE users SET hands_played = hands_played + 1 WHERE player_id = ?", (pid,))
        if not winner_id.startswith("bot_"):
            conn.execute("UPDATE users SET hands_won = hands_won + 1, total_winnings = total_winnings + ? WHERE player_id = ?",
                         (pot, winner_id))
        conn.commit()
    finally:
        conn.close()


init_db()


# ---------------------------------------------------------------- poker helpers

def hand_score(cards: List[str]):
    """Full comparable score (category, tie-breakers) for 5-7 cards given as strings like 'Ah'."""
    objs = [Card(c[0], c[1]) for c in cards if c and len(c) == 2]
    if len(objs) < 5:
        return (-1, [])
    return evaluate_7_cards(objs)


def calculate_side_pots(players: List[dict]) -> List[dict]:
    contributors = [p for p in players if p.get("total_contributed", 0) > 0]
    if not contributors:
        return []

    levels = sorted(set(p["total_contributed"] for p in contributors))
    pots: List[dict] = []
    prev_level = 0
    carry = 0
    for level in levels:
        payers = [p for p in contributors if p["total_contributed"] >= level]
        amount = (level - prev_level) * len(payers) + carry
        carry = 0
        eligible = [p["id"] for p in payers if not p.get("folded", False)]
        if amount > 0:
            if eligible:
                pots.append({"amount": amount, "eligible": eligible})
            elif pots:
                pots[-1]["amount"] += amount   # chips only folded players reached: give to the pot below
            else:
                carry = amount
        prev_level = level
    return pots


# ---------------------------------------------------------------- table

class PokerTable:
    def __init__(self, table_id: str, name: str, small_blind: int = 10, big_blind: int = 20):
        self.table_id = table_id
        self.name = name
        self.small_blind = small_blind
        self.big_blind = big_blind
        self.connections: Dict[str, WebSocket] = {}
        self.players: Dict[str, dict] = {}
        self.action_logs: List[dict] = []
        self.lock = asyncio.Lock()

        self.countdown_task = None
        self.reset_task = None
        self.turn_timer_task = None
        self.turn_seq = 0

        self.game_started = False
        self.hand_no = 0
        self.deck = None
        self.community_cards: List[str] = []
        self.pot = 0
        self.current_bet = 0
        self.last_raise = big_blind
        self.active_turn_seat: Optional[int] = None
        self.turn_time_left = TURN_SECONDS
        self.dealer_seat = -1
        self.stage = "WAITING"
        self.acted_players = set()
        self.winner_banner = ""
        self.last_chat_ts: Dict[str, float] = {}
        self.review: dict = {}
        self.review_left = 0

    # ---- small helpers
    def add_log(self, text: str, sender: str = "DEALER"):
        self.action_logs.append({"sender": sender, "text": text})
        if len(self.action_logs) > 60:
            self.action_logs.pop(0)

    def player_at(self, seat) -> Optional[dict]:
        return next((p for p in self.players.values() if p["seat"] == seat), None)

    def in_hand(self) -> List[dict]:
        return [p for p in self.players.values() if p["cards"] and not p["folded"]]

    def can_act(self) -> List[dict]:
        return [p for p in self.in_hand() if not p["all_in"]]

    def seats_after(self, seat, seats: List[int]) -> List[int]:
        seats = sorted(seats)
        return [s for s in seats if s > seat] + [s for s in seats if s <= seat]

    def next_needing_action(self, after_seat) -> Optional[int]:
        actors = {p["seat"]: p for p in self.can_act()}
        for s in self.seats_after(after_seat if after_seat is not None else -1, list(actors)):
            p = actors[s]
            if p["id"] not in self.acted_players or p["current_bet"] < self.current_bet:
                return s
        return None

    def betting_round_done(self) -> bool:
        actors = self.can_act()
        if not actors:
            return True
        if len(actors) == 1 and actors[0]["current_bet"] >= self.current_bet:
            return True     # nobody left who could answer a bet
        return all(p["id"] in self.acted_players and p["current_bet"] == self.current_bet for p in actors)

    def min_raise_to(self) -> int:
        return self.current_bet + max(self.last_raise, self.big_blind)

    def new_player(self, pid, name, chips, hands_won, seat, is_bot) -> dict:
        return {"id": pid, "name": name, "chips": chips, "hands_won": hands_won, "seat": seat,
                "cards": [], "current_bet": 0, "total_contributed": 0,
                # someone sitting down mid-hand waits for the next deal
                "folded": self.game_started, "all_in": False, "is_bot": is_bot,
                "connected": True, "leaving": False}

    def get_summary(self):
        return {
            "table_id": self.table_id,
            "name": self.name,
            "blinds": f"Rs. {self.small_blind} / {self.big_blind}",
            "players_count": len(self.players),
            "max_players": 5,
            "stage": self.stage,
            "pot": self.pot,
        }

    # ---- seating
    async def connect(self, websocket: WebSocket, player_id: str, player_name: str) -> bool:
        for t in TABLES.values():
            if t is not self and player_id in t.players:
                await websocket.send_text(json.dumps({
                    "type": "ALREADY_SEATED",
                    "message": f"You are still seated at {t.name}. Go back there, or wait a few seconds for that hand to finish."}))
                return False

        safe_name = html.escape(player_name.strip())[:15] or f"User_{random.randint(100, 999)}"
        old_ws = self.connections.get(player_id)
        self.connections[player_id] = websocket
        if old_ws is not None and old_ws is not websocket:
            try:
                await old_ws.close(code=4000)      # same player opened a second tab
            except Exception:
                pass

        if player_id in self.players:
            self.players[player_id]["name"] = safe_name
            self.players[player_id]["connected"] = True
            self.players[player_id]["leaving"] = False
            self.add_log(f"{safe_name} reconnected.", "SYSTEM")
        else:
            taken = [p["seat"] for p in self.players.values()]
            free = [s for s in range(5) if s not in taken]
            if not free:
                bots = [p for p in self.players.values() if p["is_bot"]]
                idle_bots = [b for b in bots if not (self.game_started and b["cards"] and not b["folded"])]
                if idle_bots:
                    kicked = idle_bots[-1]
                    self.players.pop(kicked["id"], None)
                    free = [kicked["seat"]]
                    self.add_log(f"{kicked['name']} left to make room for {safe_name}.", "SYSTEM")
                else:
                    if bots:
                        bots[-1]["leaving"] = True
                        msg = "A bot will leave after this hand. Try this table again in a few seconds."
                    else:
                        msg = "Table is at max capacity (5/5)."
                    self.connections.pop(player_id, None)
                    await websocket.send_text(json.dumps({"type": "TABLE_FULL", "message": msg}))
                    return False

            stats = load_user_stats(player_id)
            self.players[player_id] = self.new_player(player_id, safe_name, stats["chips"], stats["hands_won"],
                                                      free[0], False)
            suffix = " (plays from next hand)" if self.game_started else ""
            self.add_log(f"{safe_name} joined Seat {free[0] + 1}{suffix}.", "SYSTEM")

        await self.broadcast_state()
        await self.evaluate_game_start()
        return True

    def add_bot(self) -> bool:
        taken = [p["seat"] for p in self.players.values()]
        free = [s for s in range(5) if s not in taken]
        if not free:
            return False
        bot_names = ["Bot_Alpha", "Bot_Bravo", "Bot_Charlie", "Bot_Delta", "Bot_Echo"]
        existing = [p["name"] for p in self.players.values()]
        name = next((b for b in bot_names if b not in existing), f"Bot_{random.randint(10, 99)}")
        bot_id = f"bot_{secrets.token_hex(4)}"
        self.players[bot_id] = self.new_player(bot_id, name, START_CHIPS, 0, free[0], True)
        self.add_log(f"🤖 {name} joined Seat {free[0] + 1}.", "SYSTEM")
        return True

    def remove_bot(self) -> bool:
        bots = [p for p in self.players.values() if p["is_bot"] and not p["leaving"]]
        if not bots:
            return False
        idle = [b for b in bots if not (self.game_started and b["cards"] and not b["folded"])]
        if idle:
            leaving = idle[-1]
            self.players.pop(leaving["id"], None)
            self.add_log(f"🤖 {leaving['name']} was removed.", "SYSTEM")
        else:
            leaving = bots[-1]
            leaving["leaving"] = True
            self.add_log(f"🤖 {leaving['name']} will leave after this hand.", "SYSTEM")
        return True

    async def handle_disconnect(self, player_id: str, websocket: WebSocket):
        if self.connections.get(player_id) is not websocket:
            return                       # a newer tab of the same player is still connected
        self.connections.pop(player_id, None)
        player = self.players.get(player_id)
        if not player:
            return
        player["connected"] = False
        self.add_log(f"{player['name']} disconnected.", "SYSTEM")

        if not self.game_started or not player["cards"]:
            self.players.pop(player_id, None)
            await self.broadcast_state()
            return
        if self.stage != "SHOWDOWN" and player["seat"] == self.active_turn_seat and not player["folded"]:
            await self.handle_player_action(player_id, "FOLD", is_timeout=True)
        else:
            await self.broadcast_state()

    # ---- state out
    async def broadcast_state(self, banner_msg: Optional[str] = None, is_alert: bool = False,
                              sound_event: Optional[str] = None):
        players = list(self.players.values())
        turn_p = self.player_at(self.active_turn_seat)
        for pid, ws in list(self.connections.items()):
            sanitized = []
            for p in players:
                show = p["id"] == pid or (self.stage == "SHOWDOWN" and bool(p["cards"]))
                sanitized.append({
                    "id": p["id"], "name": p["name"], "chips": p["chips"],
                    "hands_won": p.get("hands_won", 0), "seat": p["seat"],
                    "current_bet": p["current_bet"], "folded": p["folded"],
                    "all_in": p["all_in"], "is_bot": p["is_bot"], "connected": p["connected"],
                    "in_hand": bool(p["cards"]) and not p["folded"],
                    "hand_desc": p.get("hand_desc", "") if self.stage == "SHOWDOWN" and show else "",
                    "cards": p["cards"] if show else (["??", "??"] if p["cards"] else []),
                })
            is_my_turn = bool(turn_p and turn_p["id"] == pid and not turn_p["folded"] and not turn_p["all_in"]
                              and self.stage not in ("WAITING", "SHOWDOWN"))
            payload = {
                "type": "STATE_UPDATE",
                "table_id": self.table_id,
                "table_name": self.name,
                "hand_no": self.hand_no,
                "players": sanitized,
                "pot": self.pot,
                "current_bet": self.current_bet,
                "min_raise_to": self.min_raise_to(),
                "big_blind": self.big_blind,
                "active_turn_seat": self.active_turn_seat,
                "turn_time_left": self.turn_time_left,
                "dealer_seat": self.dealer_seat,
                "stage": self.stage,
                "community_cards": self.community_cards,
                "banner_message": banner_msg or self.default_banner(len(players)),
                "is_alert": is_alert,
                "is_my_turn": is_my_turn,
                "sound_event": sound_event,
                "logs": self.action_logs[-20:],
                "review": self.review if self.stage == "SHOWDOWN" else None,
                "review_left": self.review_left if self.stage == "SHOWDOWN" else 0,
            }
            try:
                await ws.send_text(json.dumps(payload))
            except Exception:
                pass

    def default_banner(self, count: int) -> str:
        if self.stage == "WAITING":
            return f"Waiting for players... ({count}/3 minimum seated)"
        if self.winner_banner and self.stage == "SHOWDOWN":
            return self.winner_banner
        turn_p = self.player_at(self.active_turn_seat)
        active_name = turn_p["name"] if turn_p else "None"
        return f"{self.name} | Pot: Rs. {self.pot} | Turn: {active_name} ({self.turn_time_left}s)"

    # ---- start of a hand
    def ready_players(self) -> List[dict]:
        return [p for p in self.players.values() if p["connected"] and p["chips"] > 0 and not p["leaving"]]

    async def evaluate_game_start(self):
        if self.game_started:
            return
        count = len(self.ready_players())
        if count >= 5:
            if self.countdown_task and not self.countdown_task.done():
                self.countdown_task.cancel()
            await self.start_hand()
        elif count >= 3:
            if not self.countdown_task or self.countdown_task.done():
                self.countdown_task = asyncio.create_task(self.run_grace_countdown(60))
        else:
            if self.countdown_task and not self.countdown_task.done():
                self.countdown_task.cancel()
            await self.broadcast_state()

    async def run_grace_countdown(self, seconds: int):
        try:
            for remaining in range(seconds, 0, -5):
                if self.game_started:
                    return
                count = len(self.ready_players())
                if count >= 5:
                    async with self.lock:
                        if not self.game_started:
                            await self.start_hand()
                    return
                if count < 3:
                    await self.broadcast_state("Waiting for 3 players...")
                    return
                await self.broadcast_state(f"🚨 {self.name}: Hand starts in {remaining}s... ({count}/5 players)",
                                           is_alert=True)
                await asyncio.sleep(5)
            async with self.lock:
                if not self.game_started and len(self.ready_players()) >= 3:
                    await self.start_hand()
        except asyncio.CancelledError:
            pass

    def post_blind(self, p: dict, amount: int) -> int:
        amt = min(p["chips"], amount)
        p["chips"] -= amt
        p["current_bet"] = amt
        p["total_contributed"] = amt
        if p["chips"] == 0:
            p["all_in"] = True
        update_user_chips(p["id"], p["chips"])
        return amt

    async def start_hand(self):
        # people who left during the last hand give their seat back now; broke bots reload
        for pid in [p["id"] for p in self.players.values()
                    if p["leaving"] or (not p["is_bot"] and not p["connected"])]:
            self.players.pop(pid, None)
        for p in self.players.values():
            if p["is_bot"] and p["chips"] <= 0:
                p["chips"] = START_CHIPS

        for p in self.players.values():
            p.update(cards=[], folded=True, all_in=False, current_bet=0, total_contributed=0, hand_desc="")

        eligible = sorted(self.ready_players(), key=lambda x: x["seat"])
        if len(eligible) < 2:
            self.game_started = False
            self.stage = "WAITING"
            self.active_turn_seat = None
            self.pot = 0
            self.community_cards = []
            await self.broadcast_state("Waiting for players with chips...")
            return

        self.game_started = True
        self.hand_no += 1
        self.stage = "PREFLOP"
        self.winner_banner = ""
        self.review = {}
        self.deck = Deck()
        self.community_cards = []
        self.acted_players.clear()
        self.last_raise = self.big_blind

        for p in eligible:
            p["cards"] = [str(c) for c in self.deck.draw(2)]
            p["folded"] = False

        seats = [p["seat"] for p in eligible]
        self.dealer_seat = self.seats_after(self.dealer_seat, seats)[0]
        order = self.seats_after(self.dealer_seat, seats)      # starts left of the dealer, dealer last
        if len(eligible) == 2:
            sb, bb = self.player_at(self.dealer_seat), self.player_at(order[0])   # heads-up: dealer posts SB
        else:
            sb, bb = self.player_at(order[0]), self.player_at(order[1])
        dealer = self.player_at(self.dealer_seat)

        sb_amt = self.post_blind(sb, self.small_blind)
        bb_amt = self.post_blind(bb, self.big_blind)
        self.pot = sb_amt + bb_amt
        self.current_bet = self.big_blind
        self.add_log(f"--- Hand #{self.hand_no} (Dealer: {dealer['name']}) ---", "SYSTEM")
        self.add_log(f"{sb['name']} posts SB (Rs. {sb_amt}), {bb['name']} posts BB (Rs. {bb_amt})", "DEALER")

        self.active_turn_seat = self.next_needing_action(bb["seat"])
        if self.active_turn_seat is None or self.betting_round_done():
            self.active_turn_seat = None
            await self.broadcast_state(sound_event="deal")
            await self.next_stage()
            return
        msg = f"♠️ Hand Live! Dealer: {dealer['name']} | SB: {sb['name']}, BB: {bb['name']}"
        await self.broadcast_state(msg, sound_event="deal")
        self.start_turn_timer()

    # ---- turn clock and bots
    def cancel_turn_timer(self):
        t = self.turn_timer_task
        if t and not t.done() and t is not asyncio.current_task():
            t.cancel()

    def start_turn_timer(self):
        self.cancel_turn_timer()
        self.turn_seq += 1
        seq = self.turn_seq
        p = self.player_at(self.active_turn_seat)
        self.turn_time_left = TURN_SECONDS if (p and p["connected"]) else AWAY_TURN_SECONDS
        self.turn_timer_task = asyncio.create_task(self.run_turn_timer(seq))
        if p and p["is_bot"]:
            asyncio.create_task(self.bot_turn(seq, p["id"]))

    async def run_turn_timer(self, seq: int):
        try:
            while self.turn_time_left > 0:
                await asyncio.sleep(1)
                if seq != self.turn_seq:
                    return
                self.turn_time_left -= 1
                await self.broadcast_state()
            async with self.lock:
                if seq != self.turn_seq:
                    return
                p = self.player_at(self.active_turn_seat)
                if p and not p["folded"] and not p["all_in"]:
                    auto = "CHECK" if p["current_bet"] >= self.current_bet else "FOLD"
                    self.add_log(f"{p['name']} timed out (Auto {auto}).", "TIMEOUT")
                    await self.handle_player_action(p["id"], auto, is_timeout=True)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[{self.table_id}] turn timer error: {e!r}")

    def bot_decide(self, bot: dict) -> Tuple[str, int]:
        to_call = max(0, self.current_bet - bot["current_bet"])
        can_check = to_call == 0
        bb = self.big_blind
        if self.stage == "PREFLOP":
            c1, c2 = bot["cards"]
            ranks = "23456789TJQKA"
            strong = c1[0] == c2[0] or ranks.find(c1[0]) >= 8 or ranks.find(c2[0]) >= 8
            if strong:
                action = "CALL" if to_call > 3 * bb else ("RAISE" if random.random() < 0.35 else "CALL")
            else:
                action = "CALL" if to_call <= bb else "FOLD"
        else:
            category = hand_score(bot["cards"] + self.community_cards)[0]
            if category >= 2:
                action = "RAISE" if random.random() < 0.6 else "CALL"
            elif category == 1:
                action = "CALL" if to_call <= 4 * bb else "FOLD"
            else:
                action = "CALL" if to_call <= bb and random.random() < 0.3 else "FOLD"
        if can_check and action in ("CALL", "FOLD"):
            action = "CHECK"
        raise_to = 0
        if action == "RAISE":
            raise_to = max(self.min_raise_to(), self.current_bet * 2)
        return action, raise_to

    async def bot_turn(self, seq: int, bot_id: str):
        try:
            await asyncio.sleep(random.uniform(1.2, 2.5))
            async with self.lock:
                if seq != self.turn_seq:
                    return
                bot = self.players.get(bot_id)
                if not bot or bot["seat"] != self.active_turn_seat or bot["folded"] or bot["all_in"]:
                    return
                action, raise_to = self.bot_decide(bot)
                await self.handle_player_action(bot_id, action, raise_to=raise_to)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[{self.table_id}] bot error: {e!r}")

    # ---- actions
    async def dispatch(self, player_id: str, data: dict):
        action = str(data.get("action", ""))
        if action in ("CHECK", "CALL", "FOLD"):
            await self.handle_player_action(player_id, action)
        elif action == "RAISE":
            try:
                amount = int(data.get("amount", 0))
            except (TypeError, ValueError):
                return
            await self.handle_player_action(player_id, "RAISE", raise_to=amount)
        elif action == "REBUY":
            await self.handle_rebuy(player_id)
        elif action == "CHAT":
            await self.handle_chat(player_id, str(data.get("message", "")))
        elif action == "ADD_BOT":
            if player_id in self.players and self.add_bot():
                await self.broadcast_state()
                await self.evaluate_game_start()
        elif action == "REMOVE_BOT":
            if player_id in self.players and self.remove_bot():
                await self.broadcast_state()

    async def handle_player_action(self, player_id: str, action: str, raise_to: int = 0, is_timeout: bool = False):
        player = self.players.get(player_id)
        if (not self.game_started or self.stage in ("WAITING", "SHOWDOWN") or not player
                or player["seat"] != self.active_turn_seat or player["folded"] or player["all_in"]):
            return
        to_call = max(0, self.current_bet - player["current_bet"])
        if action == "CHECK" and to_call > 0:
            return                      # not allowed while facing a bet
        if action == "RAISE":
            max_to = player["current_bet"] + player["chips"]
            target = min(max(self.min_raise_to(), raise_to), max_to)
            if target <= self.current_bet:
                action = "CALL"         # cannot raise: only enough chips to call
        if action == "CALL" and to_call == 0:
            action = "CHECK"

        self.cancel_turn_timer()
        sound = "bet"
        if action == "FOLD":
            player["folded"] = True
            sound = "fold"
            self.add_log(f"{player['name']} folds.", "ACTION")
        elif action == "CHECK":
            sound = "check"
            self.add_log(f"{player['name']} checks.", "ACTION")
        elif action == "CALL":
            amount = min(player["chips"], to_call)
            self.put_in(player, amount)
            tail = " (All-In!)" if player["all_in"] else ""
            self.add_log(f"{player['name']} calls Rs. {amount}{tail}.", "ACTION")
        elif action == "RAISE":
            self.put_in(player, target - player["current_bet"])
            if player["current_bet"] > self.current_bet:
                raise_size = player["current_bet"] - self.current_bet
                if raise_size >= self.last_raise:
                    self.last_raise = raise_size
                self.current_bet = player["current_bet"]
                self.acted_players.clear()
            tail = " (All-In!)" if player["all_in"] else ""
            self.add_log(f"{player['name']} raises to Rs. {player['current_bet']}{tail}.", "ACTION")

        self.acted_players.add(player_id)
        await self.advance_turn(sound_event=sound)

    def put_in(self, player: dict, amount: int):
        amount = max(0, min(amount, player["chips"]))
        player["chips"] -= amount
        player["current_bet"] += amount
        player["total_contributed"] += amount
        self.pot += amount
        if player["chips"] == 0:
            player["all_in"] = True
        update_user_chips(player["id"], player["chips"])

    async def handle_chat(self, player_id: str, text: str):
        now = time.time()
        if now - self.last_chat_ts.get(player_id, 0) < 0.8:
            return
        self.last_chat_ts[player_id] = now
        player = self.players.get(player_id)
        if player and text.strip():
            self.add_log(html.escape(text.strip())[:100], player["name"])
            await self.broadcast_state()

    async def handle_rebuy(self, player_id: str):
        player = self.players.get(player_id)
        if not player or player["chips"] > 0:
            return
        in_this_hand = self.game_started and self.stage != "SHOWDOWN" and player["cards"] and not player["folded"]
        if in_this_hand:
            return
        player["chips"] = START_CHIPS
        update_user_chips(player["id"], START_CHIPS)
        self.add_log(f"{player['name']} reloaded Rs. {START_CHIPS}.", "SYSTEM")
        await self.broadcast_state(f"{player['name']} reloaded Rs. {START_CHIPS}!")
        await self.evaluate_game_start()

    # ---- moving the hand along
    async def advance_turn(self, sound_event: Optional[str] = None):
        alive = self.in_hand()
        if len(alive) <= 1:
            await self.finish_hand(alive[0] if alive else None, "All opponents folded")
            return
        if self.betting_round_done():
            await self.next_stage()
            return
        self.active_turn_seat = self.next_needing_action(self.active_turn_seat)
        if self.active_turn_seat is None:
            await self.next_stage()
            return
        await self.broadcast_state(sound_event=sound_event)
        self.start_turn_timer()

    async def next_stage(self):
        while True:
            self.acted_players.clear()
            for p in self.players.values():
                p["current_bet"] = 0
            self.current_bet = 0
            self.last_raise = self.big_blind
            self.active_turn_seat = None
            self.cancel_turn_timer()
            self.turn_seq += 1

            if self.stage == "RIVER":
                await self.evaluate_showdown()
                return
            if self.stage == "PREFLOP":
                self.stage = "FLOP"
                cards = [str(c) for c in self.deck.draw(3)]
                self.community_cards.extend(cards)
                self.add_log(f"Flop: [{' '.join(cards)}]", "DEALER")
            elif self.stage == "FLOP":
                self.stage = "TURN"
                card = str(self.deck.draw(1)[0])
                self.community_cards.append(card)
                self.add_log(f"Turn: [{card}]", "DEALER")
            elif self.stage == "TURN":
                self.stage = "RIVER"
                card = str(self.deck.draw(1)[0])
                self.community_cards.append(card)
                self.add_log(f"River: [{card}]", "DEALER")

            if len(self.can_act()) >= 2:
                self.active_turn_seat = self.next_needing_action(self.dealer_seat)
                await self.broadcast_state(sound_event="deal")
                self.start_turn_timer()
                return
            # everyone (or all but one) is all-in: run the board out
            await self.broadcast_state(sound_event="deal")
            await asyncio.sleep(1)

    async def evaluate_showdown(self):
        alive = self.in_hand()
        for p in alive:
            score = hand_score(p["cards"] + self.community_cards)
            p["hand_score"] = score
            p["hand_desc"] = HAND_NAMES[score[0]] if score[0] >= 0 else "Showdown"
            self.add_log(f"{p['name']} shows {' '.join(p['cards'])} ({p['hand_desc']})", "SHOWDOWN")

        pots = calculate_side_pots(list(self.players.values()))
        if not pots:
            pots = [{"amount": self.pot, "eligible": [p["id"] for p in alive]}]
        seat_order = self.seats_after(self.dealer_seat, [p["seat"] for p in alive])

        paid = 0
        summary = []
        winner_ids = set()
        best = max(alive, key=lambda p: p["hand_score"]) if alive else None
        for idx, sub in enumerate(pots):
            cands = [p for p in alive if p["id"] in sub["eligible"]]
            if not cands:
                continue
            top = max(p["hand_score"] for p in cands)
            winners = sorted([p for p in cands if p["hand_score"] == top], key=lambda p: seat_order.index(p["seat"]))
            share, odd = divmod(sub["amount"], len(winners))
            for i, w in enumerate(winners):
                won = share + (odd if i == 0 else 0)
                w["chips"] += won
                paid += won
                winner_ids.add(w["id"])
                update_user_chips(w["id"], w["chips"])
                summary.append(f"{w['name']} won Rs. {won} (Pot {idx + 1}: {w['hand_desc']})")
        leftover = self.pot - paid
        if leftover > 0 and best:
            best["chips"] += leftover
            update_user_chips(best["id"], best["chips"])

        reason = " | ".join(summary) if summary else "Showdown"
        await self.finish_hand(best, reason, already_paid=True, winner_ids=winner_ids, showdown=True)

    async def finish_hand(self, winner: Optional[dict], reason: str, already_paid: bool = False,
                          winner_ids: Optional[set] = None, showdown: bool = False):
        self.cancel_turn_timer()
        self.turn_seq += 1
        self.stage = "SHOWDOWN"
        self.active_turn_seat = None
        pot = self.pot

        if winner:
            if not already_paid:
                winner["chips"] += pot
                update_user_chips(winner["id"], winner["chips"])
            winner["hands_won"] = winner.get("hands_won", 0) + 1
            self.winner_banner = f"🏆 {winner['name']} wins Rs. {pot}! [{reason}]"
            self.add_log(f"🏆 {winner['name']} awarded Rs. {pot} ({reason})", "POT")
            dealt = [p["id"] for p in self.players.values() if p["cards"]]
            hand_name = winner.get("hand_desc") or reason
            record_hand_completion(self.table_id, winner["id"], winner["name"], pot, hand_name,
                                   self.community_cards, dealt)
        else:
            self.winner_banner = "Hand ended."

        if self.reset_task and not self.reset_task.done() and self.reset_task is not asyncio.current_task():
            self.reset_task.cancel()
        try:
            self.review = self.build_review(set(winner_ids or ([winner["id"]] if winner else [])), showdown)
        except Exception as e:                  # the review must never stop the next hand
            print(f"[{self.table_id}] review error: {e!r}")
            self.review = {}
        self.reset_task = asyncio.create_task(self.auto_reset_countdown(REVIEW_SECONDS))

    def build_review(self, winner_ids: set, showdown: bool) -> dict:
        """Every dealt player's cards and what they would have made on the full board, with a
        plain verdict, so each player can see where their decision was right or wrong.
        If the hand stopped early, the rest of the board is the real next cards of the deck
        (shown only for study; nothing is paid on them)."""
        dealt = [p for p in self.players.values() if p["cards"]]
        played = list(self.community_cards)
        missing = max(0, 5 - len(played))
        extra = [str(c) for c in self.deck.cards[:missing]] if (self.deck and missing) else []
        board = played + extra
        scores = {p["id"]: hand_score(p["cards"] + board) for p in dealt}
        best = max(scores.values()) if scores else None
        best_players = [p for p in dealt if scores[p["id"]] == best]
        best_names = " & ".join(p["name"] for p in best_players)
        best_hand = HAND_NAMES[best[0]] if best and best[0] >= 0 else ""

        winners = [p for p in dealt if p["id"] in winner_ids]
        win_names = " & ".join(p["name"] for p in winners)
        win_hand = ""
        if winners:
            ws = max(scores[p["id"]] for p in winners)
            win_hand = HAND_NAMES[ws[0]] if ws[0] >= 0 else ""

        rows = []
        for p in sorted(dealt, key=lambda x: x["seat"]):
            sc = scores[p["id"]]
            hand = HAND_NAMES[sc[0]] if sc[0] >= 0 else ""
            would_win = sc == best
            if p["id"] in winner_ids:
                result = "won"
                if showdown:
                    verdict, right = f"Won with {hand}.", True
                    if not would_win:
                        verdict += f" ({best_names} folded a better hand: {best_hand}.)"
                elif would_win:
                    verdict, right = f"Won - everyone folded. {hand} was the best hand anyway.", True
                else:
                    verdict, right = (f"Won - everyone folded. Bluff worked: {best_names} would have won "
                                      f"with {best_hand}."), True
            elif p["folded"]:
                result = "folded"
                if would_win:
                    verdict, right = f"Fold was a mistake: your {hand} would have won.", False
                else:
                    verdict, right = f"Fold was right: {best_names} would have won with {best_hand}.", True
            else:
                result = "lost"
                verdict, right = f"Lost: your {hand} lost to {win_names}'s {win_hand}.", False
            rows.append({"id": p["id"], "seat": p["seat"], "name": p["name"], "cards": p["cards"], "hand": hand,
                         "result": result, "verdict": verdict, "right": right})
        return {"hand_no": self.hand_no, "board": board, "played": len(played), "players": rows}

    async def auto_reset_countdown(self, seconds: int):
        try:
            for remaining in range(seconds, 0, -1):
                self.review_left = remaining
                await self.broadcast_state(f"{self.winner_banner} — Next in {remaining}s...",
                                           sound_event="win" if remaining == seconds else None)
                await asyncio.sleep(1)
            async with self.lock:
                if self.stage != "SHOWDOWN":
                    return
                await self.start_hand()          # with fewer than 2 ready it tidies seats and waits
                if not self.game_started:
                    await self.evaluate_game_start()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[{self.table_id}] reset error: {e!r}")


TABLES: Dict[str, PokerTable] = {
    "table_1": PokerTable("table_1", "Table 1 - Rookie Ring", small_blind=10, big_blind=20),
    "table_2": PokerTable("table_2", "Table 2 - Standard Felt", small_blind=10, big_blind=20),
    "table_3": PokerTable("table_3", "Table 3 - Mid Stakes", small_blind=25, big_blind=50),
    "table_4": PokerTable("table_4", "Table 4 - High Rollers", small_blind=50, big_blind=100),
    "table_5": PokerTable("table_5", "Table 5 - VIP Suite", small_blind=100, big_blind=200),
}

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------- HTTP

@app.get("/")
async def get_index():
    return HTMLResponse(content=(STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/api/health")
async def health():
    return {"ok": True, "tables": len(TABLES)}


def bad(msg: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


async def read_json(request: Request) -> Optional[dict]:
    try:
        data = await request.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def signed_in(player_id: str, token: str, recovery_key: Optional[str] = None) -> dict:
    profile = profile_with_daily_bonus(player_id)
    profile["token"] = token
    if recovery_key:
        profile["recovery_key"] = recovery_key      # shown once; only its hash is kept
    return profile


@app.post("/api/register")
async def register(request: Request):
    """Sign up (name + email + phone) or sign back in (same email + phone), as before.
    The PIN is optional: if one is given it is saved (or replaced) for quick sign-in later."""
    data = await read_json(request)
    if data is None:
        return bad("Bad request.")
    name = clean_name(data.get("name"))
    email = str(data.get("email") or "").strip().lower()[:120]
    phone = clean_phone(data.get("phone"))
    pin = str(data.get("pin") or "").strip()
    if len(name) < 2:
        return bad("Enter a name (2 to 15 letters).")
    if not EMAIL_RE.match(email):
        return bad("Enter a valid email id.")
    if not phone:
        return bad("Enter a valid phone number (8 to 15 digits).")
    if pin and not PIN_RE.match(pin):
        return bad("The PIN must be 4 to 6 digits (or leave it empty).")

    conn = db()
    try:
        known = conn.execute("SELECT phone FROM users WHERE email = ?", (email,)).fetchone()
    finally:
        conn.close()
    if known and not same_phone(known[0], phone):
        return bad("This email id is already registered with a different phone number.", 409)
    step = await otp_step(email, data.get("otp"))
    if step is not None:
        return step

    recovery = new_recovery_key() if pin else None
    conn = db()
    try:
        row = conn.execute("SELECT player_id, phone, token FROM users WHERE email = ?", (email,)).fetchone()
        if row:
            if not same_phone(row[1], phone):
                return bad("This email id is already registered with a different phone number.", 409)
            player_id, token = row[0], row[2] or secrets.token_urlsafe(24)
            conn.execute("UPDATE users SET player_name = ?, token = ? WHERE player_id = ?", (name, token, player_id))
        else:
            player_id, token = "u_" + secrets.token_hex(6), secrets.token_urlsafe(24)
            conn.execute("""INSERT INTO users (player_id, player_name, chips, hands_played, hands_won, total_winnings,
                                               email, phone, token, created_at, streak)
                            VALUES (?, ?, ?, 0, 0, 0, ?, ?, ?, ?, 0)""",
                         (player_id, name, START_CHIPS, email, phone, token, int(time.time())))
        if pin:
            conn.execute("""UPDATE users SET pin_hash = ?, recovery_hash = ?, pin_fails = 0, pin_locked_until = NULL
                            WHERE player_id = ?""",
                         (hash_secret(pin), hash_secret(clean_recovery_key(recovery)), player_id))
        conn.commit()
    finally:
        conn.close()
    return signed_in(player_id, token, recovery)


GUEST_PER_HOUR = 300           # new nickname accounts per hour (all players together)
_guest_created: List[float] = []


@app.post("/api/guest")
async def guest(request: Request):
    """Stratos version: play with a nickname only (no email, no phone, no code).
    The token stays in the player's browser; sending it back keeps the same chips
    and only changes the nickname."""
    data = await read_json(request)
    if data is None:
        return bad("Bad request.")
    name = clean_name(data.get("name"))
    if len(name) < 2:
        return bad("Enter a nickname (2 to 15 letters).")
    known = user_by_token(str(data.get("token") or ""))
    conn = db()
    try:
        if known:
            player_id = known[0]
            token = str(data.get("token"))
            conn.execute("UPDATE users SET player_name = ? WHERE player_id = ?", (name, player_id))
        else:
            now = time.time()
            _guest_created[:] = [t for t in _guest_created if now - t < 3600]
            if len(_guest_created) >= GUEST_PER_HOUR:
                return bad("Too many new players right now. Please try again in a few minutes.", 429)
            _guest_created.append(now)
            player_id, token = "g_" + secrets.token_hex(6), secrets.token_urlsafe(24)
            conn.execute("""INSERT INTO users (player_id, player_name, chips, hands_played, hands_won,
                                               total_winnings, token, created_at, streak)
                            VALUES (?, ?, ?, 0, 0, 0, ?, ?, 0)""",
                         (player_id, name, START_CHIPS, token, int(now)))
        conn.commit()
    finally:
        conn.close()
    for t in TABLES.values():                  # a seated player's new nickname shows at the table
        p = t.players.get(player_id)
        if p:
            p["name"] = name
    return signed_in(player_id, token)


@app.post("/api/login")
async def login(request: Request):
    """Sign back in with email id (or phone number) + PIN."""
    data = await read_json(request)
    if data is None:
        return bad("Bad request.")
    ident = str(data.get("id") or "").strip()
    pin = str(data.get("pin") or "").strip()
    email = ident.lower()[:120] if "@" in ident else ""
    phone = "" if email else clean_phone(ident)
    if not email and not phone:
        return bad("Enter your email id or phone number.")
    if not PIN_RE.match(pin):
        return bad("Enter your 4 to 6 digit PIN.")
    conn = db()
    try:
        row, err = find_user_row(conn, email, phone)
        if not row:
            return bad(err, 404)
        if not row[3]:
            return bad("This account has no PIN. Sign in with your email id and phone number "
                       "(you can add a PIN there).", 409)
        locked = locked_message(row)
        if locked:
            return bad(locked, 429)
        if not check_secret(pin, row[3]):
            note_failure(conn, row[0], row[5] or 0)
            return bad("Wrong PIN.", 401)
        token = row[7] or secrets.token_urlsafe(24)
        conn.execute("UPDATE users SET token = ?, pin_fails = 0, pin_locked_until = NULL WHERE player_id = ?",
                     (token, row[0]))
        conn.commit()
    finally:
        conn.close()
    return signed_in(row[0], token)


@app.post("/api/reset_pin")
async def reset_pin(request: Request):
    """Forgot PIN: prove the account with whatever the player has - a code emailed to the
    email id (email + phone while email codes are off), or recovery key + email id / phone number."""
    data = await read_json(request)
    if data is None:
        return bad("Bad request.")
    email = str(data.get("email") or "").strip().lower()[:120]
    phone_raw = str(data.get("phone") or "").strip()
    phone = clean_phone(phone_raw) if phone_raw else ""
    key = clean_recovery_key(data.get("recovery_key"))
    new_pin = str(data.get("new_pin") or "").strip()
    if email and not EMAIL_RE.match(email):
        return bad("Enter a valid email id.")
    if phone_raw and not phone:
        return bad("Enter a valid phone number (8 to 15 digits).")
    if not PIN_RE.match(new_pin):
        return bad("Choose a new PIN of 4 to 6 digits.")
    otp_on = email_otp_config() is not None
    if key and (email or phone):
        pass                                   # recovery key route, checked below
    elif otp_on:
        if not email:
            return bad("Enter your email id (we email you a code), or use your recovery key.")
    elif not (email and phone):
        return bad("Fill in your email id and phone number, or your recovery key with one of them.")
    use_otp = otp_on and not key
    if use_otp:
        conn = db()
        try:
            known, err = find_user_row(conn, email, "")
        finally:
            conn.close()
        if not known:
            return bad(err, 404)
        step = await otp_step(email, data.get("otp"))
        if step is not None:
            return step
    conn = db()
    try:
        row, err = find_user_row(conn, email, phone)
        if not row:
            return bad(err, 404)
        locked = locked_message(row)
        if locked:
            return bad(locked, 429)
        proofs = []
        if use_otp:
            proofs.append(True)                # the emailed code was checked above
        elif email and phone and not otp_on:
            proofs.append(same_phone(row[2], phone))
        if key:
            proofs.append(check_secret(key, row[4]))
        if not any(proofs):
            note_failure(conn, row[0], row[5] or 0)
            return bad("Those details do not match this account.", 401)
        recovery = new_recovery_key()
        token = secrets.token_urlsafe(24)          # new token: other signed-in devices must sign in again
        conn.execute("""UPDATE users SET pin_hash = ?, recovery_hash = ?, token = ?, pin_fails = 0,
                                         pin_locked_until = NULL WHERE player_id = ?""",
                     (hash_secret(new_pin), hash_secret(clean_recovery_key(recovery)), token, row[0]))
        conn.commit()
    finally:
        conn.close()
    return signed_in(row[0], token, recovery)


@app.get("/api/auth_info")
async def auth_info():
    return {"email_otp": email_otp_config() is not None}


@app.get("/api/me")
async def me(token: str = ""):
    user = user_by_token(token)
    if not user:
        return JSONResponse({"error": "Please register again."}, status_code=401)
    return profile_with_daily_bonus(user[0])


@app.get("/api/tables")
async def get_tables_list():
    return [table.get_summary() for table in TABLES.values()]


@app.get("/api/leaderboard")
async def get_leaderboard():
    conn = db()
    try:
        rows = conn.execute("""
            SELECT player_name, chips, hands_played, hands_won, total_winnings
            FROM users ORDER BY chips DESC, hands_won DESC LIMIT 10
        """).fetchall()
    finally:
        conn.close()
    return [{
        "name": r[0], "chips": r[1], "played": r[2], "won": r[3], "winnings": r[4],
        "win_rate": f"{(r[3] / r[2] * 100):.1f}%" if r[2] > 0 else "0.0%",
    } for r in rows]


@app.get("/api/history/{table_id}")
async def get_hand_history(table_id: str):
    conn = db()
    try:
        rows = conn.execute("""
            SELECT id, hand_timestamp, winner_name, pot_won, winning_hand, board_cards
            FROM hand_history WHERE table_id = ? ORDER BY id DESC LIMIT 10
        """, (table_id,)).fetchall()
    finally:
        conn.close()
    return [{
        "id": r[0], "time": time.strftime('%H:%M:%S', time.localtime(r[1])),
        "winner": r[2], "pot": r[3], "hand": r[4], "board": r[5],
    } for r in rows]


# ---------------------------------------------------------------- WebSockets

async def table_session(websocket: WebSocket, table: Optional[PokerTable], token: str):
    await websocket.accept()
    user = user_by_token(token)
    if not user:
        await websocket.send_text(json.dumps({"type": "AUTH_FAILED", "message": "Please register again."}))
        await websocket.close(code=4001)
        return
    if table is None:
        await websocket.close(code=1008)
        return
    player_id, name = user
    async with table.lock:
        joined = await table.connect(websocket, player_id, name)
    if not joined:
        await websocket.close()
        return
    try:
        while True:
            text = await websocket.receive_text()
            try:
                data = json.loads(text)
                if not isinstance(data, dict):
                    continue
            except ValueError:
                continue
            async with table.lock:
                await table.dispatch(player_id, data)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[{table.table_id}] socket error for {player_id}: {e!r}")
    finally:
        async with table.lock:
            await table.handle_disconnect(player_id, websocket)


# Backward-compatibility route for older client tabs (Table 1)
@app.websocket("/ws/{player_id}")
async def legacy_websocket_endpoint(websocket: WebSocket, player_id: str, token: str = ""):
    await table_session(websocket, TABLES["table_1"], token)


# Standard multi-table route; the player is the one the token belongs to
@app.websocket("/ws/{table_id}/{player_id}")
async def websocket_endpoint(websocket: WebSocket, table_id: str, player_id: str, token: str = ""):
    await table_session(websocket, TABLES.get(table_id), token)
