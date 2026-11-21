"""Native-Orca values for the persona placeholders whose project-setup text is Cotal-shaped.

project-setup's render_persona fills {{ATTACHED}} with Orca + Cotal text (the orca-cotal-lanes skill). For a
native-Orca persona, call prepare() first; render_persona then fills the rest.
"""

ATTACHED_RULE = ("- This project also has attached repos, which you open lanes in: {paths}. Each keeps its own beads\n"
                 "  workspace and default branch; start a lane there with `worker-start --worktree new-top-level --repo\n"
                 "  path:<attached repo>`.\n")


def prepare(text: str, attached: tuple = ()) -> str:
    """The persona text with the native values in; the placeholders left are the ones render_persona fills."""
    rule = ATTACHED_RULE.format(paths=", ".join(f"`{a}`" for a in attached)) if attached else ""
    return text.replace("{{ATTACHED}}\n", rule)
