# --- engine/season_manager.py ---
import time
from typing import List, Optional

class PokerPlayer:
    def __init__(self, player_id: str, name: str, chips: int = 1000):
        self.player_id = player_id
        self.name = name
        self.chips = chips
        self.is_connected = False
        self.is_ready = False

class PokerSet:
    def __init__(self, set_number: int, assigned_players: List[PokerPlayer], hands_per_set: int = 10):
        self.set_number = set_number
        self.assigned_players = assigned_players  # 3 to 5
        self.hands_per_set = hands_per_set
        self.current_hand = 0
        self.is_active = False
        self.is_completed = False
        self.alert_dispatched = False
        self.grace_period_start = None
        self.GRACE_PERIOD_SECONDS = 60

    def ready_count(self) -> int:
        return sum(1 for p in self.assigned_players if p.is_connected and p.is_ready)

class SeasonManager:
    MAX_SETS = 4
    MIN_PLAYERS = 3
    MAX_PLAYERS = 5

    def __init__(self, season_id: str, hands_per_set: int = 10):
        self.season_id = season_id
        self.hands_per_set = hands_per_set
        self.sets: List[PokerSet] = []
        self.active_set_idx = 0
        self.season_finished = False

    def create_season(self, sets_roster: List[List[PokerPlayer]]):
        self.sets = []
        for i in range(min(self.MAX_SETS, len(sets_roster))):
            players = sets_roster[i][:self.MAX_PLAYERS]
            self.sets.append(PokerSet(set_number=i + 1, assigned_players=players, hands_per_set=self.hands_per_set))

    def get_current_set(self) -> Optional[PokerSet]:
        if self.active_set_idx < len(self.sets):
            return self.sets[self.active_set_idx]
        return None

    def get_next_set(self) -> Optional[PokerSet]:
        nxt = self.active_set_idx + 1
        return self.sets[nxt] if nxt < len(self.sets) else None

    def dispatch_alert(self, s: PokerSet):
        names = [p.name for p in s.assigned_players]
        print(f"\n[BROADCAST ALERT] Set {s.set_number} ({', '.join(names)}): You can play now!")
        s.alert_dispatched = True

    def check_start(self, s: PokerSet) -> bool:
        ready = s.ready_count()
        total = len(s.assigned_players)

        # 5 players present -> Instant start
        if ready == total and ready >= self.MIN_PLAYERS:
            s.is_active = True
            s.grace_period_start = None
            print(f"[SET {s.set_number}] All {ready} seated. Starting immediately!")
            return True

        # 3 or 4 players present -> 60s countdown
        if ready >= self.MIN_PLAYERS:
            if s.grace_period_start is None:
                s.grace_period_start = time.time()
                print(f"[SET {s.set_number}] Quorum {ready}/{total}. 60s timer started...")
                return False
            if (time.time() - s.grace_period_start) >= s.GRACE_PERIOD_SECONDS:
                s.is_active = True
                print(f"[SET {s.set_number}] 60s timer elapsed. Starting with {ready} players.")
                return True
        return False
