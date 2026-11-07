# Contributing to Stratos Poker Arena

Thanks for helping. Bug reports, ideas and pull requests are welcome.

## Reporting a bug or idea

Open an issue using the **Bug report** or **Feature request** template. Include steps to reproduce, what you expected and what happened, plus browser/device for page problems.

Security problems: do not open a public issue; follow [SECURITY.md](SECURITY.md).

## Running locally

```bash
pip install fastapi "uvicorn[standard]"
python -m uvicorn server.app:app --host 127.0.0.1 --port 8000
```

Then open http://localhost:8000.

## Pull requests

1. Fork the repo and create a branch from `main`.
2. Keep each pull request to one change; keep existing features working.
3. Test a few full hands (with bots) before opening the pull request.
4. Never commit `poker_arena.db*` or `logs/`: the database holds players' email ids and phone numbers.
5. Fill in the pull request template.

By contributing you agree your work is released under the [MIT License](LICENSE) and that you follow the [Code of Conduct](CODE_OF_CONDUCT.md).
