# Claude plays Minecraft Bedrock

A workflow for putting Claude in control of real players on a Bedrock server. Each companion joins as a player and
looks at the world. Claude decides what it says and does, and the game carries that out the way a player would:
walking with real controls, mining with a tool, crafting from what it carries, and fighting, building and chatting.
There are no cheats unless you give them.

You decide who plays. A companion is a few lines of JSON (name, personality, who it looks after, a role). A role is
a small text file of switches plus instructions. Every action is declared once in `actions.json`. The characters
in `examples/` are just that: examples to copy from.

```
     what it sees (JSON)                    one decision (JSON)
 ┌──────────────┐  ─────────────▶  ┌──────────┐  ─────────────▶  ┌──────────────────┐
 │  the game    │                  │  brain   │                  │  the game        │
 │  main.js     │  ◀── chat ─────  │ brain.py │  ── say, do ──▶  │  main.js runs    │
 │  look        │                  │ + Claude │                  │  the action      │
 └──────────────┘                  └──────────┘                  └────────┬─────────┘
                                                                          │ real controls
                                                                 ┌────────▼─────────┐
                                                                 │  body.js: the    │
                                                                 │  player's client │
                                                                 └──────────────────┘
```

| Part | File | What it does |
| --- | --- | --- |
| **Body** | `body.js` | A [bedrock-protocol](https://github.com/PrismarineJS/bedrock-protocol) client that joins with its own name and skin, and moves with real player input, so others see legs walking, the head turning and arms swinging. The server does the physics. |
| **Hands** | `Claude_BP/scripts/main.js` | A behaviour pack script that carries out actions: route-finding (A*), mining, crafting, building, fighting, chests, bows, portals and the taxi. It never breaks blocks players placed. |
| **Mind** | `brain.py` | Every few seconds, and as soon as someone talks to it, it asks the game what the companion sees and asks Claude (`claude -p`, Haiku or Sonnet) for one decision: what to say, what to do, a note to itself, a lesson for its journal. |
| **Map** | `map_server.py`, `map.html` | An optional live top-down map that any device on the network can open. |

## Requirements

- macOS or Linux with Docker. The Bedrock server image runs under x86 emulation on Apple Silicon.
- Python 3.10 or newer.
- The [Claude Code](https://claude.com/claude-code) CLI, logged in. Each decision is one `claude -p` call. No API key
  is stored in this repo.
- `bot/install.sh` once. It installs bedrock-protocol into `bot/node_modules` inside Docker, and builds its network
  module, so you don't need Node on the host.

## Quick start

```sh
bot/install.sh                              # once
python3 server/play_server.py start         # Bedrock server in Docker, UDP 19132, a fresh survival world
# companions.json: set "buddy" to your gamertag
python3 brain.py start                      # the companion joins and starts thinking
python3 map_server.py &                     # optional: live map on http://localhost:8765
```

Join from Minecraft (**Play > Servers > Add Server**) using the address printed by `play_server.py status`, port
19132. Say hi to Claude in chat.

```sh
COMPANIONS=examples/family.json python3 brain.py start   # play with the example cast instead
python3 brain.py stop
python3 server/play_server.py cmd say hi    # type a console command into the live game
python3 server/play_server.py update        # install changed packs and restart (the world is kept)
python3 server/play_server.py stop
```

Script changes load without kicking anyone: copy `Claude_BP` into `server/data/behavior_packs/`, then run
`play_server.py cmd reload`.

## Making your own companions

### 1. A companion: `companions.json`

```json
{"name": "Pip", "buddy": "YourGamertag", "about": "a cheerful farmer who loves animals", "role": "friend",
 "skin": "claude", "slim": false, "model": "haiku", "enabled": true}
```

| Field | Meaning |
| --- | --- |
| `name`, `about` | The player's name and personality. `about` goes straight into Claude's instructions. |
| `buddy` | The player it looks after. Use `"-"` for nobody in particular (like the taxi). |
| `role` | A file in `roles/`. If left out, it's `friend`. |
| `skin`, `slim` | `skins/<skin>.b64`. `skins/paint.py` paints new ones. |
| `model` | `haiku` (fast, cheap) or `sonnet` (smarter, slower). |
| `think_every`, `speak_gap` | Seconds between decisions when nothing happens, and between lines nobody asked for. |
| `aliases` | Other names it answers to ("taxi", "coach"). |
| `traits` | Overrides any of its role's traits for this one companion, e.g. `{"armoured": true}`. |
| `deal` | A deal file (see `examples/deals/`). The companion won't help until its kid makes a promise in chat. |
| `listens_to`, `manages`, `crew`, `members`, `pester`, `band` | Teams: a builder crew that listens to an architect, a crew grown into N players, a baby's targets, a pop band. See `examples/family.json`. |

### 2. A role: `roles/<name>.md`

A JSON header of traits, then the instructions that role adds:

```
---
{"about": "A quiet bodyguard ...",
 "traits": {"company": {"with": "buddy", "distance": 7}, "listens": "buddy_only", "speaks_every": null, "armoured": true},
 "extra_actions": []}
---
YOU ARE ONE OF {buddy}'S PROTECTORS ({team}). ...
```

The traits the brain understands are listed, with their defaults, in `TRAITS` in `brain.py`:

- **Who it stays near and how far:** `company`.
- **Whose chat it acts on:** `listens`, either `buddy_and_named`, `buddy_only` or `anyone_by_name`.
- **How chatty it is:** `speaks_every`.
- **Giving its buddy space:** `goes_to_buddy_only_when_called`.
- **Planning and building:** `plans` (its own projects) and `designs` (for a crew it manages).
- **Fighting:** `pacifist_unless_armed`.
- **Power:** `armoured` (diamond armour and an enchanted sword) and `strong` (hits like four players, mines three times
  as fast, catches up when left behind).
- **Thinking ahead:** `planner` (a ledger of requests and multi-step plans).
- **Extra knowledge:** `pro_tips` (gets `PRO_TIPS.md`).
- **Restraint:** `big_builds_only_when_asked` (portals only when its kid asks).
- **Build sites:** `site_spot`, `stays_at_site` and `holds_crew_until_project_done`.
- **Thinking speed:** `reacts_to_every_line`.

In the text, `{buddy}`, `{team}`, `{manages}` and `{pester}` are filled in. A `=== note_for_crew ===` section is given
to companions that listen to this role, and `=== note_for_targets ===` to the ones it pesters.

The example roles are `friend` (the default), `guard`, `mentor`, `champion`, `taxi`, `architect`, `coach` and `baby`.

### 3. An action: `actions.json`

Each action is declared once:

```json
"shoot": {"runs_in": "game", "secs": 40, "offer": "all",
          "use": "{\"act\":\"shoot\",\"mob\":\"skeleton\"|\"hostile\",\"count\":1}",
          "doc": "shoot with a bow from a distance (needs a bow and arrows)"}
```

Claude's list of actions is built from `use` and `doc`. The fields:

- **`runs_in`:** where the action runs. `game` actions need a runner with the same name in `RUNNERS` in
  `Claude_BP/scripts/main.js`, which is called every tick with `(player, task)` until it calls `finish()`. `brain`
  actions are handled in `brain.py`.
- **`offer`:** who sees the action. `all` is everyone. `role` is only roles that list it in `extra_actions`.
  `companion` is only companions with the setting named in `when`. `internal` is started by the game itself.
- **`secs`:** the time limit, which the game reads too.

After editing, run `python3 tools/gen_actions.py`. It shares the list with the game script, and the script warns
at startup about any action that has no runner.

## Tests

```sh
python3 test/test_wiring.py   # no server needed: fails if anything declared isn't wired up (see below)
python3 test/portal_test.py   # test server: chest store/take, build and light a portal, walk in, arrive in the Nether, walk
python3 test/brain_test.py    # test server + the real brain: a stand-in player asks for help in chat, the companion
                              # comes over and crafts a crafting table (about 3 minutes, uses your claude CLI)
```

`test_wiring.py` is the guard. It fails if:

- an action has no runner, or a runner isn't declared
- the generated list is stale
- the craft list and the recipes disagree
- a role uses a trait the brain doesn't know, or the brain checks a role by name instead of a trait
- a companion points at a missing role, skin or deal
- any example companion's instructions fail to build, or leave a `{placeholder}` unfilled

The game tests run on a throwaway server (`test/bds_test.py`) that publishes no ports, so they work while the play
server is up.

## Safety

- The server runs with **online-mode off**, because companions have no Microsoft account. Anyone on your network can
  join under any name. Keep it on your LAN and don't forward port 19132.
- Companions only have normal player abilities, plus whatever traits you give them.
- Player builds are protected. Blocks players place are remembered and never broken, and companions don't build near
  them or pick up other people's things.

## Extras

- `KNOWLEDGE.md` is what every companion knows about the game. `PRO_TIPS.md` is extra know-how for `pro_tips` roles.
- `songs/make.py` makes the example band's jingles, which are already built in `Claude_RP/sounds`.
- The Bedrock server is pinned to 1.26.52.3 in `server/play_server.py`, because newer versions may drop the RakNet
  transport this setup relies on.

## Licence

MIT. See `LICENSE`. Minecraft is a trademark of Mojang/Microsoft; this project is not affiliated with them.
