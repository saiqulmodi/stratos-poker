"""One-time setup for email codes (OTP) in Stratos Poker.

Asks for the Gmail address that sends the codes and its 16-letter Gmail *app password*
(Google Account -> Security -> 2-Step Verification -> App passwords), checks that Gmail
accepts it, sends a test code to that same address, and saves email_otp.json next to the
server. That file is in .gitignore and never leaves this laptop. The running server picks
it up at once; no restart is needed.
"""
import getpass
import json
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = ROOT / "email_otp.json"
DEFAULT_SENDER = "saiqulmodi@gmail.com"


def main() -> int:
    print("Stratos Poker - email code setup\n")
    sender = input(f"Gmail address that sends the codes [{DEFAULT_SENDER}]: ").strip() or DEFAULT_SENDER
    password = getpass.getpass("Gmail app password (16 letters, typing is hidden): ").replace(" ", "").strip()
    if len(password) != 16:
        print("\nAn app password has 16 letters. Create one at https://myaccount.google.com/apppasswords")
        return 1

    print("\nChecking with Gmail...")
    try:
        msg = EmailMessage()
        msg["Subject"] = "Stratos Poker code: 123456 (test)"
        msg["From"] = f"Stratos Poker <{sender}>"
        msg["To"] = sender
        msg.set_content("Test email: Stratos Poker email codes are set up.")
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as s:
            s.starttls()
            s.login(sender, password)
            s.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        print("Gmail did not accept that address + app password. Nothing was saved.")
        return 1
    except Exception as e:
        print(f"Could not reach Gmail ({e}). Nothing was saved.")
        return 1

    CFG.write_text(json.dumps({"sender": sender, "app_password": password}), encoding="utf-8")
    print(f"\nDone. A test email was sent to {sender}.")
    print("Players now get a 6-digit code by email when they sign up, sign in with")
    print("email + phone, or reset a forgotten PIN.")
    return 0


if __name__ == "__main__":
    code = main()
    input("\nPress Enter to close.")
    sys.exit(code)
