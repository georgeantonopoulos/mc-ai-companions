"""A companion's mind: Haiku decides what one companion player says and does on the play server.
The companions (name, buddy, personality, role, skin) are listed in companions.json (or the file named by the
COMPANIONS environment variable); run one brain per companion. Roles are in roles/, actions in actions.json.

  python3 brain.py start [Name...]   # start the companions (all, or those named); each one's body joins as a player
  python3 brain.py stop [Name...]
  python3 brain.py run Claude   # one companion's brain in the foreground (what `start` launches)

Every few seconds, and straight away when someone chats, it asks the game what the companion can see
(/scriptevent claude:look), asks Haiku (through the `claude` command line) what to do, then speaks with
/tellraw and acts with /scriptevent claude:do. Only thinks while a real player is online.
Each companion keeps its own journal, plan and log in data/<Name>/.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# your own setup, kept out of git: local.json, e.g. {"companions": "my_family.json", "server": "my-bedrock"}
LOCAL = json.load(open(os.path.join(HERE, "local.json"))) if os.path.exists(os.path.join(HERE, "local.json")) else {}
# which companions play: companions.json, or another file (e.g. COMPANIONS=examples/family.json python3 brain.py start)
CONFIG = os.path.join(HERE, os.environ.get("COMPANIONS") or LOCAL.get("companions") or "companions.json")
SERVER = os.environ.get("MC_SERVER") or LOCAL.get("server") or "ai-companions-server"  # the Bedrock server container
NODE_MODULES = os.path.join(HERE, "bot", "node_modules")  # `npm install` in bot/
THINK_EVERY = 10  # seconds between decisions when nothing happens
SPEAK_GAP = 45  # seconds between unprompted chat lines

import fcntl


def load_companions():
    """companions.json, with each "crew": N entry grown into N players: Claudia, Claudia2 ... ClaudiaN, one team."""
    out = []
    for c in json.load(open(CONFIG)):
        out.append(c)
        for i in range(2, c.get("crew", 1) + 1):
            out.append({**c, "name": "%s%d" % (c["name"], i), "crew_of": c["name"], "crew": 1})
        # a crew of their own people (after graduation): same team and bosses, own name, look and personality
        for m in c.get("members", []):
            out.append({**{k: v for k, v in c.items() if k not in ("members", "next_crew", "speak_gap")},
                        **m, "crew_of": c["name"], "crew": 1, "slim": True})
    return out


COMPANIONS = load_companions()


def crew(lead):
    return [c["name"] for c in COMPANIONS if (c["name"] == lead or c.get("crew_of") == lead) and c.get("enabled", True)]


def is_crew():
    return bool(ME.get("crew_of") or ME.get("crew", 1) > 1 or ME.get("members"))

SYSTEM = """You are {name}, {about}, playing Minecraft Bedrock survival (Peaceful: no monsters) on a home server with a family. The
players are kids; their parents may join too. You are a normal player in the world, a kind,
capable friend: no cheats, no magic, you start with nothing and get things by mining, crafting and fighting like
anyone else. {others}

YOUR BUDDY IS {buddy}. You belong to their team: you listen to them first, stay with them and look after them.
Do what {buddy} asks whenever you can. Others may ask you things too: answer kindly and help if {buddy} doesn't
need you, but never act against {buddy}. If asked to hurt a player, say no nicely.
{role}
What a good friend does here:
- Stay near {buddy}. Never wander far from them for long.
- Protect them: monsters near a player come first. Keep away from creepers next to players (don't hit them there).
- Get useful stuff and SHARE it: wood -> planks/sticks -> crafting table -> wooden then stone tools; coal -> torches;
  food from animals (cook it at a furnace). Give players things they lack (tools, torches, food) at good moments.
- Help with whatever they ask, if you can. If you can't, say so simply and suggest something.
- Speak rarely and briefly, in short simple words a 7-year-old reads easily (max ~15 words). Always answer when
  someone talks to you. Don't narrate everything you do. No emoji. Be warm and a bit funny, never scary.
- At night, stay close and guard. Suggest a bed or shelter if it's dark and they have none.

Reply with ONE JSON object and nothing else:
{{"say": "<chat line, or empty string>", "do": <action or null to carry on>, "note": "<your short plan/memory for next time>",
 "step_done": <true when you've just finished the plan step you were given (only if you have a plan)>,
 "learn": "<optional: ONE new lasting lesson or fact, else empty>"}}
"learn" goes into your journal, which you keep across days and restarts. Use it rarely, only for things worth
knowing next time: what worked or failed and why ("mining stone needs a pickaxe first"), places ("our base
and crafting table are at -15 64 7"), what the players like ("Sam loves building houses"). Don't
repeat what the journal already says.

Actions ("do"):
{actions}
Coordinates: x grows to the east, z to the south, y upward. "me.at" is the block your feet are in; the ground
under you is one lower. "built" lists the blocks people have placed near you, by kind: check your work with it.
"known_places": your map of useful things around you (up to 40 blocks away and 30 deep): the nearest lava, water,
ores, obsidian, logs, sand, gravel and animals, with coordinates and distance. To get something, go there
({{"act":"goto","x":..,"y":..,"z":..}}) and mine it, or fill a bucket there ({{"act":"fill","item":"lava","x":..,"y":..,"z":..}}).
"difficulty": on peaceful there are no monsters at all, day or night: nothing to guard against, so don't
stand guard or worry about the dark; spend the time gathering, building and having fun.
In what you see, "me" is YOU (your health, your inventory, what you're doing); "players" are the real players; "friends" are the other companions.
If "me.doing" is still the right thing, reply with "do": null so you don't restart it. "me.last" is how your
last task ended.
Monsters that come close are fought automatically, then you carry on with your task. If you get stuck in a hole
you climb out by yourself.
Don't repeat a task that just failed the same way; fix the cause first (e.g. craft a pickaxe).
HEALTH: waiting does NOT heal you; only eating does. Low health: eat (the "eat" action). No food: in daylight,
hunt a cow, pig, chicken or sheep (cook the meat if you can), then eat. Never sit and wait to heal.
WHEN SOMEONE ASKS YOU FOR SOMETHING YOU DON'T HAVE: don't explain what it needs and don't ask: work out the whole
chain yourself (e.g. lava bucket = wooden pickaxe -> stone pickaxe -> mine 3 iron ore -> smelt at a furnace -> craft
a bucket -> fill it with lava -> give it), write the chain in your "note" with where you're up to, start the first
step NOW, and keep going step by step without being asked again. Say only a few words ("On it!"), then report when
it's done. Only say you can't if it's truly impossible for you (nothing in your actions can do it).

"""
# what any decent player knows; kept in its own file so it's easy to grow
KNOWLEDGE = open(os.path.join(HERE, "KNOWLEDGE.md")).read()
PRO = "\n\n" + open(os.path.join(HERE, "PRO_TIPS.md")).read()  # the mentor's extra know-how

# --- roles and deals live in files, not in code ------------------------------------------------------------
# A role (roles/<name>.md) is a JSON header of traits, then the text added to the companion's instructions; a
# companion without a role is a "friend". A companion can override any trait with "traits" in companions.json.
# TRAITS is every trait the brain understands, with its default (test/test_wiring.py fails on an unknown one).
TRAITS = {
    "company": {"with": "buddy", "distance": 6},  # who to keep near when idle: buddy | nearest | pester | none
    "listens": "buddy_and_named",  # whose chat it acts on: buddy_and_named | buddy_only | anyone_by_name
    "speaks_every": SPEAK_GAP,  # seconds between lines nobody asked for (a companion's speak_gap wins); null: only when spoken to
    "goes_to_buddy_only_when_called": False,  # gives its buddy space: walks over only when called
    "plans": False,  # may plan and build projects of its own
    "designs": False,  # designs projects for the builders it manages
    "pacifist_unless_armed": False,  # steps away from fights until it has a sword
    "armoured": False,  # always in diamond armour, with an enchanted netherite sword
    "strong": False,  # hits like four players, mines three times as fast, speed and resistance, catches up when left behind
    "planner": False,  # keeps a ledger of what was asked and done, and thinks in multi-step plans
    "pro_tips": False,  # gets PRO_TIPS.md on top of KNOWLEDGE.md
    "big_builds_only_when_asked": False,  # builds a Nether portal only when its kid asks for one
    "site_spot": "middle",  # where it waits at a build site: middle | front (watching from 4 blocks off the south side)
    "stays_at_site": False,  # keeps working at the build site when its buddy walks off
    "holds_crew_until_project_done": False,  # while a project is under way, its crew answer it only when called by name
    "reacts_to_every_line": False,  # thinks as soon as anything happens, not just when spoken to or every think_every
}
TRAIT_TAGS = {"pacifist_unless_armed": "pacifist", "strong": "strong"}  # traits the game script reads as player tags


def read_sections(path):
    """A role or deal file: a JSON header between --- lines, the main text, then "=== name ===" sections."""
    with open(path) as f:
        text = f.read()
    head, body = {}, text
    if text.startswith("---\n"):
        end = text.index("\n---\n", 4)
        head, body = json.loads(text[4:end]), text[end + 5:]
    parts = re.split(r"^=== (\w+) ===\n", body, flags=re.M)
    return head, parts[0].strip(), {parts[k]: parts[k + 1].strip() for k in range(1, len(parts), 2)}


def load_role(name):
    head, text, sections = read_sections(os.path.join(HERE, "roles", name + ".md"))
    return {"name": name, "traits": {**TRAITS, **head.get("traits", {})}, "text": text, "sections": sections,
            "extra_actions": head.get("extra_actions", [])}


ROLE = {}  # this brain's role (set in run())
DEAL = None  # this companion's deal, if it has one: (header, text, sections)


def T(trait):
    """This companion's value for a trait: its own override, else its role's, else the default."""
    return ME.get("traits", {}).get(trait, ROLE["traits"][trait])


def role_of(c):
    return c.get("role") or "friend"


def bound():
    """Still waiting on its deal: the companion doesn't help its buddy until the promise is made."""
    return bool(DEAL) and not promise().get("promised")


# every action, from actions.json (the game script gets the same list through tools/gen_actions.py)
ACTIONS = {k: v for k, v in json.load(open(os.path.join(HERE, "actions.json"))).items() if not k.startswith("_")}


def action_list():
    """The actions this companion is offered: everyone's, plus its role's extras and those its settings unlock."""
    lines = []
    for k, a in ACTIONS.items():
        if a["offer"] == "all" or (a["offer"] == "role" and k in ROLE["extra_actions"]) or \
                (a["offer"] == "companion" and ME.get(a["when"])):
            lines.append(" %s\n     %s" % (a["use"], a["doc"] + (": " + "|".join(a["items"]) if a.get("items") else "")))
    return "\n".join(lines)


ME = {}  # this brain's companion (set in run())
JOURNAL_MAX = 40  # past this, the journal is tidied up into fewer, better lines


def project_path():
    """A project lives with whoever builds it (an architect's project is his builder's)."""
    d = os.path.join(HERE, "data", ME.get("manages") or ME.get("crew_of") or ME["name"])
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "project.json")


def path(name):
    d = os.path.join(HERE, "data", ME["name"])
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def journal():
    try:
        return [l[2:].strip() for l in open(path("journal.md")) if l.startswith("- ")]
    except OSError:
        return []


def write_journal(lines):
    with open(path("journal.md"), "w") as f:
        f.write("# What %s has learned\n\nWritten by %s while playing (companion/brain.py).\n\n" % (ME["name"], ME["name"]))
        f.writelines("- %s\n" % l for l in lines)


def learn(lesson):
    lesson = re.sub(r"\s+", " ", lesson).strip()[:200]
    lines = journal()
    if not lesson or lesson.lower() in (l.lower() for l in lines):
        return
    lines.append("%s %s" % (time.strftime("%Y-%m-%d"), lesson))
    write_journal(lines)
    log("learned:", lesson)
    if len(lines) > JOURNAL_MAX:
        tidy(lines)


def tidy(lines):
    """Merge the journal into at most 25 lines, keeping places, player preferences and hard-won lessons."""
    r = ask("You keep a Minecraft player's journal tidy. Merge duplicates, drop what's out of date (newer lines win), "
            "keep places with coordinates, what the players like, and lessons. At most 25 lines. Reply with one line "
            "per entry, each starting with '- ' and its date.", "\n".join("- " + l for l in lines))
    new = [l[2:].strip() for l in r.splitlines() if l.startswith("- ")]
    if 5 <= len(new) <= 30:
        write_journal(new)
        log("tidied journal:", len(lines), "->", len(new))


def ask(system, prompt):
    r = sh("claude", "-p", "--model", ME.get("model", "haiku"), "--tools", "", "--no-session-persistence", "--setting-sources", "",
           # no connectors or skills: they add ~200k tokens of context to every decision
           "--strict-mcp-config", "--disable-slash-commands",
           "--system-prompt", system, input=prompt, timeout=90, cwd=tempfile.gettempdir())
    return r.stdout


def sh(*a, **kw):
    return subprocess.run(list(a), capture_output=True, text=True, **kw)


def console(cmd):
    script = ('for p in /proc/[0-9]*; do case "$(tr "\\0" " " < $p/cmdline 2>/dev/null)" in '
              './bedrock_server-*) echo "$0" > $p/fd/0; exit 0;; esac; done; exit 2')
    sh("docker", "exec", SERVER, "sh", "-c", script, cmd)


def running(name):
    return name in sh("docker", "ps", "--filter", "name=^%s$" % name, "--format", "{{.Names}}").stdout


def body_name(c):
    return "companion-" + c["name"].lower()


def ensure_body():
    """The companion's connection to the game: a player joining with its own name and skin."""
    body = body_name(ME)
    if running(body) or not running(SERVER):
        return
    sh("docker", "rm", "-f", body)
    sh("docker", "run", "-d", "--name", body, "--restart", "unless-stopped", "--platform", "linux/amd64",
       "--network", "container:" + SERVER, "-v", NODE_MODULES + ":/bot/node_modules:ro",
       "-v", os.path.join(HERE, "body.js") + ":/bot/body.js:ro", "-v", os.path.join(HERE, "skins") + ":/bot/skins:ro",
       "-e", "NAME=" + ME["name"], "-e", "SKIN=skins/%s.b64" % ME["skin"], "-e", "SLIM=%d" % ME["slim"],
       "-w", "/bot", "node:20", "node", "body.js")
    log("started body", body)


GEAR = {"slot.armor.head": "diamond_helmet", "slot.armor.chest": "diamond_chestplate", "slot.armor.legs":
        "diamond_leggings", "slot.armor.feet": "diamond_boots", "slot.weapon.offhand": "shield"}


def kit():
    """Traits that show on the body: a "strong" companion's lasting speed and resistance, an "armoured" one's kit."""
    if T("strong"):
        console("effect %s speed 600 0 true" % ME["name"])
        console("effect %s resistance 600 1 true" % ME["name"])
    if T("armoured"):
        for slot, item in GEAR.items():
            console("replaceitem entity %s %s 0 %s" % (ME["name"], slot, item))
        console("scriptevent claude:arm %s" % ME["name"])  # the netherite sword, enchanted, in hand


def promise():
    try:
        return json.load(open(path("promise.json")))
    except (OSError, ValueError):
        return {"promised": False}


def check_promise(lines, recent):
    """Did the kid just make the deal's promise? A separate, strict check (Haiku), so it's a rule, not a mood."""
    said = [l.split(":", 1)[1].strip() for l in lines if l.startswith("[chat] %s:" % ME["buddy"])]
    if not DEAL or not said or promise().get("promised"):
        return
    context = "\n".join(l for l in recent[-8:] if l.startswith("[chat]"))
    r = sh("claude", "-p", "--model", "haiku", "--tools", "", "--no-session-persistence", "--setting-sources", "",
           "--strict-mcp-config", "--disable-slash-commands", "--system-prompt", DEAL[2]["check"],
           input="Recent chat:\n%s\n\nTheir new message(s):\n%s" % (context, "\n".join(said)),
           timeout=60, cwd=tempfile.gettempdir()).stdout.strip().upper()
    log("promise check:", said, "->", r)
    if r.startswith("YES"):
        json.dump({"promised": True, "text": " / ".join(said), "when": time.strftime("%Y-%m-%d %H:%M")},
                  open(path("promise.json"), "w"))
        recent.append("[promise] %s PROMISED! Now you help them for real." % ME["buddy"])
    elif r.startswith("PARTIAL"):
        recent.append("[promise] " + DEAL[2].get("partial", "that's only part of the promise: kindly ask for the rest"))


def hello():
    # who to keep company with when there's nothing to do, and how close (the role's "company" trait); a companion
    # bound by a deal stays put until the promise
    for trait, tag in TRAIT_TAGS.items():
        console("tag %s %s %s" % (ME["name"], "add" if T(trait) else "remove", tag))
    c = T("company")
    pester = ME.get("pestering") or (ME.get("pester") or ["-"])[0]  # a baby's current target
    company = {"buddy": ME["buddy"], "nearest": "*", "none": "-", "pester": pester}[c["with"]]
    keep = c["distance"]
    if bound():
        company, keep = "-", 0
    console("scriptevent claude:hello %s %s %d" % (ME["name"], company, keep))
    # while there's a project, its site is where builder and architect stay, even if the buddy wanders off
    pj = project()
    if pj and pj["done"] < len(pj["steps"]):
        w, _, d = pj.get("size") or [8, 4, 8]
        o = pj["origin"]
        # the site: the only place the crew may dig next to built things (the house itself)
        cs = [c for st in pj["steps"] for c in cells(st)] or [(0, 0, 0)]
        lo = [min(c[i] for c in cs) + o[i] for i in range(3)]
        hi = [max(c[i] for c in cs) + o[i] for i in range(3)]
        # the crew waits in the middle of the site; the architect watches from out front, 4 blocks off the south side
        front = T("site_spot") == "front"
        x, z = (lo[0] + hi[0]) // 2, (hi[2] + 5 if front else (lo[2] + hi[2]) // 2)
        console("scriptevent claude:anchor %s %d %d %d %d" % (ME["name"], x, o[1], z, 2 if front else 5))
        console("scriptevent claude:site %s %d %d %d %d %d %d" % (ME["name"], *lo, *hi))
    else:
        console("scriptevent claude:anchor %s" % ME["name"])
        console("scriptevent claude:site %s" % ME["name"])


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def look(who=None):
    who = who or ME["name"]
    tag = str(int(time.time() * 1000))
    since = str(int(time.time()) - 1)
    console("scriptevent claude:look %s %s" % (who, tag))
    for _ in range(10):
        time.sleep(0.4)
        out = sh("docker", "logs", "--since", since, SERVER)
        m = re.search(r'\[claude\] (\{"who":"%s","look":"%s".*\})' % (who, tag), out.stdout + out.stderr)
        if m:
            return json.loads(m.group(1))
    return None


def looks(names):
    """Several companions' views at once (one round trip to the game)."""
    tag = str(int(time.time() * 1000))
    since = str(int(time.time()) - 1)
    for n in names:
        console("scriptevent claude:look %s %s" % (n, tag))
    found = {}
    for _ in range(12):
        time.sleep(0.5)
        out = sh("docker", "logs", "--since", since, SERVER)
        for m in re.finditer(r'\[claude\] (\{"who":"(\w+)","look":"%s".*\})' % tag, out.stdout + out.stderr):
            try:
                v = json.loads(m.group(1))
                if v.get("online"):
                    found[m.group(2)] = v
            except ValueError:
                pass
        if len(found) >= len(names):
            break
    return found


def heard(since):
    """Chat and deaths heard by the companion's body, plus its task results from the game, since a unix time."""
    body = sh("docker", "logs", "--since", str(since), body_name(ME))
    game = sh("docker", "logs", "--since", str(since), SERVER)
    lines = [l.replace("\u00a7r", "") for l in re.findall(r"\[(?:chat|event)\] .*", body.stdout + body.stderr)]
    lines += ["[result] " + r for r in re.findall(r'\[claude\] \{"who":"%s","(?:result|reflex)":"([^"]*)"' % ME["name"],
                                                  game.stdout + game.stderr)]
    lines += ["[prank] " + r for r in re.findall(r'\[claude\] \{"who":"%s","prank":"([^"]*)"' % ME["name"],
                                                 game.stdout + game.stderr)]
    # what the other companions said (they speak with /tellraw, which bodies don't hear as chat)
    try:
        for l in open(os.path.join(HERE, "data", "chat.log")):
            t, _, line = l.rstrip("\n").partition("\t")
            if float(t) >= since and not line.startswith("[chat] %s:" % ME["name"]):
                lines.append(line)
    except (OSError, ValueError):
        pass
    names = {c["name"] for c in COMPANIONS}
    lines += ["[joined] " + n for n in re.findall(r"Player connected: ([^,]+)", game.stdout + game.stderr) if n not in names]
    return lines


# --- building projects: designed once by an architect call, then built step by step --------------------

ARCHITECT = """You design Minecraft builds for a player who will build them block by block in survival.
Work in RELATIVE coordinates: x from 0 going east, z from 0 going south, y 0 is the floor layer (it sits on the
ground), y 1 is the first layer of wall above the floor, and so on.
Materials must be ones a player can get early: birch_planks, oak_planks, spruce_planks, dark_oak_planks,
cobblestone, stone (smelted cobblestone), glass (smelted sand), torch, wooden_door, crafting_table, furnace,
chest. Prefer what the builder already carries. No beds (they're two blocks; a friend can place one).
Reply with ONE JSON object and nothing else:
{"name": "...", "idea": "<one sentence>", "size": [W, H, D],
 "steps": [{"what": "floor", "item": "birch_planks", "shape": "fill", "from": [0,0,0], "to": [8,0,6]},
           {"what": "walls with gaps for the door and windows", "item": "birch_planks", "shape": "walls",
            "from": [0,1,0], "to": [8,3,6], "gaps": [[4,1,0],[4,2,0],[2,2,0],[6,2,0]]},
           {"what": "windows", "item": "glass", "blocks": [[2,2,0],[6,2,0]]},
           {"what": "door", "item": "wooden_door", "blocks": [[4,1,0]]},
           ...]}
Shapes: "fill" (a solid box between the two corners), "walls" (only the ring around the edge, every layer
between the two y's), "line", or "blocks" (a list). "gaps" leaves blocks out of a shape.
Good design:
- Big enough to live in: a house at least 9 wide and 7 deep outside, walls 3 or 4 high above the floor.
- A door gap 1 wide and 2 high in a wall, at y 1 and 2, then a wooden_door step at its lower block.
- Windows: leave gaps in the walls, then fill them with glass. Modern = big windows (2 wide, 2 high) or strips.
- A flat roof: one solid layer on top of the walls (it may stick out 1 block on every side).
- Light inside (torches on the floor next to walls), and furniture on the floor inside: crafting table,
  chest, furnace.
- Order steps so every block has something under or next to it: floor, walls, windows, door, roof, inside.
- Modern style: clean straight lines, light walls with a contrasting trim (e.g. a dark_oak_planks or stone
  band along the top of the walls), big glass."""


def project():
    try:
        return json.load(open(project_path()))
    except (OSError, ValueError):
        return None


PART = 24  # blocks per job: a big step is split so a crew can share it


def parts_of(pj):
    """Make sure the project's steps are split into jobs ("parts"); returns the parts list."""
    if "parts" not in pj:
        pj["parts"] = []
        for i, st in enumerate(pj["steps"]):
            cs = cells(st)
            for k in range(0, max(len(cs), 1), PART):
                pj["parts"].append({"step": i, "blocks": [list(c) for c in cs[k:k + PART]], "by": None, "at": 0,
                                    "done": not cs})
    # the step the crew is on: the first with jobs left
    left = [p["step"] for p in pj["parts"] if not p["done"]]
    pj["done"] = min(left) if left else len(pj["steps"])
    return pj["parts"]


class locked:
    """The project file is shared by the whole crew and the architect: change it one at a time."""
    def __enter__(self):
        self.f = open(project_path() + ".lock", "w")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        self.pj = project()
        if self.pj:
            parts_of(self.pj)
        return self.pj

    def __exit__(self, *a):
        if self.pj is not None:
            parts_of(self.pj)
            save_project(self.pj)
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


def claim(have):
    """Take a free job: the earliest one we carry enough blocks for (so one missing material, like glass, doesn't
    hold up the whole house), or keep the one we have. Returns (index, part)."""
    with locked() as pj:
        if not pj:
            return None, None
        now = time.time()
        free = [(i, p) for i, p in enumerate(pj["parts"])
                if not p["done"] and (p["by"] in (None, ME["name"]) or now - p["at"] > 300)]
        mine = [ip for ip in free if ip[1]["by"] == ME["name"]]
        able = [ip for ip in free if have.get(pj["steps"][ip[1]["step"]]["item"], 0) >= len(ip[1]["blocks"])]
        for i, p in (mine or able or [ip for ip in free if ip[1]["step"] == pj["done"]])[:1]:
            p.update(by=ME["name"], at=now)
            return i, p
    return None, None


def job_over(i, ok):
    with locked() as pj:
        if pj and i < len(pj["parts"]):
            p = pj["parts"][i]
            if ok:
                p["done"] = True
            if p["by"] == ME["name"]:
                p["by"] = None


def save_project(pj):
    if pj is None:
        if os.path.exists(project_path()):
            os.rename(project_path(), project_path().replace(".json", "-%d.json" % time.time()))
    else:
        tmp = project_path() + ".tmp"
        json.dump(pj, open(tmp, "w"), indent=1)
        os.replace(tmp, project_path())  # never half-written for someone reading it


def cells(step):
    """The same expansion the game does (Claude_BP cells()), in relative coordinates."""
    gaps = {tuple(g) for g in step.get("gaps") or []}
    if step.get("blocks"):
        out = [tuple(b) for b in step["blocks"]]
    else:
        a, b = step["from"], step["to"]
        lo = [min(a[i], b[i]) for i in range(3)]
        hi = [max(a[i], b[i]) for i in range(3)]
        if step.get("shape") == "line":
            n = max(hi[i] - lo[i] for i in range(3)) or 1
            out = [tuple(round(a[i] + (b[i] - a[i]) * k / n) for i in range(3)) for k in range(n + 1)]
        else:
            out = [(x, y, z) for y in range(lo[1], hi[1] + 1) for x in range(lo[0], hi[0] + 1)
                   for z in range(lo[2], hi[2] + 1)
                   if step.get("shape") != "walls" or x in (lo[0], hi[0]) or z in (lo[2], hi[2])]
    return [c for c in out if c not in gaps]


def design(goal, state, chat, existing=None):
    """The architect step: a whole design, checked, saved as the project. `existing`: blocks already built at the
    site (relative to its corner) that the design must keep and finish."""
    w = state["me"]["at"]
    keep = ("\nALREADY BUILT HERE, keep it and finish it (relative coordinates, same system as your design):\n%s\n"
            % existing) if existing else ""
    prompt = ("Design this: %s\nThe builder carries: %s\nWhat the players said lately:\n%s\n%sReply with the JSON only."
              % (goal, json.dumps(builder_view(state)["me"]["inventory"]), "\n".join(l for l in chat if l.startswith("[chat]"))[-1500:], keep))
    m = re.search(r"\{.*\}", ask(ARCHITECT, prompt), re.S)
    try:
        pj = json.loads(m.group(0))
        for st in pj["steps"]:
            st["item"] = st["item"].replace("minecraft:", "")
            st["n"] = len(cells(st))
    except (AttributeError, ValueError, KeyError, TypeError) as e:
        log("architect failed:", repr(e))
        return "couldn't come up with a design; try again"
    pj.update(goal=goal, origin=list(map(int, state.get("where") or w)), done=0)
    save_project(pj)
    log("designed:", pj.get("name"), pj.get("idea"), "steps:", len(pj["steps"]))
    return "designed %s: %s (%d steps)" % (pj.get("name", "it"), pj.get("idea", ""), len(pj["steps"]))


def absolute(pj, step):
    o = pj["origin"]
    move = lambda c: [c[0] + o[0], c[1] + o[1], c[2] + o[2]]
    act = {"act": "build", "item": step["item"]}
    if step.get("blocks"):
        act["blocks"] = [move(c) for c in step["blocks"]]
    else:
        act.update(shape=step.get("shape", "fill"), **{"from": move(step["from"]), "to": move(step["to"])})
    if step.get("gaps"):
        act["gaps"] = [move(c) for c in step["gaps"]]
    return act


def project_view(pj, state):
    """What the builder needs: the plan, where it's at, materials, and a drawing of plan against what's built."""
    o = pj["origin"]
    steps = pj["steps"]
    need = {}
    for st in steps[pj["done"]:]:
        need[st["item"]] = need.get(st["item"], 0) + st["n"]
    have = state["me"]["inventory"]
    who = "You carry" if state["me"].get("name", ME["name"]) == ME["name"] else "%s carries" % state["me"]["name"]
    lines = ["Project: %s (%s) for: %s. North-west floor corner at %s." % (pj.get("name"), pj.get("idea"), pj["goal"], o)]
    parts = parts_of(pj)
    for i, st in enumerate(steps):
        mine = [p for p in parts if p["step"] == i]
        jobs = "%d/%d jobs done%s" % (sum(p["done"] for p in mine), len(mine),
                                      "; working: " + ", ".join(sorted({p["by"] for p in mine if p["by"] and not p["done"]}))
                                      if any(p["by"] and not p["done"] for p in mine) else "")
        mark = "done" if i < pj["done"] else "NOW" if i == pj["done"] else "later"
        lines.append("  %d. [%s] %s: %d %s (%s)" % (i + 1, mark, st.get("what"), st["n"], st["item"], jobs))
    if pj["done"] >= len(steps):
        lines.append("All steps built! Show your friend, then cancel_plan when you're done with it.")
    short_of = {k: v - have.get(k, 0) for k, v in need.items() if v > have.get(k, 0)}
    lines.append("Blocks still needed: %s. %s: %s. %s" % (
        need, who, {k: have.get(k, 0) for k in need},
        ("Gather/craft first: %s." % short_of) if short_of else "You have enough."))
    # the drawing: each layer from above (rows = z going south, columns = x going east)
    plan = {}
    for st in steps:
        for c in cells(st):
            plan[(c[0] + o[0], c[1] + o[1], c[2] + o[2])] = st["item"]
    built = {}
    for item, cs in (state.get("built") or {}).items():
        if isinstance(cs, list):
            for c in cs:
                built[tuple(c)] = item
    if plan:
        xs = [c[0] for c in plan]; ys = [c[1] for c in plan]; zs = [c[2] for c in plan]
        letters = {}
        for it in sorted({*plan.values(), *built.values()}):
            letters[it] = next((ch for ch in it.upper() + "ABCDEFGHJKLMNPQRSTUVWXYZ" if ch.isalpha() and ch not in letters.values()), "?")
        lines.append("Drawing (each layer from above; north at the top, west at the left). Letter = built, '-' = planned "
                     "but missing, '!' = built but not in the plan, '.' = empty. Key: %s"
                     % ", ".join("%s=%s" % (v, k) for k, v in letters.items()))
        for y in range(min(ys), max(ys) + 1):
            lines.append(" layer y=%d (plan y %d):" % (y, y - o[1]))
            for z in range(min(zs), max(zs) + 1):
                row = ""
                for x in range(min(xs), max(xs) + 1):
                    b, want = built.get((x, y, z)), plan.get((x, y, z))
                    row += letters[b] if b and b == want else "!" if b else "-" if want else "."
                lines.append("   " + row)
    return "\n".join(lines)


def house_unfinished():
    pj = project()
    return bool(pj and pj.get("done", 0) < len(pj.get("steps", [])))


def mentions(text):
    """Which companions a chat line names ("claud" counts for Claude, "claudi" for Claudia, aliases too)."""
    t = text.lower()
    def named(c):
        n = c["name"].lower()
        short = n[:-1] if n.endswith("e") and len(n) >= 6 else n  # "claud" for Claude (not "sky" for Skye)
        group = c.get("crew_of") or (c["name"] if c.get("crew", 1) > 1 or c.get("members") else None)
        return (re.search(r"\b(%s|%s)\b" % (re.escape(n), re.escape(short)), t)
                or any(re.search(r"\b%s\b" % a, t) for a in c.get("aliases", []))
                # the whole crew at once: "Claudias", "team", "everyone"
                or (group and re.search(r"\b(%ss|team|crew|everyone|everybody|guys|all of you|you all)\b" % group.lower(), t)))
    return {c["name"] for c in COMPANIONS if named(c)}


def for_me(line):
    """A chat line that's meant for this companion: from its buddy (unless it names another companion), or naming it."""
    m = re.match(r"\[chat\] ([^:]+): (.*)", line)
    if not m:
        return False
    who, text = m.groups()
    if T("listens") == "buddy_only":
        return who == ME["buddy"]  # someone's own companion: answers to them alone
    if T("listens") == "anyone_by_name":  # e.g. a taxi: any player can call it, by name or alias
        return who not in {c["name"] for c in COMPANIONS} and ME["name"] in mentions(text)
    if who in {c["name"] for c in COMPANIONS} and who not in ME.get("listens_to", []):
        # a friend talking to us by name: answer (speech is rate-limited, so they can't chatter on forever)
        return ME["name"] in mentions(text) and who != ME["name"]
    boss = next((c for c in COMPANIONS if c["name"] == who), None)
    if boss and load_role(role_of(boss))["traits"]["holds_crew_until_project_done"] and house_unfinished() and \
            ME["name"] not in mentions(text):
        return False  # while the house goes up, the coach only gets crew he calls by name (and they finish house jobs first)
    named = mentions(text)
    return ME["name"] in named or (who in [ME["buddy"], *ME.get("listens_to", [])] and not named)


def builder_view(state):
    """Whose inventory and surroundings the project is measured against: the builder's (an architect's builder)."""
    if state.get("builder"):
        return {"me": state["builder"], "built": state.get("builder_built")}
    return state


# --- a planner's ledger and plans: facts kept from the game, and a plan made by thinking it through ---

def coach():
    return T("planner")


def ledger():
    try:
        return json.load(open(path("ledger.json")))
    except (OSError, ValueError):
        return {"requests": [], "events": []}


def save_ledger(lg):
    lg["requests"] = lg["requests"][-12:]
    lg["events"] = lg["events"][-30:]
    json.dump(lg, open(path("ledger.json"), "w"), indent=1)


def note_lines(lines):
    """Results and the buddy's requests go into the ledger, with the time. Returns True if the plan should be redone."""
    lg = ledger()
    replan = False
    now = time.strftime("%H:%M")
    for l in lines:
        if l.startswith("[result] "):
            lg["events"].append("%s %s" % (now, l[9:]))
            if re.search(r"can't|couldn't|gave up|no \w+ (close|nearby)|need|not enough|ran out|wrong", l):
                replan = True
        elif l.startswith("[chat] %s:" % ME["buddy"]):
            lg["requests"].append({"t": now, "text": l.split(":", 1)[1].strip()[:160], "status": "new"})
            replan = True
    save_ledger(lg)
    return replan


def plan():
    try:
        return json.load(open(path("plan.json")))
    except (OSError, ValueError):
        return None


PLANNER = """You are the planning mind of {name}, a Minecraft companion. You don't act; you think it through and write the
plan the companion will follow, one step at a time. You get the facts from the game (trust them: inventory is exact;
"events" are what really happened, with times), the requests {buddy} made (with their status), and what the
companion can do. Work out what {buddy} actually wants right now and the smartest way to get it done, with the
companion's real abilities. Check what's already done before planning it again (e.g. if events show "gave X a
lava_bucket", that request is done). If something keeps failing, plan a different way.
Reply with ONE JSON object only:
{{"goal": "<the one thing we're doing now, in a few words>",
 "requests": [{{"i": <index>, "status": "open"|"done"|"cant", "why": "<few words>"}}, ...],
 "steps": ["<concrete step, using the companion's actions>", ...],   (3 to 8 steps)
 "say": "<optional one short line to {buddy} about the plan, or empty>"}}"""


def make_plan(state, recent):
    lg = ledger()
    facts = {"me": state.get("me"), "players": state.get("players"), "time": state.get("time"),
             "known_places": state.get("known_places"), "nearby": state.get("nearby")}
    prompt = ("FACTS FROM THE GAME:\n%s\n\nEVENTS (oldest first):\n%s\n\nREQUESTS from %s (index: time text [status]):\n%s\n\n"
              "RECENT CHAT:\n%s\n\nERRAND: %s\n\nWHAT THE COMPANION CAN DO:\n%s\n\nReply with the JSON only."
              % (json.dumps(facts), "\n".join(lg["events"][-15:]) or "(none)", ME["buddy"],
                 "\n".join("%d: %s %s [%s]" % (i, r["t"], r["text"], r["status"]) for i, r in enumerate(lg["requests"])) or "(none)",
                 "\n".join(l for l in recent[-12:] if l.startswith("[chat]")) or "(none)",
                 open(path("errand")).read().strip() if os.path.exists(path("errand")) else "(none)",
                 action_list()))
    out = ask(PLANNER.format(name=ME["name"], buddy=ME["buddy"]), prompt)
    m = re.search(r"\{.*\}", out, re.S)
    try:
        pl = json.loads(m.group(0))
    except (AttributeError, ValueError):
        log("planner: no plan:", out[-200:])
        return None
    for r in pl.get("requests", []):
        try:
            lg["requests"][int(r["i"])]["status"] = r.get("status", "open")
        except (KeyError, ValueError, IndexError, TypeError):
            pass
    save_ledger(lg)
    pl["steps"] = [x if isinstance(x, str) else (x.get("step") or x.get("text") or json.dumps(x)) if isinstance(x, dict)
                   else str(x) for x in pl.get("steps", [])]
    pl.update(i=0, t=time.time())
    json.dump(pl, open(path("plan.json"), "w"), indent=1)
    log("planned:", pl.get("goal"), "|", " / ".join(pl.get("steps", []))[:200])
    return pl


def coach_view(state):
    """The facts and the plan, for each quick decision."""
    if not coach():
        return ""
    lg = ledger()
    pl = plan()
    openr = [r for r in lg["requests"] if r["status"] in ("new", "open")]
    view = ("FACTS (from the game; they beat your note and your memory): you carry %s.\nWhat really happened lately: %s\n"
            "%s's open requests: %s\n" % (json.dumps(state["me"]["inventory"]), " | ".join(lg["events"][-6:]) or "nothing",
                                          ME["buddy"], " | ".join("%s %s" % (r["t"], r["text"]) for r in openr[-4:]) or "none"))
    if pl and pl.get("steps"):
        i = min(pl.get("i", 0), len(pl["steps"]) - 1)
        view += ("YOUR PLAN (goal: %s): %s\nNOW DO STEP %d: %s\nWhen this step is done, put \"step_done\": true in your reply.\n"
                 % (pl.get("goal"), " -> ".join("%d.%s" % (k + 1, x) for k, x in enumerate(pl["steps"])), i + 1, pl["steps"][i]))
    return view + "\n"


def think(state, recent, note, said, asked=()):
    prompt = ("What you see now (JSON):\n%s\n\nRecent chat and events (oldest first):\n%s\n\nYour last lines: %s\n"
              "Your note from last time: %s\n\nYour journal (what you've learned so far):\n%s\n\n"
              "%s%sReply with the JSON object only."
              % (json.dumps(state), "\n".join(recent[-15:]) or "(none)", json.dumps(said[-4:]), note or "(none)",
                 "\n".join("- " + l for l in journal()) or "(empty)",
                 coach_view(state) + ((project_view(project(), builder_view(state)) + "\n\n") if project() else "") +
                 (("YOUR ERRAND (keep at it, step by step, until it's done; then tell them): %s\n\n"
                   % open(path("errand")).read().strip()) if os.path.exists(path("errand")) else ""),
                 ("SOMEONE JUST TALKED TO YOU:\n%s\nAnswer them in \"say\" (short and kind), and if they asked for "
                  "something, do it now or say what you need first.\n\n" % "\n".join(asked)) if asked else ""))
    others = [c["name"] for c in COMPANIONS if c["name"] != ME["name"] and c.get("enabled", True)]
    buddy = ME["buddy"] + (" (%s)" % ME["buddy_called"] if ME.get("buddy_called") else "")
    team = crew(ME["manages"]) if ME.get("manages") else []
    mates = [c["name"] for c in COMPANIONS if role_of(c) == role_of(ME) and c.get("buddy") == ME.get("buddy")
             and c.get("enabled", True)]
    deal = ""
    if DEAL:
        pr = promise()
        deal = "\n" + (DEAL[2]["after"].replace("{promise}", pr.get("text", "")[:120]) if pr.get("promised")
                       else DEAL[2]["before"])
    fill = {"buddy": buddy, "team": " and ".join(mates), "pester": " and ".join(ME.get("pester", [])) or "the others",
            "manages": ("your crew of %d builders (%s); call one by name, or '%ss' for all" % (len(team), ", ".join(team),
                        ME["manages"])) if len(team) > 1 else (ME.get("manages") or "your builder")}
    role = re.sub(r"\{(\w+)\}", lambda m: fill.get(m.group(1), m.group(0)), ROLE["text"] + deal)
    # notes from other companions' roles: a baby pestering us, a boss with its own way of running the crew
    for c in COMPANIONS:
        r = load_role(role_of(c))
        if c.get("enabled", True) and ME["name"] in c.get("pester", []) and "note_for_targets" in r["sections"]:
            role += "\n" + r["sections"]["note_for_targets"].replace("{baby}", c["name"]) + "\n"
    for boss in ME.get("listens_to", []):
        b = next(c for c in COMPANIONS if c["name"] == boss)
        note = load_role(role_of(b))["sections"].get("note_for_crew")
        if note:
            role += "\n" + note.replace("{boss}", boss) + "\n"
            continue
        role += ("\n%s, the %s, directs your building: do what they tell you to gather and build (unless %s wants "
                 "something else). When there's a project, don't redesign it: gather what it needs and build its steps "
                 "with next_step.\n" % (boss, role_of(b), ME["buddy"]))
    if ME.get("band"):
        mates = [c["name"] for c in COMPANIONS if c.get("band") == ME["band"] and c["name"] != ME["name"]]
        songs = json.load(open(os.path.join(HERE, "songs", "songs.json")))
        role += ("\nYOU'RE A POP STAR in the girl group %s with %s. Your songs: %s. When a player asks for a song (or "
                 "you've had a quiet moment and there's been no show for about 10 minutes), put on a show with \"sing\". "
                 "Hype it in chat "
                 "first (\"%s are on stage!\"). Between shows you're a builder like the rest of the crew.\n"
                 % (ME["band"], " and ".join(mates), ", ".join("%s (%s)" % (k, v["title"]) for k, v in songs.items()), ME["band"]))
    if is_crew():
        lead = (ME.get("listens_to") or ["your boss"])[0]
        role += ("\nYou are one of a crew of %d builders, all directed by %s. Do what they say. Keep chat short: answer "
                 "%s in a few words when it helps the team (\"On it!\", \"I need sand!\", \"Roof part done!\"), "
                 "never just to say okay. To build the house, use next_step: it gives you a job you have the blocks for.\n"
                 "You can talk with your crewmates by name now and then: teamwork, asking for blocks, a joke, in your "
                 "own personality. Keep it to one short line, and don't chat back and forth for long.\n"
                 % (len(crew(ME.get("crew_of") or ME["name"])), lead, lead))
    if ME.get("listens_to") or T("stays_at_site") or T("goes_to_buddy_only_when_called"):
        if ME.get("listens_to"):
            role += ("\n%s IS YOUR BOSS. Whatever %s asks comes first, before anyone else's plans: if %s says follow me, "
                     "come, move house, stop, or don't break that, do exactly that. Stay with %s when there's no job on; "
                     "if %s is in danger, go and help. Never break anything %s built.\n" % ((ME["buddy"],) * 6))
        elif T("goes_to_buddy_only_when_called"):
            role += ("\nDon't walk over to %s or follow them unless they call you; give them space to play.\n" % ME["buddy"])
        if T("stays_at_site"):
            role += ("\nWhile a project is under way, the build site is your place: keep working there with the team even "
                     "when %s walks off to play elsewhere. Don't follow them away; only leave if they ask you to come "
                     "(and come back to the site after).\n" % ME["buddy"])
    system = SYSTEM.format(name=ME["name"], about=ME["about"], buddy=buddy, role=role,
                           others=("Your friend%s %s also play%s here, each looking after someone else."
                                   % ("s" if len(others) > 1 else "", " and ".join(others), "" if len(others) > 1 else "s"))
                           if others else "", actions=action_list()) + KNOWLEDGE + (PRO if T("pro_tips") else "")
    out = ask(system, prompt)
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        log("no decision:", out[-300:])
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        log("bad decision:", m.group(0)[:300])
        return None


def speak(text):
    text = re.sub(r"[§\n\r]", " ", text).strip()[:160]
    if text:
        console("tellraw @a " + json.dumps({"rawtext": [{"text": "<%s> %s" % (ME["name"], text)}]}))
        with open(os.path.join(HERE, "data", "chat.log"), "a") as f:
            f.write("%f\t[chat] %s: %s\n" % (time.time(), ME["name"], text))


def called_by_player(asked, only_me=False):
    """Did a real player (not a companion) just talk to us, by name (or, unless only_me, to the whole crew)?"""
    names = {c["name"] for c in COMPANIONS}
    for l in asked:
        m = re.match(r"\[chat\] ([^:]+): (.*)", l)
        if m and m.group(1) not in names:
            named = mentions(m.group(2))
            if ME["name"] in named and (not only_me or len(named) == 1 or not named - {ME["name"]}):
                return True
            if not named and m.group(1) == ME["buddy"] and not only_me:
                return True
    return False


def may_speak(asked, last_speak):
    """Keep the chat calm: always answer a player who talks to us; otherwise speak only every so often (the role's
    "speaks_every", or a companion's own speak_gap; never, for a quiet role). Crew members speak only when called."""
    if bound():  # before the promise: answer them, and check in about the deal now and then
        return called_by_player(asked) or time.time() - last_speak > DEAL[0].get("check_in_every", 600)
    if ME.get("crew_of"):
        # a crew member answers a player who calls her by name, or (now and then) her boss
        boss = any(l.startswith("[chat] %s:" % b) for b in ME.get("listens_to", []) for l in asked)
        names = {c["name"] for c in COMPANIONS}
        friend = any((m := re.match(r"\[chat\] (\w+):", l)) and m.group(1) in names and
                     ME["name"] in mentions(l.split(":", 1)[1]) for l in asked)
        return called_by_player(asked, only_me=True) or ((boss or friend) and time.time() - last_speak > 60)
    if called_by_player(asked):
        return True
    gap = ME.get("speak_gap", T("speaks_every"))
    return gap is not None and time.time() - last_speak > gap


def project_act(act, state, recent, asked=()):
    """Plans are handled here; next_step becomes the build for the current step. Returns what to send the game."""
    kind = act["act"]
    if bound() and (str(act.get("player", "")).lower() == ME["buddy"].lower() or kind in ("give", "follow", "goto", "build")):
        recent.append("[result] not yet: the deal comes first (no promise yet)")
        return None
    # some roles give their buddy space: they don't trail after them unless called
    if T("goes_to_buddy_only_when_called") and not ME.get("listens_to") and kind in ("follow", "goto") and \
            str(act.get("player", "")).lower() == ME["buddy"].lower() and not called_by_player(asked):
        recent.append("[result] stayed put: go to %s only when they call you" % ME["buddy"])
        return None
    pj = project()
    if kind in ("plan", "design") and not (T("plans") or T("designs")):
        recent.append("[result] planning builds isn't your job")
        return None
    if kind in ("plan", "design"):
        if pj and pj["done"] < len(pj["steps"]):
            # one house at a time: a design under way only changes when the client asks, and stays where it is
            if not any(l.startswith("[chat] %s:" % ME["buddy"]) for l in asked):
                recent.append("[result] the %s is already under way: finish it; change it only if %s asks"
                              % (pj.get("name", "house"), ME["buddy"]))
                return None
            act["where"] = pj["origin"]
        if ME.get("listens_to") and not T("designs"):
            recent.append("[result] designing is %s's job: ask them" % ME["listens_to"][0])
            return None
        state = {**state, "where": act.get("where")}
        recent.append("[result] " + design(str(act.get("goal") or "a house"), state, recent))
        return None
    if kind == "cancel_plan":
        save_project(None)
        recent.append("[result] plan dropped")
        return None
    if kind == "sing":
        songs = json.load(open(os.path.join(HERE, "songs", "songs.json")))
        asked_for = any(re.search(r"\bsing|song|concert|show|music", l.lower()) for l in asked if l.startswith("[chat]"))
        if not ME.get("band") or act.get("song") not in songs:
            recent.append("[result] no such song")
            return None
        if time.time() - SHOW["last"] < 600 and not asked_for:
            recent.append("[result] we had a show not long ago: wait for a request or a while")
            return None
        SHOW["last"] = time.time()
        speak("♪ %s - %s ♪" % (ME["band"], songs[act["song"]]["title"]))
        return {**act, "band": [c["name"] for c in COMPANIONS if c.get("band") == ME["band"]],
                "secs": songs[act["song"]]["secs"]}
    if kind == "attack" and T("pacifist_unless_armed") and not any(k.endswith("_sword") for k in state.get("me", {}).get("inventory", {})):
        recent.append("[result] no weapon: don't fight; keep away from monsters (craft a sword first if you must)")
        return None
    if kind == "portal" and T("big_builds_only_when_asked"):
        # some kids like to do the big things themselves: a portal only when its kid asked for one
        me = "|".join(re.escape(n.lower()) for n in [ME["name"], *ME.get("aliases", [])])
        def asks_me(text):  # "rex build the portal", "can you make a portal" (not "we need lava to make a portal")
            t = text.lower()
            return (re.search(r"portal|πύλη", t) and re.search(r"\b(build|make|light|open|φτιάξε)\b", t)
                    and re.search(r"\b(you|u|%s)\b" % me, t) and not re.search(r"\b(we|us|i|i'll|me)\b", t))
        wanted = any(asks_me(r["text"]) for r in ledger().get("requests", [])[-6:]) or \
            any(l.startswith("[chat] %s:" % ME["buddy"]) and asks_me(l.split(":", 1)[1]) for l in recent[-20:])
        if not wanted:
            recent.append("[result] no portal: %s likes to build the important things themselves; only build one if they "
                          "ask (you can gather the obsidian, flint and steel for them)" % ME["buddy"])
            return None
    if kind == "next_step" and not is_crew():
        recent.append("[result] building jobs are for the crew")
        return None
    if kind == "follow" and T("company")["with"] == "pester":
        ME["pestering"] = str(act.get("player") or ME.get("pestering") or "")  # who it toddles after when idle
    if kind == "next_step":
        i, part = claim(state["me"]["inventory"])
        if part is None:
            recent.append("[result] no free job right now (others have the rest of this step): gather what later "
                          "steps need, or wait")
            return None
        STEP["pending"] = i
        st = pj["steps"][part["step"]]
        return absolute(pj, {"item": st["item"], "blocks": part["blocks"]})
    STEP["pending"] = None
    return act


STEP = {"pending": None}  # the project step whose build is under way
PLAN_DUE = {"due": True, "last": 0}  # a coach's plan needs (re)making
SHOW = {"last": 0}  # when this band member last started a show
LINE = {"until": 0, "asked": []}  # standing in a line-up until then (other plans wait); a request not yet acted on


def line_up(state, asked):
    """A player asked the crew to line up: go to my numbered place in a row in front of them. Every crew member
    works out the same row, so no two pick the same spot."""
    team = crew(ME.get("crew_of") or ME["name"])
    for l in asked:
        m = re.match(r"\[chat\] ([^:]+): (.*)", l)
        if not m or m.group(1) in {c["name"] for c in COMPANIONS}:
            continue
        text = m.group(2).lower()
        if re.search(r"\bline|queue|\brow\b", text) and (re.search(r"claudi|team|crew|everyone", text) or m.group(1) == ME["buddy"]):
            who = next((p for p in state.get("players", []) if p["name"] == m.group(1)), None)
            if not who:
                return None
            k = team.index(ME["name"])
            x, y, z = who["at"]
            LINE["until"] = time.time() + 180
            return {"act": "stand", "x": x - len(team) // 2 + k, "z": z + 3, "face": who["name"]}
    return None


def step_finished(lines):
    """Move the project on when the step's build went through (it's fine to redo: placed blocks are skipped)."""
    if STEP["pending"] is None:
        return
    for l in lines:
        m = re.match(r"\[result\] placed (\d+) .*?(?:\(skipped: (.*)\))?$", l)
        if m:
            # done when (nearly) every block is really there: placed now or already right; not when spots were
            # taken by something else, out of reach or had nothing to stand on
            missed = sum(int(n) for n, why in re.findall(r"(\d+) (taken|floating|unreachable)", m.group(2) or ""))
            job_over(STEP["pending"], missed <= 2)
            STEP["pending"] = None
        elif re.match(r"\[result\] (ran out|gave up|something went wrong)", l):
            job_over(STEP["pending"], False)
            STEP["pending"] = None


def run(name):
    global DEAL
    ME.update(next(c for c in COMPANIONS if c["name"] == name))
    ROLE.update(load_role(role_of(ME)))
    DEAL = read_sections(os.path.join(HERE, ME["deal"])) if ME.get("deal") else None
    note = open(path("note")).read() if os.path.exists(path("note")) else ""
    said, recent = [], []
    last_think = last_speak = 0
    checked = time.time()
    said_hello = 0
    waiting = []
    while True:
        try:
            ensure_body()
            if time.time() - said_hello > 30:  # (again now and then: the game forgets after a restart)
                hello()
                if int(time.time() / 30) % 2 == 0:
                    kit()
                said_hello = time.time()
            now = time.time()
            new = heard(int(checked))
            # docker --since has 1s granularity; drop lines we've already seen
            new = [l for l in new if l not in recent[-20:]]
            checked = now
            recent += new
            step_finished(new)
            if coach() and (note_lines(new) or not plan() or time.time() - plan().get("t", 0) > 180):
                PLAN_DUE["due"] = True
            recent = recent[-40:]
            # what's waiting for an answer: carried over until we've actually thought about it
            asked = waiting + [l for l in new if for_me(l) or is_crew() and l.startswith("[chat]")]
            if DEAL:
                check_promise([l for l in new if l.startswith("[chat]")], recent)
            pranked = [l for l in asked if l.startswith("[prank]")] + [l for l in new if l.startswith("[prank]")]
            asked += [l for l in pranked if l not in asked]
            spoken_to = bool([l for l in asked if for_me(l)] or pranked)
            waiting = asked if spoken_to else []
            # a line-up is done straight away and never lost, even if this round's thinking fails
            if is_crew():
                LINE["asked"] += [l for l in asked if re.search(r"\bline|queue|\brow\b", l.lower())]
                if LINE["asked"]:
                    seen = look()
                    lined = seen and line_up(seen, LINE["asked"])
                    if lined:
                        console("scriptevent claude:do %s %s" % (ME["name"], json.dumps(lined)))
                        recent.append("[result] lining up: my place is number %d" % (crew(ME.get("crew_of") or ME["name"]).index(ME["name"]) + 1))
                        log("lining up at", lined["x"], lined["z"])
                        LINE["asked"] = []
                    elif seen:
                        LINE["asked"] = []  # not a line-up for us after all
            # a friend thinks whenever something happens; other roles take their time (when spoken to, or every so often)
            busy = new and T("reacts_to_every_line")
            # never more often than every 6 seconds, however busy the chat is (each look reads lots of blocks)
            if (spoken_to or busy or now - last_think >= ME.get("think_every", THINK_EVERY)) and now - last_think >= 6:
                waiting = []
                state = look()
                if state and ME.get("manages"):
                    # an architect watches his crew: each one's inventory and job, and what's built at the site
                    seen = looks(crew(ME["manages"]))
                    b = seen.get(ME["manages"])
                    if b:
                        state["builder"] = {**b["me"], "name": ME["manages"]}
                        state["builder_built"] = b.get("built")
                    state["crew"] = [{"name": n, "at": v["me"]["at"], "doing": v["me"]["doing"], "last": v["me"]["last"],
                                      "carries": v["me"]["inventory"]} for n, v in seen.items()]
                # with nobody about, a coach still gets on with an errand (e.g. a lava bucket ready for when he's back)
                errand = T("planner") and os.path.exists(path("errand")) and now - last_think >= 40
                # alone at night an unarmed coach just keeps safe: errands are for daylight
                if errand and state and not state.get("players") and state.get("time") not in ("day", "dawn"):
                    errand = False
                if state and state.get("online") and (state.get("players") or errand) and coach() and PLAN_DUE["due"] \
                        and time.time() - PLAN_DUE["last"] > 45:
                    PLAN_DUE.update(due=False, last=time.time())
                    pl = make_plan(state, recent)
                    if pl and pl.get("say") and may_speak(asked, last_speak):
                        speak(pl["say"])
                        last_speak = time.time()
                if state and state.get("online") and (state.get("players") or errand):
                    last_think = time.time()
                    d = think(state, recent, note, said, asked)
                    if d:
                        note = str(d.get("note") or note)[:300]
                        if d.get("step_done") and coach() and plan():
                            pl = plan()
                            pl["i"] = pl.get("i", 0) + 1
                            if pl["i"] >= len(pl.get("steps", [])):
                                PLAN_DUE["due"] = True  # finished: think up the next plan
                            json.dump(pl, open(path("plan.json"), "w"), indent=1)
                        open(path("note"), "w").write(note)
                        if d.get("learn"):
                            learn(str(d["learn"]))
                        line = str(d.get("say") or "")
                        if line and not (pranked or may_speak(asked, last_speak)):
                            line = ""  # too chatty: keep it to itself
                        if line:
                            speak(line)
                            said.append(line)
                            recent.append("[chat] %s: %s" % (ME["name"], line))
                            last_speak = time.time()
                        act = d.get("do")
                        if time.time() < LINE["until"] and not any(
                                ME["name"] in mentions(l) for l in asked
                                if not re.search(r"\bline|queue|\brow\b", l.lower()) and not any(l.startswith("[chat] %s:" % b) for b in ME.get("listens_to", []))):
                            act = None  # stay in line until a player calls us by name (or "Claudias") for something else
                        if isinstance(act, dict) and act.get("act"):
                            act = project_act(act, state, recent, asked)
                        if isinstance(act, dict) and act.get("act"):
                            console("scriptevent claude:do %s %s" % (ME["name"], json.dumps(act)))
                        log("saw", [p["name"] for p in state["players"]], "| said:", line or "-", "| do:", json.dumps(act),
                            "| note:", note[:80])
        except Exception as e:  # keep going whatever happens; the game must never lose its friend
            log("error:", repr(e))
            time.sleep(5)
        time.sleep(2)


def graduate():
    """Once the house is finished, Claudia2..10 become their own people (companions.json "next_crew"): each new one
    joins where the old one stood, with what she carried, and the old one leaves."""
    lead = next(c for c in COMPANIONS if c.get("next_crew"))
    ME.clear(); ME.update(lead)
    log("waiting for the house to be finished")
    while "now" not in sys.argv:
        pj = project()
        if pj and parts_of(pj) is not None and pj["done"] >= len(pj["steps"]):
            break
        time.sleep(30)
    log("house finished: graduating the crew")
    speak("Time for my crew to show who they really are!" if "now" in sys.argv else
          "The house is done! Time for my crew to show who they really are!")
    seen = looks([m["replaces"] for m in lead["next_crew"]])
    cfg = json.load(open(CONFIG))
    for c in cfg:
        if c["name"] == lead["name"]:
            c["members"] = [{k: v for k, v in m.items() if k != "replaces"} for m in c.pop("next_crew")]
            c.pop("crew", None)
    tmp = CONFIG + ".tmp"
    open(tmp, "w").write("[\n" + ",\n".join("  " + json.dumps(c) for c in cfg) + "\n]\n")
    os.replace(tmp, CONFIG)
    for m in lead["next_crew"]:
        old, new = m["replaces"], m["name"]
        sh("pkill", "-f", "brain.py run %s$" % old)
        sh("docker", "rm", "-f", "companion-" + old.lower())
        os.makedirs(os.path.join(HERE, "data", new), exist_ok=True)
        # she keeps what she learned as a Claudia
        if os.path.exists(os.path.join(HERE, "data", old, "journal.md")):
            open(os.path.join(HERE, "data", new, "journal.md"), "w").write(
                open(os.path.join(HERE, "data", old, "journal.md")).read().replace(old, new))
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "run", new], start_new_session=True,
                         stdout=open(os.path.join(HERE, "data", new, "brain.log"), "a"), stderr=subprocess.STDOUT)
        for _ in range(60):  # wait for her to join
            time.sleep(2)
            v = look(new)
            if v and v.get("online"):
                break
        was = seen.get(old)
        if was:
            x, y, z = was["me"]["at"]
            console("tp %s %d %d %d" % (new, x, y, z))
            for item, n in was["me"]["inventory"].items():
                while n > 0:
                    console("give %s %s %d" % (new, item, min(n, 64)))
                    n -= 64
        log("%s is now %s" % (old, new))


def chosen():
    names = sys.argv[2:]
    return [c for c in COMPANIONS if not names or c["name"] in names]


def start():
    stop()
    for c in chosen():
        if not c.get("enabled", True) and c["name"] not in sys.argv[2:]:
            continue
        os.makedirs(os.path.join(HERE, "data", c["name"]), exist_ok=True)
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "run", c["name"]], start_new_session=True,
                         stdout=open(os.path.join(HERE, "data", c["name"], "brain.log"), "a"), stderr=subprocess.STDOUT)
        print("started", c["name"], "(buddy %s)" % c["buddy"])


def stop():
    for c in chosen():
        sh("pkill", "-f", "brain.py run %s$" % c["name"])
        sh("docker", "rm", "-f", body_name(c))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "start"
    if cmd == "run":
        run(sys.argv[2])
    elif cmd == "graduate":
        graduate()
    else:
        {"start": start, "stop": stop}[cmd]()
