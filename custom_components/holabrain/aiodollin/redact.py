"""Making identifiers safe to print.

Debug logging in this client exists so that a cloud failure can be reported, and reports
get pasted into public issue trackers. The appliance id is the one identifier the request
path carries by construction — it is the last segment of every device endpoint — so it has
to be handled where the message is formatted, not by asking people to scrub their own logs.

Home Assistant's diagnostics use the same pseudonyms, so an issue that carries both a log
excerpt and a diagnostics dump can still be read as one story.
"""

from __future__ import annotations

import hashlib
import re

# Appliance ids ("thing codes") are long runs of digits — 15 in every payload seen so far.
# Twelve is the threshold rather than fifteen so a differently sized id is still caught,
# while the short numbers that are genuinely part of an endpoint are left alone.
_IDENTIFIER = re.compile(r"\d{12,}")


def pseudonym(thing_code: str) -> str:
    """Return a stable, non-reversible stand-in for an appliance id.

    Hashing rather than numbering keeps the same appliance recognisable across two reports
    from the same user, which is what makes an "it broke again after the update" report
    useful.
    """
    return f"appliance-{hashlib.sha256(thing_code.encode()).hexdigest()[:8]}"


def redact_path(path: str) -> str:
    """Return a request path with any appliance id replaced by its pseudonym."""
    return _IDENTIFIER.sub(lambda match: pseudonym(match.group()), path)
