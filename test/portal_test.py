"""On the isolated test server: a companion body ("Tester") builds a Nether portal, lights it, walks through, and
we check it arrives in the Nether and can still move. Also tries the chest and bow skills."""
import json, os, re, subprocess, sys, time
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "test"))
import bds_test as b

def last(pattern, since_text=""):
    m = re.findall(pattern, b.logs())
    return m[-1] if m else None

b.prepare()
w = os.path.join(b.DATA, "worlds", b.WORLD)
for kind, src in (("behavior_packs", "Claude_BP"), ("resource_packs", "Claude_RP")):
    dst = os.path.join(b.DATA, kind, src)
    subprocess.run(["rsync", "-a", "--delete", os.path.join(HERE, src) + "/", dst + "/"], check=True)
    h = json.load(open(os.path.join(dst, "manifest.json")))["header"]
    json.dump([{"pack_id": h["uuid"], "version": h["version"]}], open(os.path.join(w, "world_%s.json" % kind), "w"))
b.start()
body = subprocess.Popen(["docker", "run", "--rm", "--name", "tester-body", "--platform", "linux/amd64", "--network", "container:" + b.NAME,
    "-v", os.path.join(HERE, "bot", "node_modules") + ":/bot/node_modules:ro",
    "-v", os.path.join(HERE, "body.js") + ":/bot/body.js:ro", "-v", os.path.join(HERE, "skins") + ":/bot/skins:ro",
    "-e", "NAME=Tester", "-e", "SKIN=skins/rex.b64", "-w", "/bot", "node:20", "node", "body.js"],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
time.sleep(30)
for c in ["scriptevent claude:hello Tester - 0", "tag Tester add drive", "gamerule domobspawning false", "time set day",
          "give Tester obsidian 10", "give Tester flint_and_steel 1", "give Tester chest 1", "give Tester dirt 20",
          "execute as Tester at @s run fill ~-3 ~ ~1 ~3 ~4 ~3 air", "give Tester bow 1", "give Tester arrow 8"]:
    b.command(c); time.sleep(0.5)
time.sleep(2)
def do(act):
    b.command("scriptevent claude:do Tester " + json.dumps(act))
def results():
    return re.findall(r'"who":"Tester","(?:result|error)":"([^"]*)"', b.logs())
do({"act": "store", "items": ["dirt"]}); time.sleep(4)
do({"act": "take", "item": "dirt", "count": 5}); time.sleep(4)
# away from the chest (companions keep clear of anything built), onto open ground
for c in ["execute as Tester at @s run tp @s ~8 ~ ~", "execute as Tester at @s run fill ~-3 ~ ~1 ~3 ~4 ~3 air",
          "execute as Tester at @s run fill ~-3 ~-1 ~1 ~3 ~-1 ~3 stone"]:
    b.command(c); time.sleep(1)
do({"act": "portal"}); time.sleep(16)
print("results so far:", results()[-6:])
do({"act": "enter_portal"}); time.sleep(25)
b.command("scriptevent claude:debug Tester"); time.sleep(2)
dbg = last(r'"who":"Tester","debug".*')
print("results:", results()[-4:])
print("after the portal:", (dbg or "")[:300])
before = last(r'"at":\{[^}]*\}')
do({"act": "wander"}); time.sleep(6)
b.command("scriptevent claude:debug Tester"); time.sleep(1); print("during wander:", (last(r'"who":"Tester","debug".*') or "")[:600]); time.sleep(6)
b.command("scriptevent claude:debug Tester"); time.sleep(2)
print("moving in the Nether? before", before, "after", last(r'"who":"Tester","debug".*?"at":(\{[^}]*\})'))
subprocess.run(["docker", "rm", "-f", "tester-body"], capture_output=True)
print("--- body log ---"); print((body.communicate(timeout=20)[0] or "")[-1500:])
subprocess.run(["docker", "rm", "-f", b.NAME], capture_output=True)
