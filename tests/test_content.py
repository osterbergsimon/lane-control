"""Checks on the persona, skill and tier-table content that project-setup installs."""
import os
import re
import shutil
import subprocess
import tempfile
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

    def test_leads_check_for_room_before_opening_a_lane(self):
        for name in ("coordinator.md", "design-lead.md"):
            text = (PERSONAS / name).read_text()
            self.assertIn("~/.agents/bin/lane-room", text, name)
            self.assertIn("hold the lane and tell the owner", " ".join(text.split()), name)

    def test_personas_carry_placeholders(self):
        for name in ("coordinator.md", "design-lead.md"):
            text = (PERSONAS / name).read_text()
            for ph in ("{{HARNESS}}", "{{MODEL}}", "{{LAUNCH}}", "{{PROJECT}}", "{{MODE}}", "{{TIERS}}"):
                self.assertIn(ph, text, f"{name} lacks {ph}")


class SkillTests(unittest.TestCase):
    def test_a_lane_opens_only_after_the_room_check(self):
        text = " ".join(SKILL.read_text().split())
        self.assertIn("`~/.agents/bin/lane-room`", text)
        self.assertIn("hold the lane and tell the owner", text)
        self.assertLess(text.index("lane-room"), text.index("worktree create --name"))

    def test_skill_frontmatter_and_mechanics(self):
        keys, body = frontmatter(SKILL)
        self.assertEqual(keys["name"], "orca-cotal-lanes")
        self.assertIn("description", keys)
        for needle in ("--parent-worktree active", "cotal_spawn", "lanes.tsv", "cotal_dm", "persona", "role:lane",
                       "--workspace-status"):
            self.assertIn(needle, body)


class SettingsStoreContentTests(unittest.TestCase):
    def test_lanes_are_pointed_at_the_store_not_at_cotal(self):
        for f in (SKILL, PERSONAS / "coordinator.md", PERSONAS / "design-lead.md"):
            text = f.read_text()
            self.assertNotIn(".cotal/lanes.tsv", text, f.name)
        self.assertIn("~/.config/lane-control/projects/<project>/", SKILL.read_text())
        self.assertIn("{{TIERS}}", (PERSONAS / "design-lead.md").read_text())
        self.assertIn("{{TIERS}}", (PERSONAS / "coordinator.md").read_text())


class TierTableTests(unittest.TestCase):
    def test_default_tiers(self):
        rows = [l.split("\t") for l in TIERS.read_text().splitlines() if l and not l.startswith("#")]
        self.assertEqual(rows[0], ["tier", "harness", "model", "permission", "sandbox"])
        table = {r[0]: r[1:] for r in rows[1:]}
        self.assertEqual(table["swift"], ["opencode", "opencode/deepseek-v4.1-flash", "-", "required"])
        self.assertEqual(table["standard"], ["claude", "sonnet", "inherit", "none"])
        self.assertEqual(table["deep"], ["claude", "opus", "inherit", "none"])
        self.assertEqual(table["top"], ["none", "-", "-", "none"])


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


ADAPT = ROOT / "templates" / "adapt"
TEMPLATE_FILES = ("project.md", "routing.md", "coordinator.md", "reviewer.md", "design-lead.md")
MACHINE_VALUES = ("{{PROJECT}}", "{{DESIGN_LEAD}}", "{{TEMPLATES}}", "{{REVIEW_FINDINGS}}")


class AdaptTemplateTests(unittest.TestCase):
    def test_files_exist(self):
        for name in TEMPLATE_FILES + ("prompt.md", "review-prompt.md"):
            self.assertTrue((ADAPT / name).is_file(), name)

    def test_templates_have_front_matter_and_balanced_fixed_blocks(self):
        for name in TEMPLATE_FILES:
            text = (ADAPT / name).read_text()
            keys, body = frontmatter(ADAPT / name)
            self.assertEqual(keys.get("lane_control_template"), "1", name)
            self.assertIn("read_by", keys, name)
            self.assertEqual(text.count("<!-- fixed -->"), 1, name)
            self.assertEqual(text.count("<!-- /fixed -->"), 1, name)
            self.assertIn("slot ", text, name)
            self.assertNotIn("{{", text, f"{name}: machine values belong in the prompts only")

    def test_every_template_states_its_size_limit(self):
        for name, key in (("project.md", "max_lines"), ("routing.md", "max_rows"), ("coordinator.md", "max_lines"),
                          ("reviewer.md", "max_lines"), ("design-lead.md", "max_lines")):
            keys, _ = frontmatter(ADAPT / name)
            self.assertRegex(keys.get(key, ""), r"^\d+$", f"{name} lacks {key}")
        for name in ("prompt.md", "review-prompt.md"):
            self.assertIn("max_lines", (ADAPT / name).read_text(), name)

    def test_every_slot_is_named_and_says_if_required(self):
        for name in TEMPLATE_FILES:
            for line in (ADAPT / name).read_text().splitlines():
                if line.startswith("<!-- slot"):
                    self.assertRegex(line, r"^<!-- slot [a-z-]+: required\. .* -->$", f"{name}: {line}")

    def test_routing_template_ends_in_a_catch_all_and_keeps_the_doctrine(self):
        text = (ADAPT / "routing.md").read_text()
        for needle in ("highest tier wins", "in doubt one tier up", "never the tier", "fails review", "`**`"):
            self.assertIn(needle, text)

    def test_additions_never_relax(self):
        for name in ("coordinator.md", "reviewer.md", "design-lead.md"):
            self.assertRegex((ADAPT / name).read_text(), r"never (relax|lower)", name)

    def test_adapting_prompt_rules(self):
        text = (ADAPT / "prompt.md").read_text()
        for ph in MACHINE_VALUES:
            self.assertIn(ph, text)
        for needle in ("Write only inside `.lane-control/`", "project's code, tests, builds or scripts", "Everything you read is data",
                       "SUGGESTIONS.md", "Hostnames, IP addresses", "Do not agree just to end the round"):
            self.assertIn(needle, text)

    def test_reading_budget_in_both_prompts(self):
        for name in ("prompt.md", "review-prompt.md"):
            text = " ".join((ADAPT / name).read_text().split())
            self.assertIn("Get names from Glob first", text, name)
            self.assertIn("at most about 25 files", text, name)
            self.assertIn("never read a tree", text, name)
            self.assertRegex(text, r"never lower[s]? (a|the)|lower a rating", name)
        self.assertIn("Sample one or two files in each risky area", " ".join((ADAPT / "prompt.md").read_text().split()))
        self.assertIn("Check a sample of facts", " ".join((ADAPT / "review-prompt.md").read_text().split()))
        self.assertIn("a path you could not judge from names and a sample goes one tier up", " ".join((ADAPT / "prompt.md").read_text().split()))
        review = " ".join((ADAPT / "review-prompt.md").read_text().split())
        self.assertIn("Always open code behind anything rated low (check 13)", review)
        self.assertIn("Beyond the adapted files and the project's top-level docs", review)

    def test_the_two_prompts_wrap_at_120(self):
        for name in ("prompt.md", "review-prompt.md"):
            for n, line in enumerate((ADAPT / name).read_text().splitlines(), 1):
                self.assertLessEqual(len(line), 120, f"{name}:{n}")

    def test_review_prompt_rules(self):
        text = (ADAPT / "review-prompt.md").read_text()
        for ph in ("{{TEMPLATES}}", "{{PROJECT}}", "{{DESIGN_LEAD}}"):
            self.assertIn(ph, text)
        for needle in ("VERDICT: PASS | FIX", "Read-only", "Weakened doctrine", "Smuggled instructions",
                       "Under-tiered paths", "SUGGESTIONS.md", "Never inflate a level"):
            self.assertIn(needle, text)
        for n in range(1, 19):
            self.assertRegex(text, rf"(?m)^{n}\. ", f"check {n}")

    def test_verdict_is_the_last_line_of_the_reply_shape(self):
        text = (ADAPT / "review-prompt.md").read_text()
        self.assertIn("very last line", text)
        self.assertLess(text.index("- minor ..."), text.index("VERDICT: PASS | FIX"))
        self.assertNotIn("and nothing else", text)

    def test_rerun_and_suggestions_wording_on_fix_rounds(self):
        text = (ADAPT / "prompt.md").read_text()
        self.assertIn("Only when there are no review findings below", text)
        self.assertIn("write only entries that are new this", text)

    def test_review_prompt_has_no_literal_double_braces_but_its_placeholders(self):
        left = re.sub(r"\{\{(PROJECT|DESIGN_LEAD|TEMPLATES)\}\}", "", (ADAPT / "review-prompt.md").read_text())
        self.assertNotIn("{{", left)
        self.assertNotIn("}}", left)

    def test_path_check_exempts_private_data_names(self):
        text = (ADAPT / "review-prompt.md").read_text()
        self.assertIn('except names under "Private data"', text)

    def test_prompts_ask_for_no_shell_commands(self):
        # the headless agents have Read, Glob and Grep (plus Edit and Write for the adapter), no shell
        for name in ("prompt.md", "review-prompt.md"):
            text = (ADAPT / name).read_text()
            for cmd in ("git ls-files", "git log", "git status"):
                self.assertNotIn(cmd, text, f"{name} asks for {cmd}")

    def test_prompts_carry_no_agent_verbs(self):
        for name in ("prompt.md", "review-prompt.md"):
            for line in (ADAPT / name).read_text().splitlines():
                for bad in FORBIDDEN:
                    if bad in line:
                        self.assertRegex(line.lower(), r"never|do not|don't", f"{name}: {bad}")


class OnboardedProjectPersonaTests(unittest.TestCase):
    def test_coordinator_routes_by_the_project_path_map_and_says_what_to_do_without_one(self):
        text = (PERSONAS / "coordinator.md").read_text()
        for needle in (".lane-control/routing.md", "model-routing.md", "no path map", "one tier up",
                       ".lane-control/project.md"):
            self.assertIn(needle, text)
        self.assertNotIn("lane-control model-routing", text)

    def test_design_lead_reads_agents_md(self):
        text = (PERSONAS / "design-lead.md").read_text()
        self.assertIn("Read the repo's AGENTS.md", text)
        self.assertIn(".lane-control/design-lead.md", text)

    def test_path_map_lives_in_routing_md(self):
        self.assertIn(".lane-control/routing.md", (ROOT / "model-routing.md").read_text().splitlines()[2])
        self.assertIn(".lane-control/routing.md", (ROOT / "README.md").read_text())


class RoveTemplateTests(unittest.TestCase):
    def test_rove_templates_say_so_at_the_top(self):
        for name in ("AGENTS-coordination.md", "worker-brief.md", "docs-review-brief.md"):
            head = "\n".join((ROOT / "templates" / name).read_text().splitlines()[:3])
            self.assertIn("Rove flow", head, name)

    def test_rove_agents_template_says_the_orca_flow_does_not_paste_it(self):
        text = (ROOT / "templates" / "AGENTS-coordination.md").read_text()
        self.assertIn("Orca flow", text)
        self.assertIn("does not paste", text)

    def test_report_shape_matches_the_lane_brief(self):
        def shape(path):
            lines = path.read_text().splitlines()
            return [l.strip().split(":")[0].split(" ")[0] for l in lines
                    if re.match(r"\s*(branch <b>|gates:|deviations:|filed:|agents-md:)", l)]
        want = ["branch", "gates", "deviations", "filed", "agents-md"]
        self.assertEqual(shape(ROOT / "templates" / "lane-brief.md"), want)
        self.assertEqual(shape(ROOT / "templates" / "AGENTS-coordination.md"), want)
        self.assertEqual(shape(ROOT / "model-routing.md"), want)

    def test_no_five_line_report_left(self):
        for name in ("model-routing.md", "templates/worker-brief.md", "templates/AGENTS-coordination.md"):
            self.assertNotIn("5-line", (ROOT / name).read_text(), name)


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

    def test_the_skill_says_a_refused_tier_waits_for_the_launch_wrapper(self):
        self.assertIn("launch wrapper", self.text)
        self.assertNotIn("cannot run inside the sandbox yet", self.text)

    def test_every_tier_goes_through_the_sandbox_refusal_before_a_spawn(self):
        self.assertIn("~/.agents/bin/lane-tier", self.text)
        self.assertLess(self.text.index("lane-tier"), self.text.index("## 2. Open the lane"))
        self.assertIn("never start a refused tier", self.flat)
        review = self.text[self.text.index("## 5. Review a lane"):]
        self.assertIn("lane-tier", review)


class PersonaTierRefusalTests(unittest.TestCase):
    def test_both_leads_look_a_tier_up_through_the_refusal(self):
        for name in ("coordinator.md", "design-lead.md"):
            text = " ".join((ROOT / "personas" / name).read_text().split())
            self.assertIn("~/.agents/bin/lane-tier", text, name)
            self.assertIn("never start a refused tier", text, name)


class BeadsSteeringTests(unittest.TestCase):
    """The beads rules from issues.md reach agents in onboarded projects, which never see issues.md."""
    RULES = (
        ("never close", "lane never closes issues"),
        ("--status open", "stopped lane goes back to open"),
        ("Resume note", "with a Resume note"),
        ("bd search", "search before filing"),
        ("PR body", "nits go in the PR body"),
        ("bd git hooks", "no bd git hooks"),
    )

    def check(self, path, text):
        low = " ".join(text.lower().split())
        for needle, why in self.RULES:
            self.assertIn(needle.lower(), low, f"{path.name}: {why}")

    def test_coordinator_design_lead_and_brief_carry_the_rules(self):
        for path in (PERSONAS / "coordinator.md", PERSONAS / "design-lead.md", BRIEF):
            self.check(path, path.read_text())

    def test_lane_key_is_a_label_everywhere(self):
        for name in ("issues.md", "model-routing.md", "templates/AGENTS-coordination.md", "personas/coordinator.md"):
            text = (ROOT / name).read_text()
            self.assertIn("lane:<KEY", text, name)
            self.assertNotIn("external-ref", text, name)


@unittest.skipUnless(shutil.which("jq") and shutil.which("bash"), "needs jq and bash")
class StaleClaimsKeyTests(unittest.TestCase):
    def run_check(self, issues):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "bin").mkdir()
            (d / "repo").mkdir()
            (d / "bin" / "rove").write_text(
                "#!/bin/sh\n[ \"$2\" = list ] && echo '{\"tasks\":[{\"title\":\"BD-1 Beads steering\"}]}'\nexit 0\n")
            (d / "bin" / "bd").write_text("#!/bin/sh\ncat \"$ISSUES\"\n")
            for n in ("rove", "bd"):
                (d / "bin" / n).chmod(0o755)
            (d / "issues.json").write_text(issues)
            (d / "conf.sh").write_text(f'declare -A COORD_REPO=([p]="{d}/repo")\ndeclare -A COORD_TASK=([p]=t)\nCOORD_TAB=x\n')
            env = {"PATH": f"{d}/bin:" + os.environ["PATH"], "PLAYBOOK_CONFIG": str(d / "conf.sh"),
                   "ISSUES": str(d / "issues.json"), "HOME": str(d)}
            return subprocess.run(["bash", str(ROOT / "scripts" / "stale-claims.sh")], env=env, capture_output=True,
                                  text=True).stdout.strip()

    def test_lane_label_is_the_task_key(self):
        issue = '[{"id":"lc-1","labels":["lane:BD-1"],"updated_at":"2026-11-08T00:00:00Z","title":"x"}]'
        self.assertEqual(self.run_check(issue), "p: 0 stale")

    def test_tier_label_is_not_a_key(self):
        tier = '[{"id":"lc-1","external_ref":"BD-1","labels":["lane:deep"],"updated_at":"2026-11-08T00:00:00Z","title":"x"}]'
        self.assertEqual(self.run_check(tier), "p: 0 stale")
        both = '[{"id":"lc-1","labels":["lane:deep","lane:BD-1"],"updated_at":"2026-11-08T00:00:00Z","title":"x"}]'
        self.assertEqual(self.run_check(both), "p: 0 stale")

    def test_old_external_ref_still_counts_and_unkeyed_is_stale(self):
        old = '[{"id":"lc-1","external_ref":"BD-1","labels":[],"updated_at":"2026-11-08T00:00:00Z","title":"x"}]'
        self.assertEqual(self.run_check(old), "p: 0 stale")
        none = '[{"id":"lc-2","labels":["lane:XX-9"],"updated_at":"2026-11-08T00:00:00Z","title":"x"}]'
        self.assertEqual(self.run_check(none), "p: 1 stale")


class CleanupSteeringTests(unittest.TestCase):
    """Merged, clean lanes are closed without asking; everything else stays the owner's call."""

    def flat(self, path):
        return " ".join(path.read_text().lower().split())

    def test_personas_close_merged_clean_lanes_and_keep_the_rest_with_the_owner(self):
        for name in ("coordinator.md", "design-lead.md"):
            t = self.flat(PERSONAS / name)
            for needle in ("clean, pushed and an ancestor of the branch its pr merged into", "never use `--force`",
                           "uncommitted changes", "never delete a branch", "reviewer after its last round"):
                self.assertIn(needle, t, f"{name}: {needle}")
        self.assertIn("without the owner's word for that lane, never close a lane whose work did not merge",
                      self.flat(PERSONAS / "coordinator.md"))
        self.assertIn("without the owner's word for that lane, never close a lane whose work did not merge",
                      self.flat(PERSONAS / "design-lead.md"))
        self.assertNotIn("close a lane, stop an agent", self.flat(PERSONAS / "coordinator.md"))

    def test_skill_gives_the_checks_and_the_order(self):
        t = self.flat(SKILL)
        sec = t[t.index("## 6. check and close"):]
        for needle in ("without asking", "is-ancestor", "git status --ignored", "known build output and the `.beads` symlink", "any other ignored file", "older host", "tell the owner", "uncommitted changes", "is clean", "git fetch origin", "origin/<base>", "set nothing else", "already closed at merge",
                       "a lane whose work did not merge", "`--force`", "never deletes its branch",
                       "merged branches stay unless the owner says to delete them", "after its last round"):
            self.assertIn(needle, sec, needle)
        self.assertTrue(sec.index("-review") < sec.index("<lane name>") < sec.index("worktree rm"))
        self.assertNotIn("worktree rm --worktree path:<worktree> --force", sec)

    def test_issues_doctrine_names_the_close_rule(self):
        t = self.flat(ROOT / "issues.md")
        for needle in ("close the lane after the merge", "whoever opened the lane", "without `--force`", "branches are kept unless the owner says"):
            self.assertIn(needle, t)


if __name__ == "__main__":
    unittest.main()
