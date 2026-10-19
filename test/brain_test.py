"""End to end on the throwaway test server: a stand-in player (PlayerOne) asks the default companion (Claude, from
companions.json) for help in chat; the real brain (Claude through the `claude` CLI) decides, and the companion
answers in chat and acts in the game.

  python3 test/brain_test.py

Needs Docker, bot/node_modules (bot/install.sh) and a logged-in `claude` CLI. Takes about 3 minutes.
"""
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "test"))
import bds_test as b  # noqa: E402

SAY = "Claude can you come to me please?|Claude please make me a crafting table"


def main():
    b.prepare()
    b.install_packs()
    b.start()
    b.command("gamerule domobspawning false")
    b.command("time set day")
    player = subprocess.Popen(
        ["docker", "run", "--rm", "--name", "test-player", "--platform", "linux/amd64", "--network", "container:" + b.NAME,
         "-v", os.path.join(HERE, "bot", "node_modules") + ":/bot/node_modules:ro",
         "-v", os.path.join(HERE, "test", "chat_player.js") + ":/bot/p.js:ro", "-e", "NAME=PlayerOne",
         "-e", "SAY=" + SAY, "-e", "WAIT=60", "-e", "GAP=45", "-w", "/bot", "node:20", "node", "p.js"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(15)
    b.command("give PlayerOne oak_log 4")
    shutil.rmtree(os.path.join(HERE, "data", "Claude"), ignore_errors=True)
    t0 = time.time()
    env = {**os.environ, "MC_SERVER": b.NAME, "COMPANIONS": "companions.json"}
    brain = subprocess.Popen([sys.executable, os.path.join(HERE, "brain.py"), "run", "Claude"], env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    time.sleep(170)
    brain.terminate()
    for name in ("companion-claude", "test-player"):
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    brain_log = brain.communicate(timeout=20)[0] or ""
    player_log = player.communicate(timeout=20)[0] or ""
    game = b.logs()
    subprocess.run(["docker", "rm", "-f", b.NAME], capture_output=True)

    # what Claude said, from the brain's chat log (the stand-in player doesn't receive server chat, so it can't tell us)
    said = []
    if os.path.exists(os.path.join(HERE, "data", "chat.log")):
        for line in open(os.path.join(HERE, "data", "chat.log")):
            t, _, text = line.partition("\t")
            if float(t) > t0 and text.startswith("[chat] Claude: "):
                said.append(text[len("[chat] Claude: "):].strip())
    results = re.findall(r'"who":"Claude","(?:result|error)":"([^"]*)"', game)
    errors = [l for l in game.splitlines() if re.search(r"\[Scripting\].*(Error|error)|has no runner|isn't in actions", l)]
    print("--- the player ---\n" + "\n".join(l for l in player_log.splitlines() if "said:" in l))
    print("--- Claude said ---\n" + "\n".join(said[-8:]))
    print("--- Claude's results in the game ---\n" + "\n".join(results[-10:]))
    print("--- brain log (tail) ---\n" + brain_log[-1500:])
    problems = []
    if not said:
        problems.append("Claude never spoke")
    if not any(re.search(r"arrived at PlayerOne|caught up|with PlayerOne", r) for r in results):
        problems.append("Claude never came over")
    if not any(re.search(r"made \d+ crafting_table|gave PlayerOne \d+ crafting_table", r) for r in results):
        problems.append("no crafting table made")
    if errors:
        problems.append("script errors: " + "; ".join(errors[:3]))
    print("\nRESULT:", "PASS" if not problems else "FAIL")
    for p in problems:
        print("  -", p)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
