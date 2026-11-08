"""Checks on the persona, skill and tier-table content that project-setup installs."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PERSONAS = ROOT / "personas"
SKILL = ROOT / "skills" / "orca-cotal-lanes" / "SKILL.md"
TIERS = ROOT / "templates" / "lanes.tsv"
BRIEF = ROOT / "templates" / "lane-brief.md"
FORBIDDEN = ["rove api", "terminal send", "orchestration dispatch", "task-create", "--inject"]


def frontmatter(path):
    text = path.read_text()
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    assert m, f"{path} has no frontmatter"
    keys = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            keys[k.strip()] = v.strip()
    return keys, m.group(2)


class PersonaTests(unittest.TestCase):
    def test_coordinator_frontmatter(self):
        keys, body = frontmatter(PERSONAS / "coordinator.md")
        for k in ("name", "role", "description", "agent", "model", "capabilities"):
            self.assertIn(k, keys)
        self.assertEqual(keys["name"], "coordinator")
        self.assertEqual(keys["role"], "coordinator")
        self.assertIn("spawn", keys["capabilities"])
        self.assertIn("role:lane", keys["capabilities"])
        self.assertIn("orca-cotal-lanes", body)

    def test_design_lead_frontmatter(self):
        keys, body = frontmatter(PERSONAS / "design-lead.md")
        self.assertEqual(keys["name"], "design-lead")
        self.assertEqual(keys["role"], "design")
        self.assertIn("spawn", keys["capabilities"])
        self.assertIn("role:lane", keys["capabilities"])
        self.assertIn("orca-cotal-lanes", body)

    def test_reviewer_frontmatter_and_rules(self):
        keys, body = frontmatter(PERSONAS / "reviewer.md")
        self.assertEqual(keys["name"], "reviewer")
        self.assertEqual(keys["role"], "reviewer")
        self.assertNotIn("spawn", keys.get("capabilities", ""))  # a reviewer starts no agents
        for needle in ("read-only", "blocker", "major", "minor", "cotal_dm", "never edit", "exercised"):
            self.assertIn(needle, body.lower())
        for ph in ("{{HARNESS}}", "{{MODEL}}", "{{LAUNCH}}", "{{PROJECT}}"):
            self.assertIn(ph, (PERSONAS / "reviewer.md").read_text())

    def test_leads_point_at_the_reviewer(self):
        for name in ("coordinator.md", "design-lead.md"):
            self.assertIn("{{REVIEWER}}", (PERSONAS / name).read_text(), name)

    def test_personas_carry_placeholders(self):
        for name in ("coordinator.md", "design-lead.md"):
            text = (PERSONAS / name).read_text()
            for ph in ("{{HARNESS}}", "{{MODEL}}", "{{LAUNCH}}", "{{PROJECT}}", "{{MODE}}", "{{TIERS}}"):
                self.assertIn(ph, text, f"{name} lacks {ph}")


class SkillTests(unittest.TestCase):
    def test_skill_frontmatter_and_mechanics(self):
        keys, body = frontmatter(SKILL)
        self.assertEqual(keys["name"], "orca-cotal-lanes")
        self.assertIn("description", keys)
        for needle in ("--parent-worktree active", "cotal_spawn", "lanes.tsv", "cotal_dm", "persona", "role:lane",
                       "--workspace-status"):
            self.assertIn(needle, body)


class TierTableTests(unittest.TestCase):
    def test_default_tiers(self):
        rows = [l.split("\t") for l in TIERS.read_text().splitlines() if l and not l.startswith("#")]
        self.assertEqual(rows[0], ["tier", "harness", "model", "permission"])
        table = {r[0]: r[1:] for r in rows[1:]}
        self.assertEqual(table["swift"], ["opencode", "opencode/deepseek-v4.1-flash", "-"])
        self.assertEqual(table["standard"], ["claude", "sonnet", "inherit"])
        self.assertEqual(table["deep"], ["claude", "opus", "inherit"])
        self.assertEqual(table["top"], ["none", "-", "-"])


class NoTypingIntoAgentsTests(unittest.TestCase):
    def test_no_forbidden_verbs(self):
        files = list(PERSONAS.glob("*.md")) + [SKILL, BRIEF]
        for f in files:
            text = f.read_text()
            for bad in FORBIDDEN:
                # The skill may name a forbidden verb only in a "never" sentence.
                for line in text.splitlines():
                    if bad in line:
                        self.assertRegex(line.lower(), r"never|do not|don't", f"{f.name}: '{bad}' outside a prohibition")



class BypassOptionTests(unittest.TestCase):
    def test_lane_skill_offers_bypass_in_the_mode_cycle(self):
        text = (ROOT / "skills" / "orca-cotal-lanes" / "SKILL.md").read_text()
        self.assertEqual(text.count('"allow-dangerously-skip-permissions": ""'), 2)


class LaneSkillCliTests(unittest.TestCase):
    def setUp(self):
        self.text = SKILL.read_text()
        self.flat = " ".join(self.text.split())  # line wraps do not matter

    def test_names_the_task_after_creating_it(self):
        self.assertIn('worktree set --worktree path:<worktree> --display-name "<KEY> <short title>" --json', self.text)

    def test_uses_the_real_orca_cli_in_project_setup_order(self):
        t = self.text
        line = next(l for l in t.splitlines() if l.startswith("for c in "))
        order = [line.index(x) for x in ('"$ORCA_CLI"', ".orca/cli/bin/orca", "orca-ide", "linux-orca-cli-shim",
                                          "command -v orca)")]
        self.assertEqual(order, sorted(order))
        self.assertIn("in double quotes", t)
        for l in t.splitlines():
            if l.lstrip().startswith("orca "):
                self.fail(f"bare orca command: {l}")
            for m in re.finditer(r"`orca ([a-z]+ [a-z]+)", l):
                self.assertEqual(m.group(1), "terminal send", f"bare orca in backticks: {l}")

    def test_finds_the_setup_tab_without_trusting_the_title(self):
        t = self.flat
        for needle in ('`paneTitle` is "Setup"', "setup-runner.sh", "`agentIdentity`", "exactly one line", "If unsure, leave a tab open"):
            self.assertIn(needle, t)

    def test_resolver_does_not_rely_on_a_shell_variable(self):
        self.assertIn("new shell", self.text)
        self.assertNotIn("ORCA=${", self.text)

    def test_closes_shell_and_setup_tab_only_after_the_agent_started(self):
        t = self.flat
        i_wait, i_spawn, i_close, i_owner = (t.index(x) for x in (
            "1. If the worktree's", "start the agent with the `cotal_spawn`", "3. Only then close the plain shell", "4. Give the owner a shell"))
        self.assertTrue(i_wait < i_spawn < i_close < i_owner)
        for needle in ("an empty worktree may refuse the spawn and strand the agent's name", "Close the Setup tab only if the marker",
                       "If the marker never appeared, keep the Setup tab open", "With no marker in `orca.yaml`"):
            self.assertIn(needle, t)

    def test_closes_only_handles_noted_before_the_spawn(self):
        t = self.flat
        self.assertLess(t.index("note the handle of every terminal"), t.index("start the agent with the `cotal_spawn`"))
        self.assertIn("only among the handles you noted in step 2", t)

    def test_stops_when_no_real_orca_cli_is_found(self):
        self.assertIn("If it prints nothing, or only plain `orca` was found", self.flat)
        self.assertIn("stop and tell the owner", self.flat)

    def test_points_to_the_tier_table_instead_of_copying_it(self):
        self.assertIn("tier table file your persona names", self.text)
        self.assertNotIn("opencode/deepseek", self.text)
        for word in ("`none`", "`inherit`"):
            self.assertIn(word, self.text)


if __name__ == "__main__":
    unittest.main()
