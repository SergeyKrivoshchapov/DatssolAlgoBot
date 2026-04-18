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
    def __init__(self, token: str, timeout: float = 3.0):
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
    def neighbors8(pos: tuple[int, int]) -> list[tuple[int, int]]:
        x, y = pos
        return [(x + dx, y + dy) for dx in [-1, 0, 1] for dy in [-1, 0, 1] if not (dx == 0 and dy == 0)]

    @staticmethod
    def inside(pos: tuple[int, int], w: int, h: int) -> bool:
        return 0 <= pos[0] < w and 0 <= pos[1] < h

    @staticmethod
    def is_adjacent_4(a: tuple[int, int], b: tuple[int, int]) -> bool:
        return Geometry.manhattan(a, b) == 1

    @staticmethod
    def is_adjacent_8(a: tuple[int, int], b: tuple[int, int]) -> bool:
        return Geometry.chebyshev(a, b) == 1

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
        self.danger_zones = self._parse_danger_zones()

    def is_dangerous(self, pos: tuple[int, int]) -> bool:
        for center, radius, turns in self.danger_zones:
            if Geometry.in_range(pos, center, radius):
                return True
        return False

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

        # ===== ПРИОРИТЕТЫ =====
        # 1. СКОРОСТЬ СТРОЙКИ/РЕМОНТА (repair_power) — КРИТИЧНО
        # 2. ЗАЩИТА ОТ ЗЕМЛЕТРЯСЕНИЙ (earthquake_mitigation)
        # 3. МАКСИМАЛЬНОЕ HP (max_hp)
        # 4. Всё остальное

        # Группа 1: Скорость стройки/ремонта (максимум 3 улучшения)
        repair_c, repair_m = cur("repair_power")
        if repair_m > 0 and repair_c < repair_m and repair_c < 3:
            return "repair_power"

        # Группа 2: Защита от землетрясений (максимум 3 улучшения)
        earthquake_c, earthquake_m = cur("earthquake_mitigation")
        if earthquake_m > 0 and earthquake_c < earthquake_m and earthquake_c < 3:
            return "earthquake_mitigation"

        # Группа 3: Максимальное HP (максимум 5 улучшений)
        hp_c, hp_m = cur("max_hp")
        if hp_m > 0 and hp_c < hp_m and hp_c < 5:
            return "max_hp"

        # Группа 4: Остальные апгрейды
        remaining = [
            "signal_range",
            "settlement_limit",
            "decay_mitigation",
            "vision_range",
            "beaver_damage_mitigation"
        ]

        for name in remaining:
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

    def is_adjacent_to_network(self, target: tuple[int, int]) -> bool:
        if not self.active_positions:
            return False
        return any(Geometry.is_adjacent_4(target, ap) for ap in self.active_positions)

    def _parse_danger_zones(self):
        zones = []
        meteo = self.arena.get("meteoForecasts") or []

        for m in meteo:
            if not isinstance(m, dict):
                continue

            # берём будущую позицию
            pos = self._pos_to_tuple(m.get("nextPosition") or m.get("position"))
            if not pos:
                continue

            radius = int(m.get("radius", 0) or 0)
            turns = int(m.get("turnsUntil", 1) or 1)

            zones.append((pos, radius, turns))

        return zones

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


class CrossExpander:
    def __init__(self):
        self.cross_origin: tuple[int, int] | None = None
        self.ray_next_k: dict[str, int] = {"N": 1, "E": 1, "S": 1, "W": 1}
        self.recent_failed_targets: dict[tuple[int, int], int] = {}
        self.max_failed_target_age = 8

    def update_origin(self, main_pos: tuple[int, int] | None):
        if main_pos is not None:
            self.cross_origin = main_pos

    def cleanup_failed_targets(self, turn_i: int | None):
        if turn_i is None:
            return
        for pos, t in list(self.recent_failed_targets.items()):
            if (turn_i - t) > self.max_failed_target_age:
                del self.recent_failed_targets[pos]

    def _ring_positions(self, origin: tuple[int, int], r: int, w: int, h: int) -> list[tuple[int, int]]:
        if r <= 0:
            return []
        ox, oy = origin
        res = []
        x0, x1 = ox - r, ox + r
        y0, y1 = oy - r, oy + r

        for x in range(x0, x1 + 1):
            if Geometry.inside((x, y0), w, h):
                res.append((x, y0))
        for y in range(y0 + 1, y1 + 1):
            if Geometry.inside((x1, y), w, h):
                res.append((x1, y))
        for x in range(x1 - 1, x0 - 1, -1):
            if Geometry.inside((x, y1), w, h):
                res.append((x, y1))
        for y in range(y1 - 1, y0, -1):
            if Geometry.inside((x0, y), w, h):
                res.append((x0, y))

        seen, uniq = set(), []
        for p in res:
            if p not in seen:
                uniq.append(p)
                seen.add(p)
        return uniq

    def _ray_target(self, origin: tuple[int, int], direction: str, k: int) -> tuple[int, int] | None:
        ox, oy = origin
        if k <= 0:
            return None
        if direction == "N":
            return ox, oy - k
        if direction == "S":
            return ox, oy + k
        if direction == "E":
            return ox + k, oy
        if direction == "W":
            return ox - k, oy
        return None

    def get_candidates(self, origin: tuple[int, int], w: int, h: int) -> list[tuple[int, int]]:
        res = self._ring_positions(origin, 1, w, h)
        for d in ("N", "E", "S", "W"):
            t = self._ray_target(origin, d, self.ray_next_k.get(d, 1))
            if t is not None and Geometry.inside(t, w, h):
                res.append(t)
        return res

    def advance_ray(self, origin: tuple[int, int], target: tuple[int, int]):
        if Geometry.chebyshev(target, origin) == 1:
            return
        for d in ("N", "E", "S", "W"):
            k = self.ray_next_k.get(d, 1)
            if self._ray_target(origin, d, k) == target:
                self.ray_next_k[d] = k + 1
                break

    def mark_failed(self, target: tuple[int, int], turn_i: int | None):
        self.recent_failed_targets[target] = turn_i if turn_i is not None else 0


class Strategy:
    def __init__(self, seed: int | None = None):
        self.last_known_main_pos: tuple[int, int] | None = None
        self.rng = random.Random(seed)
        self.max_commands_per_turn = 8
        self.max_builds_per_turn = 3
        self.last_relocate_turn: int | None = None
        self.expander = CrossExpander()
        self.consecutive_empty_turns = 0

    def _find_build_pair(self, state: GameState, target: tuple[int, int], builder: CommandBuilder) -> tuple[Plantation, Plantation] | None:
        """Найти пару (author, exit) для стройки/атаки target"""
        if not state.active_plantations:
            return None

        # Ищем плантации, которые могут дотянуться до цели через AR
        valid_exits = []
        for p in state.active_plantations:
            if Geometry.in_range(p.pos, target, state.ar) and p.id not in builder.used_authors:
                valid_exits.append(p)

        if not valid_exits:
            return None

        # Сортируем: сначала ближайшие к цели, потом с большим HP
        valid_exits.sort(key=lambda p: (Geometry.chebyshev(p.pos, target), -p.hp))

        # Используем одну и ту же плантацию как author и exit
        return valid_exits[0], valid_exits[0]

    def _repair_candidates(self, state: GameState, target: Plantation, builder: CommandBuilder) -> list[tuple[Plantation, Plantation]]:
        """Найти кандидатов для ремонта target"""
        cands = []
        for p in state.active_plantations:
            if p.id == target.id:
                continue
            if p.id in builder.used_authors:
                continue
            if not Geometry.in_range(p.pos, target.pos, state.ar):
                continue
            cands.append((p, p))
        cands.sort(key=lambda x: (-x[0].hp, Geometry.chebyshev(x[0].pos, target.pos)))
        return cands

    def make_payload(self, arena: dict) -> dict:
        state = GameState(arena)
        builder = CommandBuilder(self.max_commands_per_turn)

        # ===== ОБНАРУЖЕНИЕ РЕСПАВНА =====
        if self.last_known_main_pos is not None and state.main_pos != self.last_known_main_pos:
            # Проверяем, был ли это плановый перенос
            if self.last_relocate_turn != state.turn_i:
                print(f"[Turn {state.turn_i}] RESPAWN DETECTED! Resetting state.", file=sys.stderr)
                # ПОЛНЫЙ СБРОС
                self.expander = CrossExpander()
                self.last_relocate_turn = None
                self.consecutive_empty_turns = 0
        self.last_known_main_pos = state.main_pos

        self.expander.cleanup_failed_targets(state.turn_i)
        self.expander.update_origin(state.main_pos)

        self.expander.cleanup_failed_targets(state.turn_i)
        self.expander.update_origin(state.main_pos)

        builds_done = 0

        # 1. Продолжить существующие стройки
        if state.constructions:
            for con in sorted(state.constructions, key=lambda c: -c.progress):
                if builds_done >= self.max_builds_per_turn:
                    break
                if con.progress < 50:
                    pair = self._find_build_pair(state, con.pos, builder)
                    if pair and builder.add_build(pair[0], pair[1], con.pos):
                        builds_done += 1

        # 2. Ремонт ЦУ (критический приоритет)
        if state.main_plantation and state.main_plantation.hp < 35:
            for author, exit_p in self._repair_candidates(state, state.main_plantation, builder):
                if builder.add_repair(author, exit_p, state.main_plantation):
                    break

        # 3. Ремонт остальных плантаций
        hp_threshold = 20 if state.earthquake_imminent else 40
        low_hp = sorted(
            [p for p in state.plantations if p.hp < hp_threshold and not p.is_main],
            key=lambda p: p.hp
        )
        for p in low_hp:
            if not builder.can_add():
                break
            for author, exit_p in self._repair_candidates(state, p, builder):
                if builder.add_repair(author, exit_p, p):
                    break

        # 4. Атака бобров (если они слабые или близко)
        for b in sorted(state.beavers, key=lambda x: x.hp):
            if not builder.can_add():
                break
            # Атакуем только если можем нанести значительный урон или добить
            if b.hp < 50 or Geometry.chebyshev(b.pos, state.main_pos or (0, 0)) <= 3:
                pair = self._find_build_pair(state, b.pos, builder)
                if pair:
                    builder.add_attack(pair[0], pair[1], b.pos)

        # 5. Атака врагов (только ослабленных)
        for e in sorted(state.enemies, key=lambda x: x.hp):
            if not builder.can_add():
                break
            if e.hp < 30:  # Атакуем только если враг почти мёртв
                pair = self._find_build_pair(state, e.pos, builder)
                if pair:
                    builder.add_attack(pair[0], pair[1], e.pos)

        # 6. Экспансия Креста
        build_budget = max(0, min(self.max_builds_per_turn - builds_done, self.max_commands_per_turn - len(builder.cmd)))
        if state.plantations and build_budget > 0 and self.expander.cross_origin:
            candidates = self.expander.get_candidates(self.expander.cross_origin, state.w, state.h)

            # Сортируем кандидатов: сначала бонусные клетки
            def candidate_score(pos):
                score = 0

                if Geometry.is_bonus(pos[0], pos[1]):
                    score += 1000

                # центр карты
                score -= Geometry.chebyshev(pos, (state.w // 2, state.h // 2)) * 0.3

                # избегаем опасности
                if state.is_dangerous(pos):
                    score -= 2000

                # ближе к врагам — агрессивнее
                if state.enemies:
                    d = min(Geometry.chebyshev(pos, e.pos) for e in state.enemies)
                    score -= d * 0.5

                return score

            candidates.sort(key=candidate_score, reverse=True)

            built = 0
            for target in candidates:
                if built >= build_budget:
                    break
                if target in self.expander.recent_failed_targets:
                    continue
                if not state.is_free_cell(target):
                    continue
                if not state.is_adjacent_to_network(target):
                    continue

                pair = self._find_build_pair(state, target, builder)
                if not pair:
                    self.expander.mark_failed(target, state.turn_i)
                    continue
                if state.is_dangerous(target):
                    self.expander.mark_failed(target, state.turn_i)
                    continue
                if builder.add_build(pair[0], pair[1], target):
                    built += 1
                    self.expander.advance_ray(self.expander.cross_origin, target)

        # 7. Перенос ЦУ
        relocate_main = []
        if state.main_pos and state.turn_i:
            main_prog = state.cells_progress.get(state.main_pos, 0)
            is_critical = main_prog >= 50
            is_time = main_prog >= 35 and (self.last_relocate_turn is None or (state.turn_i - self.last_relocate_turn) >= 12)

            if state.is_dangerous(state.main_pos):
                is_critical = True

            if is_critical or is_time:
                # Ищем соседние плантации для переноса
                rel_cands = [
                    p for p in state.active_plantations
                    if p.pos != state.main_pos
                    and Geometry.is_adjacent_4(p.pos, state.main_pos)
                    and state.cells_progress.get(p.pos, 0) < 50
                ]
                if rel_cands:
                    best = min(rel_cands, key=lambda p: state.cells_progress.get(p.pos, 100))
                    relocate_main = [
                        [state.main_pos[0], state.main_pos[1]],
                        [best.pos[0], best.pos[1]]
                    ]
                    self.last_relocate_turn = state.turn_i
                    print(f"[Turn {state.turn_i}] Relocating MAIN from {state.main_pos} (prog {main_prog}%) to {best.pos}", file=sys.stderr)

        # Если команд нет, но есть очки апгрейда — используем их
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
            text = ""
            try:
                text = e.response.text
            except Exception:
                text = str(e)
            code = _extract_err_code(text)
            if code == 24:
                backoff_mult = min(8.0, backoff_mult * 1.5)
                backoff_s = 1.0 * backoff_mult
            else:
                backoff_mult = min(8.0, backoff_mult * 1.2)
                backoff_s = 0.6 * backoff_mult
            print(f"HTTP error on arena: {text[:300]}", file=sys.stderr)
            continue
        except Exception as e:
            backoff_mult = min(8.0, backoff_mult * 1.2)
            backoff_s = 0.6 * backoff_mult
            print(f"Error on arena: {e}", file=sys.stderr)
            continue

        turn_no = arena.get("turnNo")
        try:
            turn_no_i = int(turn_no)
        except Exception:
            turn_no_i = None

        next_turn_in = arena.get("nextTurnIn", 0.5)
        try:
            next_turn_in = float(next_turn_in)
        except Exception:
            next_turn_in = 0.5
        next_turn_in = max(0.0, next_turn_in)
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
                    if isinstance(logs, list) and logs:
                        last_msg = logs[-1]
                        if isinstance(last_msg, dict):
                            msg = last_msg.get("message", "")
                            if msg:
                                print(msg)
                except Exception:
                    pass

        if sent_for_turn == last_turn:
            deadline = time.monotonic() + max(0.0, next_turn_in + 0.02)
            _sleep_until(deadline)
            continue

        try:
            resp = client.post_command(payload)
        except requests.HTTPError as e:
            try:
                text = e.response.text
            except Exception:
                text = str(e)
            code = _extract_err_code(text)
            if code == 24:
                backoff_mult = min(8.0, backoff_mult * 1.8)
                backoff_s = 1.0 * backoff_mult
            else:
                backoff_mult = min(8.0, backoff_mult * 1.3)
                backoff_s = 0.6 * backoff_mult
            print(f"HTTP error on command: {text[:300]}", file=sys.stderr)
            continue
        except Exception as e:
            print(f"Error on command: {e}", file=sys.stderr)
            backoff_mult = min(8.0, backoff_mult * 1.3)
            backoff_s = 0.6 * backoff_mult
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
                print(f"Errors: {errors}", file=sys.stderr)
                sent_for_turn = last_turn
        else:
            sent_for_turn = last_turn

        deadline = time.monotonic() + max(0.0, next_turn_in + 0.02)
        _sleep_until(deadline)


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
