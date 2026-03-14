"""
Webhook signature validation — pure functions, no I/O.

GitHub: HMAC-SHA256 over raw payload body.
GitLab: plaintext token comparison (constant-time).
"""

import hashlib
import hmac


def validate_github_signature(
    payload_bytes: bytes,
    secret: str,
    signature_header: str,
) -> bool:
    """
    Validate GitHub HMAC-SHA256 webhook signature.
    signature_header format: "sha256=<hex_digest>"
    """
    if not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def validate_gitlab_token(token_header: str, secret: str) -> bool:
    """
    Validate GitLab webhook token (constant-time comparison).
    GitLab sends the secret as plaintext in X-Gitlab-Token.
    """
    return hmac.compare_digest(token_header.encode(), secret.encode())


def compute_pr_hash_key(
    vcs_provider: str,
    tenant_id: str,
    repo_id: str,
    pr_id: str,
    vcs_instance_id: str,
    action: str,
    commit_sha: str,
) -> str:
    """
    Compute the idempotency hash for a PR webhook event.
    Format: SHA256(vcs_provider:tenant_id:repo_id:pr_id:vcs_instance_id:action:commit_sha)
    """
    key = f"{vcs_provider}:{tenant_id}:{repo_id}:{pr_id}:{vcs_instance_id}:{action}:{commit_sha}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()
