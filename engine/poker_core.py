# --- engine/poker_core.py ---
import random
from itertools import combinations
from typing import List, Tuple

SUITS = ['c', 'd', 'h', 's']
RANKS = {r: i for i, r in enumerate(['2', '3', '4', '5', '6', '7', '8', '9', 'T', 'J', 'Q', 'K', 'A'], start=2)}

class Card:
    def __init__(self, rank: str, suit: str):
        self.rank = rank
        self.suit = suit
        self.val = RANKS[rank]

    def __repr__(self):
        return f"{self.rank}{self.suit}"

class Deck:
    def __init__(self):
        self.cards = [Card(r, s) for r in RANKS for s in SUITS]
        random.shuffle(self.cards)

    def draw(self, count: int = 1) -> List[Card]:
        drawn = self.cards[:count]
        self.cards = self.cards[count:]
        return drawn

def evaluate_5_cards(cards: List[Card]) -> Tuple[int, List[int]]:
    vals = sorted([c.val for c in cards], reverse=True)
    suits = [c.suit for c in cards]
    is_flush = len(set(suits)) == 1
    is_straight = (len(set(vals)) == 5) and (max(vals) - min(vals) == 4)
    if set(vals) == {14, 5, 4, 3, 2}:
        is_straight = True
        vals = [5, 4, 3, 2, 1]

    counts = {v: vals.count(v) for v in set(vals)}
    count_tuples = sorted(counts.items(), key=lambda x: (x[1], x[0]), reverse=True)
    c_pattern = [cnt for _, cnt in count_tuples]
    tie_breakers = [val for val, _ in count_tuples]

    if is_straight and is_flush:
        return (8, tie_breakers)
    if c_pattern == [4, 1]:
        return (7, tie_breakers)
    if c_pattern == [3, 2]:
        return (6, tie_breakers)
    if is_flush:
        return (5, tie_breakers)
    if is_straight:
        return (4, tie_breakers)
    if c_pattern == [3, 1, 1]:
        return (3, tie_breakers)
    if c_pattern == [2, 2, 1]:
        return (2, tie_breakers)
    if c_pattern == [2, 1, 1, 1]:
        return (1, tie_breakers)
    return (0, tie_breakers)

def evaluate_7_cards(cards: List[Card]) -> Tuple[int, List[int]]:
    best = (-1, [])
    for combo in combinations(cards, 5):
        score = evaluate_5_cards(list(combo))
        if score > best:
            best = score
    return best
