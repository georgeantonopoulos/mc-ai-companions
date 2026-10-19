"""Run test/drive_test.js against the isolated test server (with Claude_BP loaded) and print the server-side position
of the test player every second, so we can see which kind of movement the server accepts."""
import json, os, re, subprocess, sys, time
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "test"))
import bds_test as b

def main():
    b.prepare()
    w = os.path.join(b.DATA, "worlds", b.WORLD)
    bp = os.path.join(b.DATA, "behavior_packs", "Claude_BP")
    subprocess.run(["rsync", "-a", "--delete", os.path.join(HERE, "Claude_BP") + "/", bp + "/"], check=True)
    head = json.load(open(os.path.join(bp, "manifest.json")))["header"]
    json.dump([{"pack_id": head["uuid"], "version": head["version"]}], open(os.path.join(w, "world_behavior_packs.json"), "w"))
    json.dump([], open(os.path.join(w, "world_resource_packs.json"), "w"))
    b.start()
    js = sys.argv[1] if len(sys.argv) > 1 else "drive_test.js"
    bot = subprocess.Popen(["docker", "run", "--rm", "--platform", "linux/amd64", "--network", "container:" + b.NAME,
                            "-v", os.path.join(HERE, "bot", "node_modules") + ":/bot/node_modules:ro",
                            "-v", os.path.join(HERE, "test", js) + ":/bot/t.js:ro", "-w", "/bot", "node:20", "node", "t.js"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    t0 = time.time()
    while bot.poll() is None and time.time() - t0 < 90:
        b.command("scriptevent claude:debug Walker")
        time.sleep(1)
        L = b.logs()
        m = re.findall(r'"who":"Walker","debug".*?"at":(\{[^}]*\})', L)
        c = re.findall(r'"controls":(\{[^}]*\}[^}]*\})', L)
        if m:
            a = json.loads(m[-1])
            print("%5.1fs server sees Walker at x=%.2f y=%.2f z=%.2f  controls %s" % (time.time() - t0, a["x"], a["y"], a["z"],
                  c[-1] if c else "?"), flush=True)
    print(bot.communicate()[0][-2500:])
    subprocess.run(["docker", "rm", "-f", b.NAME], capture_output=True)

main()
