"""Step 2 — Event type filtering against whitelist."""

from vcs_gateway.models.domain import VcsEventWhitelist


def is_event_allowed(
    event_type: str,
    action: str,
    whitelist: list[VcsEventWhitelist],
) -> bool:
    """Return True if (event_type, action) pair is in the whitelist."""
    return any(
        w.event_type == event_type and w.event_action == action
        for w in whitelist
    )


def extract_event_type(headers: dict[str, str]) -> str:
    return headers.get("X-GitHub-Event") or headers.get("X-Gitlab-Event") or ""
