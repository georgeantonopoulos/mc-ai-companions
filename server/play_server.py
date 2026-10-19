"""The play server: a Bedrock Dedicated Server in Docker that phones, tablets and consoles on your Wi-Fi join.
It runs one survival world with the companion packs installed (Claude_BP, Claude_RP).

  python3 server/play_server.py start     # first time, or after a reboot
  python3 server/play_server.py update    # install the newest packs (the world is kept), restart
  python3 server/play_server.py status    # is it up, who's on, the address to type on the device
  python3 server/play_server.py errors    # script errors/warnings from the game since it started
  python3 server/play_server.py cmd say hi  # type any console command into the live game
  python3 server/play_server.py fresh     # replace the world with a brand-new one (old one kept)
  python3 server/play_server.py stop

Script changes reload without kicking anyone: copy Claude_BP into server/data/behavior_packs/ and run `cmd reload`.
The world lives in server/data.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(HERE, "server", "data")
NAME = "ai-companions-server"
PORT = 19132
WORLDS = {
    "survival": {
        "level": "Survival",
        "packs": [("behavior_packs", os.path.join(HERE, "Claude_BP")),
                  ("resource_packs", os.path.join(HERE, "Claude_RP"))],  # the taxi and the Pixel Pops' songs
        # companions join as players without a Microsoft account, which needs online-mode off: keep it on your LAN
        "props": {"server-name": "Survival with Claude", "difficulty": "easy",
                  "online-mode": "false", "texturepack-required": "true", "max-players": "24"},
    },
}
CURRENT = os.path.join(DATA, "current_world.txt")


def current():
    try:
        return open(CURRENT).read().strip() or "survival"
    except OSError:
        return "survival"


def props(w):
    return {**PROPS, "level-name": WORLDS[w]["level"], **WORLDS[w]["props"]}
# Locked to the version it was tested with: newer servers may drop the RakNet transport we rely on.
SERVER_VERSION = "1.26.52.3"

# Classic RakNet on one fixed UDP port: the newer "nethernet" transport uses random ports and LAN
# broadcast, neither of which gets through Docker's port forwarding.
PROPS = {
    "transport": "raknet",
    "server-port": str(PORT),
    "server-portv6": "19133",
    "allow-list": "false",
    "gamemode": "survival",
    "allow-cheats": "false",
    "content-log-file-enabled": "true",
    "content-log-console-output-enabled": "true",
    "max-players": "4",
}


def sh(*a, check=True):
    return subprocess.run(list(a), check=check, capture_output=True, text=True)


def running():
    return NAME in sh("docker", "ps", "--filter", "name=" + NAME, "--format", "{{.Names}}", check=False).stdout


def logs():
    r = sh("docker", "logs", NAME, check=False)
    return r.stdout + r.stderr


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def set_props():
    path = os.path.join(DATA, "server.properties")
    if not os.path.exists(path):
        return False
    txt = open(path).read()
    for k, v in props(current()).items():
        if re.search(r"^%s=" % re.escape(k), txt, re.M):
            txt = re.sub(r"^%s=.*$" % re.escape(k), "%s=%s" % (k, v), txt, flags=re.M)
        else:
            txt += "\n%s=%s\n" % (k, v)
    open(path, "w").write(txt)
    return True


def install_packs():
    w = WORLDS[current()]
    wdir = os.path.join(DATA, "worlds", w["level"])
    os.makedirs(wdir, exist_ok=True)
    out = {}
    lists = {"behavior_packs": [], "resource_packs": []}
    for kind, src in w["packs"]:
        dst = os.path.join(DATA, kind, os.path.basename(src))
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst)
        head = json.load(open(os.path.join(dst, "manifest.json")))["header"]
        lists[kind].append({"pack_id": head["uuid"], "version": head["version"]})
        out[os.path.basename(src)] = ".".join(map(str, head["version"]))
    for kind, packs in lists.items():
        json.dump(packs, open(os.path.join(wdir, "world_%s.json" % kind), "w"))
    return out


def run_container():
    sh("docker", "rm", "-f", NAME, check=False)
    # the image rewrites server.properties from these on every start, so they must match the world
    env = [a for k, v in props(current()).items() for a in ("-e", k.upper().replace("-", "_") + "=" + v)]
    sh("docker", "run", "-dit", "--name", NAME, "--restart", "unless-stopped", "--platform", "linux/amd64",
       "-e", "EULA=TRUE", "-e", "VERSION=" + SERVER_VERSION, *env,
       "-p", "%d:%d/udp" % (PORT, PORT), "-p", "19133:19133/udp",
       "-v", DATA + ":/data", "itzg/minecraft-bedrock-server")


def wait_started(t=240):
    for _ in range(t // 3):
        if "Server started" in logs():
            return True
        time.sleep(3)
    return False


def start():
    os.makedirs(DATA, exist_ok=True)
    first = not set_props()
    versions = install_packs()
    run_container()
    if not wait_started():
        raise SystemExit("server didn't start:\n" + logs()[-2000:])
    if first:  # the image writes server.properties on first start; set ours and restart once
        set_props()
        sh("docker", "restart", NAME)
        wait_started()
    time.sleep(3)
    world_rules()
    print("Play server up with", versions)
    status()


def update():
    versions = install_packs()
    set_props()
    run_container()  # made afresh: the image re-applies its settings from the container's, so a restart would undo ours
    if not wait_started():
        raise SystemExit("server didn't restart:\n" + logs()[-2000:])
    time.sleep(3)
    world_rules()
    print("Updated to", versions, "(world kept)")
    status()


def console(cmd):
    script = ('for p in /proc/[0-9]*; do case "$(tr "\\0" " " < $p/cmdline 2>/dev/null)" in '
              './bedrock_server-*) echo "$0" > $p/fd/0; exit 0;; esac; done; exit 2')
    sh("docker", "exec", NAME, "sh", "-c", script, cmd, check=False)


def world_rules():
    """Rules for the play world that the add-on doesn't set: friends can't hurt each other."""
    console("gamerule pvp false")


def fresh():
    """A brand-new world (the old one is kept as a backup folder), newest packs installed."""
    if "Players on now: nobody" not in _status_text():
        raise SystemExit("Someone is playing; not restarting.")
    sh("docker", "stop", NAME, check=False)
    wdir = os.path.join(DATA, "worlds", WORLDS[current()]["level"])
    if os.path.exists(wdir):
        backup = wdir + "-backup-" + time.strftime("%Y%m%d-%H%M")
        os.rename(wdir, backup)
        print("Old world kept at", backup)
    versions = install_packs()
    set_props()
    run_container()
    if not wait_started():
        raise SystemExit("server didn't start:\n" + logs()[-2000:])
    time.sleep(3)
    world_rules()
    print("Fresh world with", versions)
    status()


def switch():
    """Switch the server to another world; it's made fresh the first time."""
    w = sys.argv[2] if len(sys.argv) > 2 else ""
    if w not in WORLDS:
        raise SystemExit("Which world? " + " or ".join(WORLDS))
    if "Players on now: nobody" not in _status_text() and running():
        raise SystemExit("Someone is playing; not switching.")
    sh("docker", "stop", NAME, check=False)
    open(CURRENT, "w").write(w)
    versions = install_packs()
    set_props()
    run_container()
    if not wait_started():
        raise SystemExit("server didn't start:\n" + logs()[-2000:])
    time.sleep(3)
    world_rules()
    print("Now on the %s world with" % w, versions)
    status()


def _status_text():
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        status()
    return buf.getvalue()


def status():
    if not running():
        print("Play server is not running. Start it with: python3 server/play_server.py start")
        return
    L = logs()
    listen = re.findall(r"(IPv4 supported, port: \d+|Accepting clients on [^\n]+|listening[^\n]*)", L)
    joined = re.findall(r"Player connected: ([^,]+)", L)
    left = re.findall(r"Player disconnected: ([^,]+)", L)
    print("Running the %s world." % current(), "; ".join(listen[-2:]))
    print("On the device: Play > Servers > Add Server. Server Address: %s   Port: %d" % (lan_ip(), PORT))
    on = [p for p in joined if joined.count(p) > left.count(p)]
    print("Players on now:", ", ".join(sorted(set(on))) or "nobody")


def errors():
    L = logs()
    lines = [l for l in L.splitlines() if re.search(r"\[Scripting\]|ERROR", l)]
    print("\n".join(lines[-60:]) or "(no script errors or warnings)")


def run_cmd():
    """Type one console command into the live server and print what it answered."""
    since = time.time()
    console(" ".join(sys.argv[2:]))
    time.sleep(1.5)
    r = sh("docker", "logs", "--since", str(int(since)), NAME, check=False)
    print((r.stdout + r.stderr).strip() or "(no reply)")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"start": start, "update": update, "fresh": fresh, "status": status, "errors": errors, "cmd": run_cmd, "world": switch,
     "stop": lambda: (sh("docker", "stop", NAME, check=False), print("stopped"))}[cmd]()
