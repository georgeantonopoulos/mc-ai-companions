"""Writes Claude_BP/scripts/actions.js from actions.json, so the game script and the brain share one list of
actions. Run it after editing actions.json (test/test_wiring.py fails if you forget).

  python3 tools/gen_actions.py
"""
import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "Claude_BP", "scripts", "actions.js")


def render():
    acts = {k: v for k, v in json.load(open(os.path.join(HERE, "actions.json"))).items() if not k.startswith("_")}
    meta = {k: {"runs_in": v["runs_in"], "secs": v.get("secs", 60)} for k, v in acts.items()}
    return ("// Generated from actions.json by tools/gen_actions.py: don't edit by hand.\n"
            "export const ACTIONS = " + json.dumps(meta, indent=2) + ";\n")


if __name__ == "__main__":
    open(OUT, "w").write(render())
    print("wrote", os.path.relpath(OUT, HERE))
