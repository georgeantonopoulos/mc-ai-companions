# AI companions for Minecraft Bedrock

Claude-powered players that join a home Bedrock server and play survival alongside kids: a pro coach, a
champion bodyguard, a Greek taxi driver with his own taxi, an eccentric architect, a speedrunner, a pop band and
more. They are real players: no cheats by default. They walk with real movement controls, mine, craft, fight,
build, store things in chests, shoot bows, and can build a Nether portal and travel through it.

Each companion has three parts:

| Part | File | What it does |
| --- | --- | --- |
| **Body** | `body.js` | A [bedrock-protocol](https://github.com/PrismarineJS/bedrock-protocol) client that joins as a player with its own name and skin, and sends movement like a real device. |
| **Hands** | `Claude_BP/scripts/main.js` | A behaviour pack script (`@minecraft/server` 2.8.0) that carries out actions: route-finding, mining, crafting, building, fighting, the taxi, portals. It never breaks blocks players placed. |
| **Mind** | `brain.py` | Every few seconds, it asks the game what the companion sees and asks Claude (via the `claude` CLI, Haiku or Sonnet) what to say and do. Each companion keeps a journal, a plan and a ledger in `data/<Name>/`. |

There's also a live top-down map (`map_server.py`, `map.html`) that you can open on any device on the network.

## Requirements

- macOS or Linux with Docker. The Bedrock server image runs under x86 emulation on Apple Silicon.
- Python 3.10 or newer.
- The [Claude Code](https://claude.com/claude-code) CLI, logged in. The brains call `claude -p`.
- Node is not needed on the host: bodies run in the `node:20` image. Run `npm install` once inside `bot/` to get
  `bedrock-protocol`, for example: `docker run --rm -v "$PWD/bot:/bot" -w /bot node:20 npm install`.

## Quick start

```sh
python3 server/play_server.py start        # Bedrock server in Docker on UDP 19132, a fresh survival world
# edit companions.json: set "buddy" to your players' gamertags, "enabled": true for the ones you want
python3 brain.py start                     # each enabled companion joins and starts thinking
python3 map_server.py &                    # live map on http://localhost:8765
```

On the device: **Play > Servers > Add Server**, using the address printed by `play_server.py status` and port 19132.

```sh
python3 brain.py stop                      # all companions leave
python3 server/play_server.py cmd say hi   # type a console command into the live game
python3 server/play_server.py update       # install changed packs and restart (the world is kept)
python3 server/play_server.py stop
```

Script changes load without kicking anyone: copy `Claude_BP` into `server/data/behavior_packs/` and run
`play_server.py cmd reload`.

## The companions

`companions.json` lists them. Each entry has a name, a buddy (the player they look after), a personality
(`about`), a skin (`skins/<name>.png`, painted by `skins/paint.py`), a model (`haiku` or `sonnet`), and an optional
role. The roles are defined in `ROLES` in `brain.py`:

- **mentor:** a pro coach heading for the Ender Dragon. It can be set up to stay loyal only after the kid
  promises something (the example deal: listen to your parents and let them rest).
- **champion:** a strong bodyguard that keeps food, torches and tools stocked, and catches up when left behind.
- **taxi:** drives players anywhere in a custom taxi entity (`Claude_BP/entities/taxi.json` and the matching model
  in `Claude_RP`).
- **architect / coach / baby / guard:** a builder who designs and builds whole houses, a speedrun coach, a
  cheeky baby, and armoured protectors.

`PRO_TIPS.md` and `KNOWLEDGE.md` are the game knowledge the brains are given.

## Safety

- The server runs with **online-mode off**, because companions have no Microsoft account. Anyone on your network
  can join under any name. Keep it on your LAN and don't forward port 19132.
- Companions only have normal player abilities, apart from what you deliberately give them in the role prompts.
- Player builds are protected. Blocks players place are remembered and never broken, and companions don't build
  or pick things up near them.

## Tests

`test/drive_test.py` and `test/portal_test.py` run on a throwaway server (`test/bds_test.py`) that publishes no
ports, so they work while the play server is up:

```sh
python3 test/portal_test.py   # chest store/take, build and light a portal, walk in, arrive in the Nether, walk around
```

## Extras

- `songs/make.py` makes the Pixel Pops' jingles, which are already built in `Claude_RP/sounds`. It needs numpy,
  scipy, soundfile and the macOS `say` voices.
- The Bedrock server version is pinned to 1.26.52.3 in `server/play_server.py`, because newer versions may drop the
  RakNet transport this setup relies on.

## Licence

MIT. See `LICENSE`. Minecraft is a trademark of Mojang/Microsoft; this project is not affiliated with them.
