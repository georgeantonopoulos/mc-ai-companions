"""Guard tests: everything declared in one place is wired up in the others, without a server or Claude.

  python3 test/test_wiring.py

- every game action in actions.json has a runner in main.js, and every runner is in actions.json
- Claude_BP/scripts/actions.js is up to date with actions.json (tools/gen_actions.py)
- every brain action in actions.json is handled in brain.py
- the craft list in actions.json matches the recipes in main.js
- every role file parses, uses only traits the brain knows, and offers only actions that exist
- every trait the brain knows is used somewhere, and the brain checks no role by name
- every companion in companions.json and examples/*.json has a role, a skin and (if any) a deal that exist,
  and its instructions can be built
"""
import glob
import importlib
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
MAIN = open(os.path.join(HERE, "Claude_BP", "scripts", "main.js")).read()
BRAIN = open(os.path.join(HERE, "brain.py")).read()
ACTIONS = {k: v for k, v in json.load(open(os.path.join(HERE, "actions.json"))).items() if not k.startswith("_")}
CONFIGS = [os.path.join(HERE, "companions.json")] + sorted(glob.glob(os.path.join(HERE, "examples", "*.json")))
if os.path.exists(os.path.join(HERE, "local.json")):  # your own companions too
    _mine = json.load(open(os.path.join(HERE, "local.json"))).get("companions")
    if _mine:
        CONFIGS.append(os.path.join(HERE, _mine))


def runners():
    body = MAIN[MAIN.index("const RUNNERS = {"):]
    body = body[:body.index("\n};\n")]
    return set(re.findall(r"^  (\w+)\(p, t\) \{", body, re.M))


def brain(config):
    """A fresh brain module reading the given companions file."""
    os.environ["COMPANIONS"] = os.path.relpath(config, HERE)  # wins over local.json
    sys.argv = ["brain.py"]
    import brain as b
    return importlib.reload(b)


class Actions(unittest.TestCase):
    def test_game_actions_have_runners(self):
        game = {k for k, v in ACTIONS.items() if v["runs_in"] == "game"}
        self.assertEqual(sorted(game - runners()), [], "in actions.json but no runner in main.js")
        self.assertEqual(sorted(runners() - game), [], "runner in main.js but not a game action in actions.json")

    def test_generated_file_is_current(self):
        import gen_actions
        current = open(gen_actions.OUT).read()
        self.assertEqual(current, gen_actions.render(), "actions.js is stale: run python3 tools/gen_actions.py")

    def test_brain_and_script_actions_are_handled(self):
        for k, v in ACTIONS.items():
            if v["runs_in"] == "brain":
                self.assertRegex(BRAIN, r'kind (==|in \([^)]*)\W*"%s"' % k, "brain action %s isn't handled in brain.py" % k)
            if v["runs_in"] == "script":
                self.assertIn('t.act === "%s"' % k, MAIN, "script action %s isn't handled in main.js" % k)

    def test_every_action_is_described(self):
        for k, v in ACTIONS.items():
            self.assertIn(v["runs_in"], ("game", "brain", "script"), k)
            self.assertIn(v["offer"], ("all", "role", "companion", "internal"), k)
            if v["offer"] == "companion":
                self.assertTrue(v.get("when"), "%s: offer 'companion' needs 'when' (the companion setting that unlocks it)" % k)
            self.assertTrue(v["doc"], k)
            if v["offer"] != "internal":
                self.assertIn('"act":"%s"' % k, v["use"].replace("\\", ""), "%s: 'use' should show the call" % k)

    def test_craft_list_matches_recipes(self):
        body = MAIN[MAIN.index("const R = {"):]
        body = body[:body.index("\n};\n")]
        recipes = set(re.findall(r"^  (\w+):", body, re.M))
        self.assertEqual(set(ACTIONS["craft"]["items"]), recipes, "craft items in actions.json vs recipes in main.js")


class Roles(unittest.TestCase):
    def setUp(self):
        self.b = brain(CONFIGS[0])

    def test_roles_use_known_traits_and_actions(self):
        for f in glob.glob(os.path.join(HERE, "roles", "*.md")):
            head, _, _ = self.b.read_sections(f)
            for t in head.get("traits", {}):
                self.assertIn(t, self.b.TRAITS, "%s: unknown trait %s" % (os.path.basename(f), t))
            for a in head.get("extra_actions", []):
                self.assertEqual(ACTIONS.get(a, {}).get("offer"), "role",
                                 "%s: extra action %s must be an actions.json entry with offer 'role'" % (f, a))
            self.assertTrue(head.get("about"), "%s: say what the role is in 'about'" % f)

    def test_every_trait_is_used(self):
        code = BRAIN[BRAIN.index("def T(trait)"):]
        for t in self.b.TRAITS:
            self.assertRegex(code, r'T\("%s"\)|"%s"\]' % (t, t), "trait %s is declared but never read" % t)

    def test_no_role_checked_by_name(self):
        roles = [os.path.basename(f)[:-3] for f in glob.glob(os.path.join(HERE, "roles", "*.md"))]
        for r in roles:
            self.assertNotRegex(BRAIN, r'role"?\)?\)? *(==|!=|in) *\(?[^\n]*"%s"' % r,
                                "brain.py checks the %s role by name: give it a trait instead" % r)


class Companions(unittest.TestCase):
    def test_companions_are_complete_and_their_instructions_build(self):
        for config in CONFIGS:
            b = brain(config)
            b.ask = lambda system, prompt: (_ for _ in ()).throw(Captured(system))
            b.console = lambda cmd: None
            for c in b.COMPANIONS:
                where = "%s in %s" % (c["name"], os.path.basename(config))
                self.assertTrue(os.path.exists(os.path.join(HERE, "roles", b.role_of(c) + ".md")), where + ": no such role")
                self.assertTrue(os.path.exists(os.path.join(HERE, "skins", c["skin"] + ".b64")), where + ": no skin")
                for t in c.get("traits", {}):
                    self.assertIn(t, b.TRAITS, where + ": unknown trait " + t)
                if c.get("deal"):
                    _, _, sections = b.read_sections(os.path.join(HERE, c["deal"]))
                    for s in ("before", "after", "check"):
                        self.assertIn(s, sections, where + ": the deal needs a '%s' section" % s)
                b.ME.clear(); b.ME.update(c)
                b.ROLE.clear(); b.ROLE.update(b.load_role(b.role_of(c)))
                b.DEAL = b.read_sections(os.path.join(HERE, c["deal"])) if c.get("deal") else None
                state = {"me": {"inventory": {}, "at": [0, 64, 0], "hp": 20}, "players": [], "online": True}
                try:
                    b.think(state, [], "", [])
                    self.fail(where + ": instructions weren't built")
                except Captured as got:
                    system = got.args[0]
                self.assertNotIn("{{", system, where + ": doubled braces reach Claude")
                if b.T("company")["with"] == "buddy" and not b.T("goes_to_buddy_only_when_called"):
                    self.assertNotIn("Don't walk over to", system, where + ": told to keep away from the buddy it stays with")
                left = re.findall(r"(?<!\{)\{[a-z_]+\}(?!\})", system)
                self.assertEqual(left, [], where + ": unfilled placeholders in the instructions")
                for k, a in ACTIONS.items():
                    offered = a["offer"] == "all" or (a["offer"] == "role" and k in b.ROLE["extra_actions"]) or \
                        (a["offer"] == "companion" and bool(c.get(a["when"])))
                    self.assertEqual('"act":"%s"' % k in system, offered, where + ": action %s offered wrongly" % k)


class Captured(Exception):
    pass


if __name__ == "__main__":
    unittest.main(verbosity=1)
