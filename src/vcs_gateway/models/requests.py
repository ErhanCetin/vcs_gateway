"""
Webhook request payload models — Pydantic v2.

Used to parse and validate incoming webhook JSON bodies
from GitHub and GitLab before normalization.
"""

from typing import Any

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------


class GitHubPullRequest(BaseModel):
    number: int
    title: str
    user: dict[str, Any]  # {"login": "username"}
    html_url: str
    head: dict[str, Any]  # {"sha": "abc123"}
    base: dict[str, Any]


class GitHubRepository(BaseModel):
    id: int
    full_name: str  # "org/repo"
    html_url: str


class GitHubWebhookPayload(BaseModel):
    action: str
    pull_request: GitHubPullRequest
    repository: GitHubRepository


# ---------------------------------------------------------------------------
# GitLab
# ---------------------------------------------------------------------------


class GitLabObjectAttributes(BaseModel):
    iid: int  # MR number
    title: str
    url: str
    last_commit: dict[str, Any]  # {"id": "abc123"}
    action: str
    author_id: int


class GitLabProject(BaseModel):
    id: int
    path_with_namespace: str  # "group/repo"
    web_url: str


class GitLabWebhookPayload(BaseModel):
    object_kind: str  # "merge_request"
    object_attributes: GitLabObjectAttributes
    project: GitLabProject
    user: dict[str, Any]  # {"username": "...", "email": "..."}


# ---------------------------------------------------------------------------
# Normalized (VCS-agnostic)
# ---------------------------------------------------------------------------


class PullRequestData(BaseModel):
    """Normalized PR data extracted from any VCS webhook payload."""

    pr_id: str
    repo_id: str
    repo_name: str | None
    pr_title: str
    pr_author: str
    pr_url: str
    commit_sha: str
    action: str  # normalized: 'opened' | 'synchronize' | 'reopened'
    vcs_instance_id: str = "github.com"
