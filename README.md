# DatsSol AlgoBot — Game AI Visualizer & Strategy Implementation

A sophisticated game bot framework for the **DatsSol** strategy game, featuring an interactive PyQt6-based visualizer and an intelligent AI decision-making engine.

## 🎮 Project Overview

This project combines two key components:

1. **AI Bot Engine** (`bot.py`) — Autonomous game strategy agent with real-time decision-making
2. **Interactive Visualizer** (`main.py`) — Real-time game state visualization with live monitoring

The bot participates in a resource management and territory control game where players build plantations, manage upgrades, and defend against enemies while navigating environmental hazards.

---

## 🏗️ Architecture

### Core Components

#### Bot Engine (`bot.py`)

**Responsibilities:**
- Parse game arena state and extract relevant game objects
- Implement strategic decision-making algorithms
- Generate command sequences for game actions
- Handle API communication and error recovery

**Key Classes:**

| Class | Purpose |
|-------|---------|
| `GameClient` | RESTful API client for game communication |
| `GameState` | Game state parser and analyzer |
| `Geometry` | Spatial utilities (distances, ranges, neighbors) |
| `Strategy` | AI decision-making and command planning |
| `CommandBuilder` | Validates and constructs command payloads |

**Strategy Priorities (turn-based execution order):**

1. **Complete ongoing constructions** (prevent degradation)
2. **Repair main control center (ЦУ)** if HP < 40
3. **Expand territory** to nearby bonus cells and frontier
4. **Repair damaged plantations** (HP < 30)
5. **Attack nearby beavers** (if low HP and close to main)
6. **Relocate main control center** (if cell progress ≥ 50%)
7. **Purchase upgrades** in priority order:
   - Repair power → Earthquake mitigation → Max HP → Signal range → Settlement limit

#### Visualizer (`main.py`)

**Responsibilities:**
- Real-time game map rendering with zoom/pan controls
- Display all game entities (plantations, enemies, beavers, constructions)
- Show game statistics and logs
- Provide interactive cell inspection

**Features:**

| Feature | Details |
|---------|---------|
| **Interactive Map** | Zoom (Ctrl+±), pan (hjkl/arrows), click cells for info |
| **Real-time Updates** | Async threading for non-blocking API calls |
| **Dark Theme** | Custom PyQt6 stylesheet for visual clarity |
| **Tabbed Interface** | Plantations, upgrades, logs, cell info |
| **Legend** | Color-coded map elements |

**Game Elements Visualization:**

```
Color        Element
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🟫 Brown     Desert (default terrain)
⬜ Gray      Mountains (impassable)
🟩 Green     Player plantations (active)
🟨 Gold      Main control center (ЦУ)
🔴 Red       Enemy plantations
🔵 Blue      Construction in progress
🟠 Orange    Beaver lairs
🟦 Cyan      Oasis (terraformation)
🟣 Purple    Bonus cells (spawn at 7,7,14,... grid)
🌪️ Tan       Sandstorm radius forecast
```

---

## 🎯 Game Mechanics

### Game Objects

**Plantations** — Player-controlled buildings
- Properties: Position, HP, main flag, isolation status
- Actions: Build, repair, attack, relocate main

**Enemies** — Opponent plantations to defend against
- Properties: Position, HP
- Status: Visible on map

**Beavers** — Environmental hazards
- Properties: Position, HP
- Behavior: Damage nearby plantations over time

**Construction** — In-progress buildings
- Properties: Position, progress (0-50)
- Degradation: Must continue each turn or loses progress

### Upgrade Tiers

The game features 8 upgradable technologies:

| Upgrade | Purpose |
|---------|---------|
| `repair_power` | Build/repair speed |
| `max_hp` | Plantation durability |
| `settlement_limit` | Maximum plantation count |
| `signal_range` | Action radius (affects all commands) |
| `vision_range` | Map visibility radius |
| `decay_mitigation` | Reduce building degradation rate |
| `earthquake_mitigation` | Reduce earthquake damage |
| `beaver_damage_mitigation` | Reduce beaver attack damage |

### Distance Metrics

- **Chebyshev (max distance)**: For action range checks (`actionRange`)
- **Manhattan (L1 distance)**: For pathfinding and routing
- **Bonus cells**: Every 7th grid position (7, 14, 21, ...) provides resource bonuses

---

## 🚀 Usage

### Starting the Visualizer

```bash
python main.py
```

1. Copy your game authentication token from DatsSol
2. Paste into the "Токен" (Token) field
3. Click "Подключиться" (Connect)
4. Bot auto-launches in background; visualizer shows live updates

### Running Bot Standalone

```bash
python bot.py <AUTH_TOKEN>
```

**Optional flags:**
```bash
python bot.py <TOKEN> --seed 42 --max-turn 500 --logs-every 10
```

| Flag | Purpose |
|------|---------|
| `--seed` | RNG seed for reproducible decisions |
| `--max-turn` | Stop bot after N turns |
| `--logs-every` | Fetch game logs every N turns |

### Bot Integration Mode (stdin/stdout)

The bot supports streaming mode for integration:

```bash
python bot.py "-"
```

Send arena JSON via stdin; bot outputs command JSON via stdout.

---

## 🔧 Technical Details

### API Integration

**Endpoints:**
- `GET /api/arena` — Fetch current game state
- `GET /api/logs` — Retrieve game event logs
- `POST /api/command` — Submit turn commands

**Authentication:** Token passed via `X-Auth-Token` header

**Response Handling:**
- Automatic error recovery with exponential backoff (max 8x)
- HTTP 429 (rate limit) / 24 (custom error) trigger 1s delay
- All other errors trigger 0.6s retry delay

### Threading & Concurrency

**Visualizer (`main.py`):**
- `CoordinatorThread` — Async arena polling and command execution
- `GameAPIWorker` — Parallel API requests (logs, arena)
- `GameMapWidget` — Non-blocking rendering engine

**Bot (`bot.py`):**
- Synchronous polling loop with adaptive sleep scheduling
- Timing precision: ±50ms relative to server turn deadline

### Geometry Utilities

```python
# Chebyshev distance for action range
dist = max(abs(x1-x2), abs(y1-y2))

# Adjacent cells (4-connected)
neighbors = [(x±1, y), (x, y±1)]

# Bonus cell detection
is_bonus = (x % 7 == 0) and (y % 7 == 0)

# Range check (Chebyshev)
in_range = (abs(x1-x2) <= r) and (abs(y1-y2) <= r)
```

---

## 📊 Strategy Analysis

### Strengths

✅ **Robust resource management** — Prioritizes construction completion to prevent loss  
✅ **Defensive focus** — Maintains main control center integrity  
✅ **Upgrade automation** — Intelligent tech progression  
✅ **Error recovery** — Graceful handling of API failures  

### Limitations

⚠️ **No enemy prediction** — Reactive only, doesn't anticipate opponent moves  
⚠️ **Limited pathfinding** — All moves assume direct adjacency (action range)  
⚠️ **Static priority order** — No dynamic strategy adjustment mid-game  
⚠️ **Beaver AI** — Basic distance-based threat evaluation  

### Future Improvements

- [ ] BFS pathfinding for multi-step strategies
- [ ] Enemy prediction model based on log history
- [ ] Machine learning for upgrade prioritization
- [ ] Procedural strategy templates (defensive, expansionist, tech-focused)
- [ ] Multi-bot coordination system

---

## 📦 Dependencies

```python
# API & HTTP
requests>=2.28.0

# GUI Framework
PyQt6>=6.0.0

# Standard Library (included)
json, time, subprocess, threading, dataclasses
```

Install dependencies:
```bash
pip install requests PyQt6
```

---

## 🎓 Code Quality

### Design Patterns Used

- **Strategy Pattern** — Game state analysis and decision logic
- **Builder Pattern** — Command validation and payload construction
- **Observer Pattern** — PyQt signals for async communication
- **Dataclass Pattern** — Immutable game entities (Plantation, Enemy, etc.)

### Code Metrics

| File | Lines | Classes | Functions |
|------|-------|---------|-----------|
| `bot.py` | 647 | 5 | 40+ |
| `main.py` | 1132 | 5 | 60+ |

---

## 📝 Example Output

**Game Log Example:**
```
[2024-01-15 14:23:45] Plantation built at (23, 45)
[2024-01-15 14:24:12] Construction in progress at (24, 45): 15/50
[2024-01-15 14:24:40] Beaver defeated at (50, 30)
[2024-01-15 14:25:08] Upgrade: signal_range → Level 2
```

**Bot Decision Log:**
```
Turn 142: 5 commands queued
  → Complete construction at (24, 45)
  → Repair main center HP 35→45
  → Expand to (22, 45) [bonus cell]
  → Attack beaver at (50, 30) HP 8/15
  → Upgrade: signal_range
```

---

## 📄 License

This project was created as part of a programming course assignment.

---

## 👤 Author

Sergey Krivoshchapov

**Contact & Links:**  
- GitHub: [SergeyKrivoshchapov](https://github.com/SergeyKrivoshchapov)
- DatsSol Game: [games.datsteam.dev](https://games.datsteam.dev)

---

## 🤝 Contributing

Suggestions for improvements:
1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-improvement`)
3. Commit changes with descriptive messages
4. Submit a pull request with detailed explanation

---

**Last Updated:** January 2024  
**Game Version:** DatsSol (Landscape Simulation)  
**Python Version:** 3.10+
