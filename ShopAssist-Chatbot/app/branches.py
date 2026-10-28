"""Code-owned branch selection (Stage 3.1).

Everything the customer reads about branches is written HERE from the database,
never by the model, and the branch is confirmed here, never by the model.
  none_text / list_text / proposal_text  - what the customer is shown
  confirm_proposal / reject_proposal     - what a plain YES / NO does
Why: in a live chat the model invented a 'Victoria Island Branch', invented
addresses, claimed a branch was set when it was not, and once confirmed a branch
on the customer's behalf.
"""
from app import db


def none_text(place: str) -> str:
    return (f"Sorry, I couldn't find a branch for '{place}'. We currently serve: "
            f"{', '.join(db.served_cities())}. Which city or area works for you?")


def list_text(matches) -> str:
    lines = ["Which branch would you like for pickup?"]
    lines += [f"{i}. *{b['name']}* — {b['address']}" for i, b in enumerate(matches, 1)]
    lines.append("Reply with the branch name.")
    return "\n".join(lines)


def proposal_text(branch) -> str:
    return (f"Your pickup branch would be *{branch['name']}* — {branch['address']}.\n"
            "Is that right? Reply *YES* or *NO*.")


def confirm_proposal(session) -> str:
    """The customer said YES to the proposed branch. Returns the opening line the
    customer sees; the model then carries on with what they originally asked."""
    branch = db.get_branch(session.proposed_branch_id) if session.proposed_branch_id else None
    if not branch or not branch["active"]:
        session.propose_branch(None)
        return "Sorry, that branch isn't available any more. Which city or area would you like for pickup?"
    had_quote = bool(session.pending_quote)
    session.lock_branch(branch["branch_id"])          # also voids a quote for the old branch
    session.branch_just_confirmed = True              # transient: tells the model to carry on
    text = f"Pickup branch confirmed: *{branch['name']}* — {branch['address']}."
    if had_quote:
        text += " Your earlier quote was for another branch, so it has been cancelled."
    return text


def reject_proposal(session) -> str:
    """The customer said NO to the proposed branch."""
    session.propose_branch(None)
    current = db.get_branch(session.branch_id) if session.branch_id else None
    if current:
        return f"Okay, I'll keep *{current['name']}* as your pickup branch."
    return "Okay. Which city or area would you like for pickup?"
