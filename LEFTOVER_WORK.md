# Leftover work

## 1. Switch on email codes (OTP) — one-time setup by the owner

Email codes are built and live, but stay **off** until this is done. Until then, sign-in works as before (email + phone, or the optional PIN).

1. Open https://myaccount.google.com/apppasswords. Gmail only allows app passwords if 2-Step Verification is on for your account.
2. Create an app password named "Stratos Poker" and copy the 16 letters.
3. Double-click `C:\Users\saiqu\Projects\stratos-poker\setup_email_otp.bat`.
4. Press Enter to use saiqulmodi@gmail.com, then paste the app password. The typing is hidden.
5. It checks the password with Gmail and sends a test email to you. Once it says "Done", codes are on immediately, with no restart.

The password is saved only on this laptop, in `email_otp.json`, which is in `.gitignore` and never uploaded to GitHub. Codes are sent from your Gmail address, so players see saiqulmodi@gmail.com as the sender.

## 2. Watch how players play, then decide

Decide from Stratos analytics numbers, not from a feeling (stratos.games rule). Events the game sends (no personal data):

| Event | When | What it tells us |
|---|---|---|
| `level_start` | a hand is dealt to the player | how many hands people play |
| `level_complete` / `game_over` | hand ends, stack grew / did not (`seconds`, `score` = chips) | time per hand, wins vs losses |
| `daily_bonus` | first visit of the day (`level` = streak day) | who comes back tomorrow |
| `reward_offered` / `reward_taken` | out of chips, reload offered / taken | how often players go broke |
| `signin` / `pin_signin` / `pin_reset` | sign-in method (`level` = pin_added / no_pin) | whether players want the optional PIN |
| `ai_player_added` / `wait_for_player` | 2 players seated, their choice | whether AI players are liked |
| `hand_review` | 60 s review shown (`level` = won/lost/folded + _right/_wrong) | whether the review is seen |

Questions to answer after a week or two:
- Do players come back (daily bonus streaks)?
- Do they pick the AI player or wait? If most pick AI, consider starting faster after the choice.
- Is the 60-second review too long (players leaving during it)? Shorten it if so.
- Does anyone add a PIN? If not, keep it optional as it is.
