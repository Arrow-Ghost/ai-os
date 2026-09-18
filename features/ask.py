"""
ask.question -- the tool that makes "ask instead of guess" real.

Every other tool here does something. This one does nothing except stop and
ask you, then hand the answer back to the brain as the next observation. It
is READ tier and never gated -- approving a question would be absurd -- but
it genuinely blocks execution until you answer, the same way the approval
prompt does for a DANGER tool.

Without this, the only way the agent could reduce ambiguity was to guess (bad)
or finish the run and make you re-prompt it with more detail (clunky).
notify.send is one-way: it can tell you something, but cannot collect an
answer. This is the missing half.
"""

from __future__ import annotations

from servant.sdk import Tier, ToolError, tool


@tool(
    name="ask.question",
    tier=Tier.READ,
    params={"question": "What to ask. Be specific about what answer you need."},
)
def ask_question(ctx, question: str) -> str:
    """Stop and ask the human a question, then continue with their answer.

    Use this when an instruction is ambiguous, when a choice has real
    consequences and you are not confident which one is meant, or when you
    need a piece of information only the human has. Prefer this over guessing.
    """
    if not question.strip():
        raise ToolError("question is empty")

    answer = ctx.ask_open(question)
    if answer is None:
        raise ToolError(
            "nobody was there to answer (no interactive terminal). Proceed "
            "conservatively, or finish and explain what you needed to know."
        )
    ctx.log(f"asked: {question[:80]} -> answered")
    return f"the human answered: {answer}"
