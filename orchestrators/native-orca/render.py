"""Native-Orca values for the persona placeholders whose project-setup text is Cotal-shaped.

project-setup's render_persona fills {{DESIGN_ROUTE}} and {{ATTACHED}} with Orca + Cotal text (a Cotal role name, the
orca-cotal-lanes skill). For a native-Orca persona, call prepare() first; render_persona then fills the rest.
"""

DESIGN_ROUTE = ("The project's design lead (session `{{PROJECT}} design lead`, role `design-lead`) owns frontend and\n"
                "visual quality; route UI work and UI reviews through it.")
NO_DESIGN_ROUTE = "This project has no design lead: you route any UI work yourself, and it gets the code reviewer only."
ATTACHED_RULE = ("- This project also has attached repos, which you open lanes in: {paths}. Each keeps its own beads\n"
                 "  workspace and default branch; start a lane there with `worker-start --worktree new-top-level --repo\n"
                 "  path:<attached repo>`.\n")


def prepare(text: str, design_lead: bool = True, attached: tuple = ()) -> str:
    """The persona text with the native values in; the placeholders left are the ones render_persona fills."""
    rule = ATTACHED_RULE.format(paths=", ".join(f"`{a}`" for a in attached)) if attached else ""
    return (text.replace("{{ATTACHED}}\n", rule)
            .replace("{{DESIGN_ROUTE}}", DESIGN_ROUTE if design_lead else NO_DESIGN_ROUTE))
