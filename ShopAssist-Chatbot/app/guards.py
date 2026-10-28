"""Reality checks on the model's wording about branches (Stage 3.1).

agent.py already refuses invented ORDER ids. This does the same for BRANCHES:
a reply is rejected if it names a branch that does not exist, shows branch
details nobody looked up, or says a branch was set when none was.
Returns a short reason (used in the retry notice) or None when the reply is fine.
"""
import re

from app import db

_NAME_RE = re.compile(r"\b((?:[A-Z][A-Za-z']+ ){0,3}[A-Z][A-Za-z']+) Branch\b")
_STOP = {"Pickup", "The", "This", "Your", "Our", "That", "Right", "Correct", "Nearest",
         "Other", "Another", "Which", "Each", "Every", "Any", "Same", "New", "Great",
         "Welcome", "At", "In", "For"}
_SET_CLAIM_RE = re.compile(
    r"(?i)\b(set|locked|confirmed)\b[^.\n]{0,50}\b(pickup|branch)\b"
    r"|\b(pickup|branch)\b[^.\n]{0,50}\b(set|locked|confirmed)\b")


def _short(name: str) -> str:
    return name[:-7] if name.endswith(" Branch") else name


def mentioned_branches(reply: str, known: set[str]) -> tuple[set[str], set[str]]:
    """-> (known names mentioned, unknown names mentioned), names without 'Branch'."""
    found, unknown = set(), set()
    for m in _NAME_RE.finditer(reply):
        words = m.group(1).split()
        while words and words[0] in _STOP:
            words.pop(0)
        if not words:
            continue
        for k in range(len(words)):                    # does a known name end this phrase?
            if " ".join(words[k:]) in known:
                found.add(" ".join(words[k:]))
                break
        else:
            unknown.add(" ".join(words))
    return found, unknown


def bad_branch_claim(session, reply: str, tools_called: set[str],
                     tool_text: str = "") -> str | None:
    """tool_text: everything the tools returned this turn. A branch name that a tool
    result really contains (e.g. track_order: "... at Lekki Branch") is verified."""
    rows = db.get_conn().execute("SELECT name FROM branches").fetchall()
    known = {_short(r["name"]) for r in rows}
    found, unknown = mentioned_branches(reply, known)
    if unknown:
        return f"You named a branch that does not exist ({', '.join(sorted(unknown))})."
    allowed = set()
    for bid in (session.branch_id, session.proposed_branch_id):
        b = db.get_branch(bid) if bid else None
        if b:
            allowed.add(_short(b["name"]))
    verified = {n for n in found if f"{n} Branch" in tool_text}
    if (found - allowed - verified) and "set_pickup_location" not in tools_called:
        return "You mentioned branch details without calling set_pickup_location."
    if session.branch_id is None and _SET_CLAIM_RE.search(reply):
        return "You said a pickup branch was set or confirmed, but none is."
    return None
