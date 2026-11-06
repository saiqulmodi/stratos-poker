# --- test_setup.py ---
from engine.poker_core import Deck, evaluate_7_cards
from engine.season_manager import SeasonManager, PokerPlayer

deck = Deck()
hand = deck.draw(7)
score = evaluate_7_cards(hand)
print(f"Sample 7-Card Hand: {hand} -> Score Category: {score[0]}")

roster = [[PokerPlayer(f"p_{s}_{i}", f"P_S{s+1}_{i+1}") for i in range(5)] for s in range(4)]
sm = SeasonManager("SEASON-1", hands_per_set=5)
sm.create_season(roster)
s1 = sm.get_current_set()
for p in s1.assigned_players:
    p.is_connected = True
    p.is_ready = True

started = sm.check_start(s1)
print(f"Season 1 - Set 1 Start Status: {started}")
