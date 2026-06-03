# DatsSol AlgoBot — Game AI Visualizer & Strategy Implementation

A sophisticated game bot framework for the **DatsSol** strategy game, featuring an interactive PyQt6-based visualizer and an intelligent AI decision-making engine.

## Project Overview

This project combines two key components:

1. **AI Bot Engine** (`bot.py`) — Autonomous game strategy agent with real-time decision-making
2. **Interactive Visualizer** (`main.py`) — Real-time game state visualization with live monitoring

The bot participates in a resource management and territory control game where players build plantations, manage upgrades, and defend against enemies while navigating environmental hazards.

---

## Architecture

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

## Technical Details

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

### Strengths

✅ **Robust resource management** — Prioritizes construction completion to prevent loss  
✅ **Defensive focus** — Maintains main control center integrity  
✅ **Upgrade automation** — Intelligent tech progression  
✅ **Error recovery** — Graceful handling of API failures  

