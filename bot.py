import argparse
import random
import time
from dataclasses import dataclass
import sys
import json

import requests


API_BASE = "https://games-test.datsteam.dev"


def _pos_to_tuple(pos):
	if isinstance(pos, (list, tuple)) and len(pos) == 2:
		try:
			return int(pos[0]), int(pos[1])
		except Exception:
			return None
	return None


def _is_bonus(x: int, y: int) -> bool:
	return x % 7 == 0 and y % 7 == 0


def _chebyshev(a, b) -> int:
	return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def _manhattan(a, b) -> int:
	return abs(a[0] - b[0]) + abs(a[1] - b[1])


@dataclass(frozen=True)
class Plantation:
	id: str
	pos: tuple[int, int]
	hp: int
	is_main: bool
	isolated: bool


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


class Strategy:
	def __init__(self, seed: int | None = None):
		self.rng = random.Random(seed)
		self._last_upgrade_turn = None

	def _parse_plantations(self, arena: dict) -> list[Plantation]:
		res = []
		for p in arena.get("plantations", []) or []:
			if not isinstance(p, dict):
				continue
			pos = _pos_to_tuple(p.get("position"))
			if not pos:
				continue
			pid = str(p.get("id", ""))
			hp = int(p.get("hp", 0) or 0)
			res.append(
				Plantation(
					id=pid,
					pos=pos,
					hp=hp,
					is_main=bool(p.get("isMain")),
					isolated=bool(p.get("isIsolated")),
				)
			)
		return res

	def _main_pos(self, pls: list[Plantation]) -> tuple[int, int] | None:
		for p in pls:
			if p.is_main:
				return p.pos
		return pls[0].pos if pls else None

	def _occupied_positions(self, arena: dict) -> set[tuple[int, int]]:
		occ = set()
		for p in arena.get("plantations", []) or []:
			if isinstance(p, dict):
				pos = _pos_to_tuple(p.get("position"))
				if pos:
					occ.add(pos)
		for e in arena.get("enemy", []) or []:
			if isinstance(e, dict):
				pos = _pos_to_tuple(e.get("position"))
				if pos:
					occ.add(pos)
		for c in arena.get("construction", []) or []:
			if isinstance(c, dict):
				pos = _pos_to_tuple(c.get("position"))
				if pos:
					occ.add(pos)
		for b in arena.get("beavers", []) or []:
			if isinstance(b, dict):
				pos = _pos_to_tuple(b.get("position"))
				if pos:
					occ.add(pos)
		for m in arena.get("mountains", []) or []:
			pos = _pos_to_tuple(m)
			if pos:
				occ.add(pos)
		return occ

	def _mountains(self, arena: dict) -> set[tuple[int, int]]:
		ms = set()
		for m in arena.get("mountains", []) or []:
			pos = _pos_to_tuple(m)
			if pos:
				ms.add(pos)
		return ms

	def _in_range(self, src: tuple[int, int], dst: tuple[int, int], r: int) -> bool:
		return abs(src[0] - dst[0]) <= r and abs(src[1] - dst[1]) <= r

	def _upgrade_choice(self, arena: dict) -> str:
		up = arena.get("plantationUpgrades") or {}
		points = int(up.get("points", 0) or 0)
		if points <= 0:
			return ""

		tiers = {t.get("name"): t for t in (up.get("tiers") or []) if isinstance(t, dict)}

		def cur(name):
			t = tiers.get(name)
			if not t:
				return 0, 0
			return int(t.get("current", 0) or 0), int(t.get("max", 0) or 0)

		pref = [
			"repair_power",
			"signal_range",
			"vision_range",
			"max_hp",
			"settlement_limit",
			"decay_mitigation",
			"earthquake_mitigation",
			"beaver_damage_mitigation",
		]

		for name in pref:
			c, m = cur(name)
			if m > 0 and c < m:
				return name
		return ""

	def _best_build_target(self, arena: dict, exit_pos: tuple[int, int], ar: int) -> tuple[int, int] | None:
		size = arena.get("size") or [0, 0]
		try:
			w, h = int(size[0]), int(size[1])
		except Exception:
			w, h = 0, 0

		if w <= 0 or h <= 0:
			return None

		occ = self._occupied_positions(arena)
		mountains = self._mountains(arena)
		center = (w // 2, h // 2)

		candidates = []
		for dx in range(-ar, ar + 1):
			for dy in range(-ar, ar + 1):
				x = exit_pos[0] + dx
				y = exit_pos[1] + dy
				if x < 0 or y < 0 or x >= w or y >= h:
					continue
				pos = (x, y)
				if pos in occ or pos in mountains:
					continue
				bonus = 1 if _is_bonus(x, y) else 0
				dcenter = _chebyshev(pos, center)
				border = min(x, y, w - 1 - x, h - 1 - y)
				step_out = _manhattan(pos, exit_pos)
				score = 10_000 * bonus - 6 * dcenter + 2 * border - 50 * step_out + self.rng.random()
				candidates.append((score, pos))

		if not candidates:
			return None
		candidates.sort(reverse=True, key=lambda t: t[0])
		return candidates[0][1]

	def _pick_exit_plantation(self, pls: list[Plantation], target: tuple[int, int], sr: int) -> Plantation | None:
		best = None
		best_d = None
		for p in pls:
			if p.isolated:
				continue
			if not self._in_range(p.pos, target, sr):
				continue
			d = _chebyshev(p.pos, target)
			if best is None or d < best_d:
				best = p
				best_d = d
		return best

	def _pick_build_exit(self, pls: list[Plantation], main_pos: tuple[int, int] | None) -> Plantation | None:
		active = [p for p in pls if not p.isolated]
		if not active:
			return None
		if main_pos is None:
			return max(active, key=lambda p: (p.hp, -self.rng.random()))
		return max(active, key=lambda p: (_manhattan(p.pos, main_pos), p.hp, self.rng.random()))

	def make_payload(self, arena: dict) -> dict:
		pls = self._parse_plantations(arena)
		ar = int(arena.get("actionRange", 2) or 2)
		up = arena.get("plantationUpgrades") or {}
		tiers = {t.get("name"): t for t in (up.get("tiers") or []) if isinstance(t, dict)}
		sr = 3 + int((tiers.get("signal_range") or {}).get("current", 0) or 0)

		cmd = []
		upgrade = self._upgrade_choice(arena)

		active_pls = [p for p in pls if not p.isolated]
		main_pos = self._main_pos(pls)

		constructions = []
		for c in arena.get("construction", []) or []:
			if not isinstance(c, dict):
				continue
			pos = _pos_to_tuple(c.get("position"))
			if not pos:
				continue
			prog = c.get("progress", 0)
			try:
				prog = int(prog)
			except Exception:
				prog = 0
			constructions.append((prog, pos))
		constructions.sort(reverse=True, key=lambda t: t[0])

		if active_pls and constructions:
			prog, cpos = constructions[0]
			exit_p = self._pick_exit_plantation(active_pls, cpos, sr)
			if exit_p is not None and self._in_range(exit_p.pos, cpos, ar):
				author = exit_p
				cmd.append({"path": [[author.pos[0], author.pos[1]], [exit_p.pos[0], exit_p.pos[1]], [cpos[0], cpos[1]]]})
				payload = {"command": cmd, "plantationUpgrade": upgrade, "relocateMain": []}
				if not payload["command"] and not payload["plantationUpgrade"]:
					payload["plantationUpgrade"] = "repair_power" if upgrade == "" else upgrade
				return payload

		if pls:
			low = [p for p in pls if p.hp <= 18 and not p.isolated]
			if len(pls) >= 2 and low:
				target = min(low, key=lambda p: p.hp)
				helpers = [p for p in pls if p.id != target.id and not p.isolated]
				if helpers:
					author = helpers[0]
					exit_p = self._pick_exit_plantation(pls, target.pos, sr) or author
					cmd.append({"path": [[author.pos[0], author.pos[1]], [exit_p.pos[0], exit_p.pos[1]], [target.pos[0], target.pos[1]]]})

		beavers = []
		for b in arena.get("beavers", []) or []:
			if not isinstance(b, dict):
				continue
			pos = _pos_to_tuple(b.get("position"))
			if not pos:
				continue
			hp = int(b.get("hp", 0) or 0)
			beavers.append((hp, pos))
		beavers.sort(key=lambda t: t[0])

		if pls and beavers:
			for hp, bpos in beavers[:2]:
				exit_p = self._pick_exit_plantation(pls, bpos, sr)
				if exit_p and self._in_range(exit_p.pos, bpos, ar):
					author = exit_p
					cmd.append({"path": [[author.pos[0], author.pos[1]], [exit_p.pos[0], exit_p.pos[1]], [bpos[0], bpos[1]]]})

		enemies = []
		for e in arena.get("enemy", []) or []:
			if not isinstance(e, dict):
				continue
			pos = _pos_to_tuple(e.get("position"))
			if not pos:
				continue
			hp = int(e.get("hp", 0) or 0)
			enemies.append((hp, pos))
		enemies.sort(key=lambda t: t[0])

		if pls and enemies and len(cmd) < 3:
			for hp, epos in enemies[:2]:
				exit_p = self._pick_exit_plantation(pls, epos, sr)
				if exit_p and self._in_range(exit_p.pos, epos, ar):
					author = exit_p
					cmd.append({"path": [[author.pos[0], author.pos[1]], [exit_p.pos[0], exit_p.pos[1]], [epos[0], epos[1]]]})

		if pls and len(cmd) < 4:
			exit_p = self._pick_build_exit(pls, main_pos)
			if exit_p is not None:
				target = self._best_build_target(arena, exit_p.pos, ar)
				if target is not None:
					author = exit_p
					cmd.append({"path": [[author.pos[0], author.pos[1]], [exit_p.pos[0], exit_p.pos[1]], [target[0], target[1]]]})

		payload = {"command": cmd, "plantationUpgrade": upgrade, "relocateMain": []}
		if not payload["command"] and not payload["plantationUpgrade"]:
			payload["plantationUpgrade"] = "repair_power" if upgrade == "" else upgrade
		return payload


def _extract_err_code(resp_text: str) -> int | None:
	try:
		data = requests.models.complexjson.loads(resp_text)
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
			print(f"HTTP error on arena: {text[:300]}")
			continue
		except Exception as e:
			backoff_mult = min(8.0, backoff_mult * 1.2)
			backoff_s = 0.6 * backoff_mult
			print(f"Error on arena: {e}")
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
			deadline = time.monotonic() + max(0.0, next_turn_in - 0.05)
			_sleep_until(deadline)
			continue

		payload = strat.make_payload(arena)
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
			print(f"HTTP error on command: {text[:300]}")
			sent_for_turn = last_turn
			continue
		except Exception as e:
			print(f"Error on command: {e}")
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
					resp2 = client.post_command({"plantationUpgrade": "repair_power"})
					print(resp2)
				except Exception:
					pass
				sent_for_turn = last_turn
			else:
				print(resp)
				sent_for_turn = last_turn
		else:
			sent_for_turn = last_turn

		deadline = time.monotonic() + max(0.0, next_turn_in - 0.05)
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
