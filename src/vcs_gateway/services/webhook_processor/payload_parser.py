"""Step 4 — Provider-specific payload parsing and normalization."""

from vcs_gateway.models.requests import (
    GitHubWebhookPayload,
    GitLabWebhookPayload,
    PullRequestData,
)


def parse_payload(vcs_provider: str, raw_payload: bytes) -> PullRequestData:
    """Parse and normalize a raw webhook body into VCS-agnostic PullRequestData."""
    if vcs_provider == "github":
        payload = GitHubWebhookPayload.model_validate_json(raw_payload)
        return PullRequestData(
            pr_id=str(payload.pull_request.number),
            repo_id=str(payload.repository.id),
            repo_name=payload.repository.full_name,
            pr_title=payload.pull_request.title,
            pr_author=str(payload.pull_request.user.get("login", "")),
            pr_url=payload.pull_request.html_url,
            commit_sha=str(payload.pull_request.head.get("sha", "")),
            action=payload.action,
            vcs_instance_id="github.com",
        )
    if vcs_provider == "gitlab":
        payload = GitLabWebhookPayload.model_validate_json(raw_payload)
        return PullRequestData(
            pr_id=str(payload.object_attributes.iid),
            repo_id=str(payload.project.id),
            repo_name=payload.project.path_with_namespace,
            pr_title=payload.object_attributes.title,
            pr_author=str(payload.user.get("username", "")),
            pr_url=payload.object_attributes.url,
            commit_sha=str(payload.object_attributes.last_commit.get("id", "")),
            action=payload.object_attributes.action,
            vcs_instance_id="gitlab.com",
        )
    msg = f"Unsupported vcs_provider: {vcs_provider}"
    raise ValueError(msg)
