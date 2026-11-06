Here is a comprehensive summary document of the entire session. You can save this directly as `MEMORY.md` (or `PROJECT_STATUS.md`) inside `C:\Users\saiqu\projects\stratos-poker` to pick up right where we left off.

---

# Stratos Poker Arena — Project Status & Architecture Memory

**Date:** November 7, 2026

**Status:** Multi-Table Arena Live & Operational

**Workstation Directory:** `C:\Users\saiqu\projects\stratos-poker`

---

## 1. Executive Summary & Current State

The project has evolved from a single-table prototype into a multi-table, real-time Texas Hold'em platform. The system operates locally and over Tailscale Funnel with SQLite persistence, WebSockets, HTML5 Canvas 2D rendering, Web Audio synthesis, and heuristic AI house bots.

---

## 2. Tech Stack & Infrastructure

* **Backend:** FastAPI (Python 3.12), WebSockets (`/ws/{table_id}/{player_id}`), REST endpoints (`/api/tables`).
* **Engine Core:** `engine/poker_core.py` (Standard 52-card `Deck`, 7-card hand evaluator `evaluate_7_cards` ranking Royal Flush down to High Card).
* **Database:** SQLite (`poker_arena.db`) running in WAL mode with two primary tables:
* `users`: `player_id`, `player_name`, `chips`, `hands_played`, `hands_won`, `total_winnings`.
* `hand_history`: `table_id`, `hand_timestamp`, `winner_id`, `winner_name`, `pot_won`, `winning_hand`, `board_cards`.


* **Frontend:** Vanilla HTML5 / CSS3 / JavaScript (No bulky frameworks), Canvas API for felt/seat rendering, Web Audio API for synthetic audio.
* **Network & Deployment:** Local Uvicorn server (`0.0.0.0:8000`) routed through Tailscale Funnel.

---

## 3. Core Mechanics & Implemented Features

### A. Seating & Table Parameters

* **Table Cap:** Strictly limited to **3 to 5 players per table** (short-handed format for low latency and high action frequency).
* **Quorum & Countdown:** At 3 players, a 60-second grace countdown initiates; instant hand launch occurs at 5 players.
* **Dealer Rotation & Blinds:** Dealer button (`D`) rotates clockwise every hand. Small Blind and Big Blind are automatically posted relative to the dealer position.

### B. Betting & Action Clock

* **60-Second Action Clock:** Visual circular countdown ring and numerical countdown badge (`60s` down to `1s`) rendered directly above the active seat.
* **Auto-Action on Timeout:** If a player is inactive for 60s, the engine auto-checks (if free) or auto-folds to avoid stalling.
* **Betting Actions:** Check, Call, Fold, Raise slider (Min Raise to All-In presets), and side-pot eligibility for all-in players.

### C. Automated Next-Hand Loop & Showdown

* **Showdown Engine:** Evaluates all 7 cards (2 hole + 5 community) using `evaluate_7_cards`, determines exact hand rank, splits pots evenly on ties, and logs winners to disk.
* **7-Second Showcase:** Displays celebration banner and winning cards before resetting the deck, rotating the dealer button, and dealing the next hand automatically.
* **Re-buy Protection:** When a player hits Rs. 0, a reload modal offers an instant top-up back to Rs. 1000.

### D. Multi-Table Arena (5 Isolated Rooms)

1. **Table 1:** *Rookie Ring* (Blinds: Rs. 10 / 20)
2. **Table 2:** *Standard Felt* (Blinds: Rs. 10 / 20)
3. **Table 3:** *Mid Stakes* (Blinds: Rs. 25 / 50)
4. **Table 4:** *High Rollers* (Blinds: Rs. 50 / 100)
5. **Table 5:** *VIP Suite* (Blinds: Rs. 100 / 200)

* Players can return to the lobby at any time and inspect live seat counts (`players_count / 5`).

### E. AI House Bots (`Bot_Alpha`, `Bot_Bravo`, etc.)

* Clickable **`🤖 +Bot`** and **`-Bot`** controls on the top bar.
* Bots simulate realistic reaction times (1.2s to 2.5s decision delays).
* Evaluates hole card strength pre-flop (pocket pairs, high cards) and hand tier post-flop (Two Pair+, Pair, High Card).
* Automatically steps down if a human joins a full 5/5 table.

### F. Visual Polish & Audio

* **3D Multi-Denomination Chip Stacks:** Visual chip stacks rendered in 4 denominations:
* Gold (Rs. 500)
* Red (Rs. 100)
* Blue (Rs. 50)
* Green (Rs. 10)


* **Web Audio Synthesizer:** Turn notification chimes, chip wagers, card deal pops, and victory arpeggios unlocked via user-triggered audio context.
* **Table Feed:** Integrated chronological dealer log and real-time player chat feed docked to the right of the felt.

---

## 4. How to Resume When Ready

1. Open PowerShell and navigate to the project directory:
```powershell
Set-Location "C:\Users\saiqu\projects\stratos-poker"

```


2. Start the Uvicorn server:
```powershell
python -m uvicorn server.app:app --host 0.0.0.0 --port 8000

```


3. Open `http://localhost:8000` or your Tailscale public link.

---

Everything is fully preserved and ready. Get some well-deserved rest!
