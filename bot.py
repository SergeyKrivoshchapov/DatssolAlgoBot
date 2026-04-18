import argparse
import random
import time
from dataclasses import dataclass
import sys
import json
import requests

API_BASE = "https://games-test.datsteam.dev"


@dataclass(frozen=True)
class Plantation:
    id: str
    pos: tuple[int, int]
    hp: int
    is_main: bool
    isolated: bool


@dataclass(frozen=True)
class Beaver:
    id: str
    pos: tuple[int, int]
    hp: int


@dataclass(frozen=True)
class Enemy:
    id: str
    pos: tuple[int, int]
    hp: int


@dataclass(frozen=True)
class Construction:
    pos: tuple[int, int]
    progress: int


class GameClient:
    def __init__(self, token: str, timeout: float = 10.0):
        self.token = token
        self.timeout = timeout
        self.session = requests.Session()

    def _headers(self):
        return {"accept": "application/json", "X-Auth-Token": self.token}

    def get_arena(self) -> dict:
        r = self.session.get(f"{API_BASE}/api/arena", headers=self._headers(), timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise TypeError(f"arena expected dict, got {type(data).__name__}")
        return data

    def get_logs(self):
        r = self.session.get(f"{API_BASE}/api/logs", headers=self._headers(), timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list):
            return []
        return data

    def post_command(self, payload: dict) -> dict:
        r = self.session.post(f"{API_BASE}/api/command", headers=self._headers(), json=payload, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise TypeError(f"command response expected dict, got {type(data).__name__}")
        return data


class Geometry:
    @staticmethod
    def is_bonus(x: int, y: int) -> bool:
        return x % 7 == 0 and y % 7 == 0

    @staticmethod
    def chebyshev(a: tuple[int, int], b: tuple[int, int]) -> int:
        return max(abs(a[0] - b[0]), abs(a[1] - b[1]))

    @staticmethod
    def manhattan(a: tuple[int, int], b: tuple[int, int]) -> int:
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    @staticmethod
    def neighbors4(pos: tuple[int, int]) -> list[tuple[int, int]]:
        x, y = pos
        return [(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)]

    @staticmethod
    def inside(pos: tuple[int, int], w: int, h: int) -> bool:
        return 0 <= pos[0] < w and 0 <= pos[1] < h

    @staticmethod
    def is_adjacent_4(a: tuple[int, int], b: tuple[int, int]) -> bool:
        return Geometry.manhattan(a, b) == 1

    @staticmethod
    def in_range(src: tuple[int, int], dst: tuple[int, int], r: int) -> bool:
        return abs(src[0] - dst[0]) <= r and abs(src[1] - dst[1]) <= r


class GameState:
    def __init__(self, arena: dict):
        self.arena = arena
        self.w, self.h = self._parse_size(arena)
        self.turn_i = self._parse_turn(arena)
        self.ar = int(arena.get("actionRange", 2) or 2)
        sr_tier = self._upgrade_tier("signal_range")
        self.sr = 3 + (sr_tier.get("current", 0) or 0)
        self.plantations = self._parse_plantations()
        self.active_plantations = [p for p in self.plantations if not p.isolated]
        self.active_positions = {p.pos for p in self.active_plantations}
        self.main_pos = self._find_main_pos()
        self.main_plantation = self._find_main_plantation()
        self.mountains = self._parse_mountains()
        self.cells_progress = self._parse_cells_progress()
        self.constructions = self._parse_constructions()
        self.beavers = self._parse_beavers()
        self.enemies = self._parse_enemies()
        self.occupied = self._build_occupied()
        self.earthquake_imminent = self._check_earthquake()

    def _parse_size(self, arena: dict) -> tuple[int, int]:
        size = arena.get("size") or [0, 0]
        try:
            return int(size[0]), int(size[1])
        except Exception:
            return 0, 0

    def _parse_turn(self, arena: dict) -> int | None:
        try:
            return int(arena.get("turnNo"))
        except Exception:
            return None

    def _pos_to_tuple(self, pos) -> tuple[int, int] | None:
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            try:
                return int(pos[0]), int(pos[1])
            except Exception:
                return None
        return None

    def _parse_plantations(self) -> list[Plantation]:
        res = []
        for p in self.arena.get("plantations", []) or []:
            if not isinstance(p, dict):
                continue
            pos = self._pos_to_tuple(p.get("position"))
            if not pos:
                continue
            res.append(Plantation(
                id=str(p.get("id", "")),
                pos=pos,
                hp=int(p.get("hp", 0) or 0),
                is_main=bool(p.get("isMain")),
                isolated=bool(p.get("isIsolated")),
            ))
        return res

    def _find_main_pos(self) -> tuple[int, int] | None:
        for p in self.plantations:
            if p.is_main:
                return p.pos
        return self.plantations[0].pos if self.plantations else None

    def _find_main_plantation(self) -> Plantation | None:
        for p in self.plantations:
            if p.is_main:
                return p
        return self.plantations[0] if self.plantations else None

    def _parse_mountains(self) -> set[tuple[int, int]]:
        ms = set()
        for m in self.arena.get("mountains", []) or []:
            pos = self._pos_to_tuple(m)
            if pos:
                ms.add(pos)
        return ms

    def _parse_cells_progress(self) -> dict[tuple[int, int], int]:
        res = {}
        for c in self.arena.get("cells", []) or []:
            if not isinstance(c, dict):
                continue
            pos = self._pos_to_tuple(c.get("position"))
            if pos:
                try:
                    res[pos] = int(c.get("terraformationProgress", 0) or 0)
                except Exception:
                    res[pos] = 0
        return res

    def _parse_constructions(self) -> list[Construction]:
        res = []
        for c in self.arena.get("construction", []) or []:
            if not isinstance(c, dict):
                continue
            pos = self._pos_to_tuple(c.get("position"))
            if pos:
                res.append(Construction(pos, int(c.get("progress", 0) or 0)))
        return res

    def _parse_beavers(self) -> list[Beaver]:
        res = []
        for b in self.arena.get("beavers", []) or []:
            if not isinstance(b, dict):
                continue
            pos = self._pos_to_tuple(b.get("position"))
            if pos:
                res.append(Beaver(str(b.get("id", "")), pos, int(b.get("hp", 0) or 0)))
        return res

    def _parse_enemies(self) -> list[Enemy]:
        res = []
        for e in self.arena.get("enemy", []) or []:
            if not isinstance(e, dict):
                continue
            pos = self._pos_to_tuple(e.get("position"))
            if pos:
                res.append(Enemy(str(e.get("id", "")), pos, int(e.get("hp", 0) or 0)))
        return res

    def _build_occupied(self) -> set[tuple[int, int]]:
        occ = set()
        for p in self.plantations:
            occ.add(p.pos)
        for e in self.enemies:
            occ.add(e.pos)
        for c in self.constructions:
            occ.add(c.pos)
        for b in self.beavers:
            occ.add(b.pos)
        for m in self.mountains:
            occ.add(m)
        return occ

    def _check_earthquake(self) -> bool:
        meteo = self.arena.get("meteoForecasts") or []
        if isinstance(meteo, list):
            for f in meteo:
                if isinstance(f, dict) and f.get("kind") == "earthquake" and f.get("turnsUntil", 99) <= 1:
                    return True
        return False

    def _upgrade_tier(self, name: str) -> dict:
        up = self.arena.get("plantationUpgrades") or {}
        tiers = up.get("tiers") or []
        for t in tiers:
            if isinstance(t, dict) and t.get("name") == name:
                return t
        return {}

    def get_upgrade_choice(self) -> str:
        up = self.arena.get("plantationUpgrades") or {}
        points = int(up.get("points", 0) or 0)
        if points <= 0:
            return ""

        tiers = {t.get("name"): t for t in (up.get("tiers") or []) if isinstance(t, dict)}

        def cur(name):
            t = tiers.get(name) or {}
            return int(t.get("current", 0) or 0), int(t.get("max", 0) or 0)

        # Простые приоритеты
        c, m = cur("repair_power")
        if m > 0 and c < 2:
            return "repair_power"

        c, m = cur("earthquake_mitigation")
        if m > 0 and c < 1:
            return "earthquake_mitigation"

        c, m = cur("max_hp")
        if m > 0 and c < 1:
            return "max_hp"

        c, m = cur("signal_range")
        if m > 0 and c < 1:
            return "signal_range"

        c, m = cur("settlement_limit")
        if m > 0 and c < m:
            return "settlement_limit"

        for name in ["repair_power", "earthquake_mitigation", "max_hp", "signal_range", "decay_mitigation",
                     "vision_range"]:
            c, m = cur(name)
            if m > 0 and c < m:
                return name

        return ""

    def is_free_cell(self, pos: tuple[int, int]) -> bool:
        if self.w <= 0 or self.h <= 0:
            return False
        if not Geometry.inside(pos, self.w, self.h):
            return False
        if pos in self.occupied:
            return False
        if self.cells_progress.get(pos, 0) >= 100:
            return False
        return True


class CommandBuilder:
    def __init__(self, max_commands: int):
        self.cmd: list[dict] = []
        self.used_authors: set[str] = set()
        self.max_commands = max_commands

    def can_add(self) -> bool:
        return len(self.cmd) < self.max_commands

    def add_build(self, author: Plantation, exit_p: Plantation, target: tuple[int, int]) -> bool:
        if author.id in self.used_authors or not self.can_add():
            return False
        self.cmd.append({
            "path": [
                [author.pos[0], author.pos[1]],
                [exit_p.pos[0], exit_p.pos[1]],
                [target[0], target[1]]
            ]
        })
        self.used_authors.add(author.id)
        return True

    def add_repair(self, author: Plantation, exit_p: Plantation, target_p: Plantation) -> bool:
        if author.id in self.used_authors or not self.can_add():
            return False
        self.cmd.append({
            "path": [
                [author.pos[0], author.pos[1]],
                [exit_p.pos[0], exit_p.pos[1]],
                [target_p.pos[0], target_p.pos[1]]
            ]
        })
        self.used_authors.add(author.id)
        return True

    def add_attack(self, author: Plantation, exit_p: Plantation, target_pos: tuple[int, int]) -> bool:
        if author.id in self.used_authors or not self.can_add():
            return False
        self.cmd.append({
            "path": [
                [author.pos[0], author.pos[1]],
                [exit_p.pos[0], exit_p.pos[1]],
                [target_pos[0], target_pos[1]]
            ]
        })
        self.used_authors.add(author.id)
        return True


class Strategy:
    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)
        self.max_commands_per_turn = 15
        self.last_relocate_turn: int | None = None
        self.last_known_main_pos: tuple[int, int] | None = None
        self.focus_target: tuple[int, int] | None = None
        self.focus_streak: int = 0  # Сколько ходов строим одну цель

    def _get_frontier(self, state: GameState) -> list[tuple[int, int]]:
        """Все свободные клетки, соседние с нашей сетью"""
        front = set()
        for pos in state.active_positions:
            for nb in Geometry.neighbors4(pos):
                if state.is_free_cell(nb):
                    front.add(nb)
        return list(front)

    def make_payload(self, arena: dict) -> dict:
        state = GameState(arena)
        builder = CommandBuilder(self.max_commands_per_turn)

        # === СБРОС ПРИ РЕСПАВНЕ ===
        if self.last_known_main_pos is not None and state.main_pos != self.last_known_main_pos:
            if self.last_relocate_turn != state.turn_i:
                self.last_relocate_turn = None
                self.focus_target = None
                self.focus_streak = 0
        self.last_known_main_pos = state.main_pos

        # === 1. ПРОДОЛЖИТЬ НЕЗАВЕРШЁННЫЕ СТРОЙКИ (ВЫСШИЙ ПРИОРИТЕТ) ===
        # Это предотвращает деградацию
        if state.constructions:
            for con in sorted(state.constructions, key=lambda c: -c.progress):
                if not builder.can_add():
                    break
                if con.progress < 50:
                    # Назначаем ВСЕХ, кто может достать
                    for p in state.active_plantations:
                        if not builder.can_add():
                            break
                        if p.id in builder.used_authors:
                            continue
                        if Geometry.in_range(p.pos, con.pos, state.ar):
                            builder.add_build(p, p, con.pos)

            # Если есть стройки — ВСЁ, больше ничего не делаем в этом ходу (кроме ремонта ЦУ)
            # Это гарантирует, что стройки не деградируют

        # === 2. РЕМОНТ ЦУ (КРИТИЧНО) ===
        if state.main_plantation and state.main_plantation.hp < 40:
            for p in state.active_plantations:
                if not builder.can_add():
                    break
                if p.id == state.main_plantation.id:
                    continue
                if p.id in builder.used_authors:
                    continue
                if Geometry.in_range(p.pos, state.main_plantation.pos, state.ar):
                    builder.add_repair(p, p, state.main_plantation)

        # === 3. ЕСЛИ СТРОЕК НЕТ — ВЫБИРАЕМ НОВУЮ ЦЕЛЬ ===
        if not state.constructions and builder.can_add():
            # Если есть фокус и он всё ещё свободен — продолжаем его
            if self.focus_target and state.is_free_cell(self.focus_target):
                self.focus_streak += 1
            else:
                # Выбираем новую цель
                frontier = self._get_frontier(state)
                if frontier:
                    # Сортируем: сначала соседи ЦУ (чтобы построить Кольцо), потом бонусные
                    if state.main_pos:
                        frontier.sort(key=lambda pos: (
                            -Geometry.is_bonus(pos[0], pos[1]),
                            Geometry.chebyshev(pos, state.main_pos)
                        ))
                    self.focus_target = frontier[0]
                    self.focus_streak = 0

            # Строим выбранную цель ВСЕМИ силами
            if self.focus_target and state.is_free_cell(self.focus_target):
                for p in state.active_plantations:
                    if not builder.can_add():
                        break
                    if p.id in builder.used_authors:
                        continue
                    if Geometry.in_range(p.pos, self.focus_target, state.ar):
                        builder.add_build(p, p, self.focus_target)

        # === 4. РЕМОНТ ОСТАЛЬНЫХ (только если остались команды) ===
        low_hp = sorted([p for p in state.plantations if p.hp < 30 and not p.is_main], key=lambda x: x.hp)
        for target in low_hp:
            if not builder.can_add():
                break
            for p in state.active_plantations:
                if p.id == target.id:
                    continue
                if p.id in builder.used_authors:
                    continue
                if Geometry.in_range(p.pos, target.pos, state.ar):
                    if builder.add_repair(p, p, target):
                        break

        # === 5. АТАКА БОБРОВ (если есть свободные плантации) ===
        for b in state.beavers:
            if not builder.can_add():
                break
            if b.hp < 50 and Geometry.chebyshev(b.pos, state.main_pos or (0, 0)) <= 3:
                for p in state.active_plantations:
                    if not builder.can_add():
                        break
                    if p.id in builder.used_authors:
                        continue
                    if Geometry.in_range(p.pos, b.pos, state.ar):
                        builder.add_attack(p, p, b.pos)

        # === 6. ПЕРЕНОС ЦУ (простой) ===
        relocate_main = []
        if state.main_pos and state.turn_i:
            main_prog = state.cells_progress.get(state.main_pos, 0)
            if main_prog >= 50:
                for nb in Geometry.neighbors4(state.main_pos):
                    cand = None
                    for p in state.active_plantations:
                        if p.pos == nb:
                            cand = p
                            break
                    if cand:
                        relocate_main = [
                            [state.main_pos[0], state.main_pos[1]],
                            [cand.pos[0], cand.pos[1]]
                        ]
                        self.last_relocate_turn = state.turn_i
                        self.focus_target = None
                        break

        upgrade = state.get_upgrade_choice()
        if not builder.cmd and not relocate_main and upgrade:
            return {"command": [], "plantationUpgrade": upgrade, "relocateMain": []}

        return {
            "command": builder.cmd,
            "plantationUpgrade": upgrade,
            "relocateMain": relocate_main
        }

def _extract_err_code(resp_text: str) -> int | None:
    try:
        data = json.loads(resp_text)
        if isinstance(data, dict):
            c = data.get("errCode")
            return int(c) if c is not None else None
    except Exception:
        return None
    return None


def _sleep_until(deadline_mono: float, max_slice: float = 0.25):
    while True:
        now = time.monotonic()
        remain = deadline_mono - now
        if remain <= 0:
            return
        time.sleep(min(max_slice, remain))


def run_bot(token: str, seed: int | None = None, max_turns: int | None = None, logs_every: int = 10):
    client = GameClient(token)
    strat = Strategy(seed=seed)

    last_turn = None
    sent_for_turn = None
    backoff_s = 0.0
    last_logs_turn = None
    backoff_mult = 1.0

    while True:
        if token == "-":
            line = sys.stdin.readline()
            if not line:
                return
            try:
                arena = json.loads(line)
            except Exception:
                continue
            if not isinstance(arena, dict):
                continue
            payload = strat.make_payload(arena)
            print(json.dumps(payload, ensure_ascii=False), flush=True)
            continue

        if backoff_s > 0:
            time.sleep(backoff_s)
            backoff_s = 0.0

        try:
            arena = client.get_arena()
        except requests.HTTPError as e:
            text = getattr(e, "response", type("", (), {"text": str(e)})()).text if hasattr(e, "response") else str(e)
            code = _extract_err_code(text)
            backoff_mult = min(8.0, backoff_mult * (1.5 if code == 24 else 1.2))
            backoff_s = 1.0 if code == 24 else 0.6
            continue
        except Exception:
            backoff_mult = min(8.0, backoff_mult * 1.2)
            backoff_s = 0.6
            continue

        turn_no = arena.get("turnNo")
        try:
            turn_no_i = int(turn_no)
        except Exception:
            turn_no_i = None

        next_turn_in = max(0.0, float(arena.get("nextTurnIn", 0.5)))
        backoff_mult = 1.0

        if turn_no_i is None:
            time.sleep(0.5)
            continue

        if max_turns is not None and turn_no_i >= max_turns:
            return

        if last_turn is None or turn_no_i != last_turn:
            last_turn = turn_no_i
            sent_for_turn = None
            if logs_every > 0 and (last_logs_turn is None or (turn_no_i - last_logs_turn) >= logs_every):
                last_logs_turn = turn_no_i
                try:
                    logs = client.get_logs()
                    if logs:
                        last_msg = logs[-1]
                        if isinstance(last_msg, dict) and last_msg.get("message"):
                            print(last_msg["message"])
                except Exception:
                    pass

        if sent_for_turn == last_turn:
            _sleep_until(time.monotonic() + next_turn_in + 0.02)
            continue

        payload = strat.make_payload(arena)
        try:
            resp = client.post_command(payload)
        except requests.HTTPError as e:
            text = getattr(e, "response", type("", (), {"text": str(e)})()).text if hasattr(e, "response") else str(e)
            code = _extract_err_code(text)
            backoff_mult = min(8.0, backoff_mult * (1.8 if code == 24 else 1.3))
            backoff_s = 1.0 if code == 24 else 0.6
            sent_for_turn = last_turn
            continue
        except Exception:
            backoff_mult = min(8.0, backoff_mult * 1.3)
            backoff_s = 0.6
            sent_for_turn = last_turn
            continue

        errors = resp.get("errors") if isinstance(resp, dict) else None
        if errors:
            if any("command already submitted" in str(x) for x in errors):
                sent_for_turn = last_turn
            elif any("empty command" in str(x) for x in errors):
                try:
                    client.post_command({"plantationUpgrade": "repair_power"})
                except Exception:
                    pass
                sent_for_turn = last_turn
            else:
                sent_for_turn = last_turn
        else:
            sent_for_turn = last_turn

        _sleep_until(time.monotonic() + next_turn_in + 0.02)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("token")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--max-turn", type=int, default=None)
    parser.add_argument("--logs-every", type=int, default=10)
    args = parser.parse_args()
    run_bot(args.token, seed=args.seed, max_turns=args.max_turn, logs_every=args.logs_every)


if __name__ == "__main__":
    main()