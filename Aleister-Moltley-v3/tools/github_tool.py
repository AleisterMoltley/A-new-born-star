"""GitHub Tool — Aleister can create repos, push code, manage issues.

Direct GitHub API integration via PyGithub. No git CLI needed.
Aleister can materialize his ideas as real repositories.

Tools:
  github_create_repo  — Create a new repository
  github_push_file    — Create/update a file in a repo
  github_create_issue — Open an issue
  github_list_repos   — List your repositories
  github_read_file    — Read a file from a repo

Env: GITHUB_TOKEN (personal access token with repo scope)
     GITHUB_USERNAME (default: extracted from token)
"""

from __future__ import annotations

import base64
import os
from typing import Optional

from tool_registry import BaseTool, ToolResult, ToolContext
import logging

logger = logging.getLogger(__name__)


class _GitHubClient:
    """Lazy-loaded GitHub API client."""

    def __init__(self):
        self._gh = None
        self._user = None

    def _init(self) -> bool:
        if self._gh is not None:
            return True
        token = os.getenv("GITHUB_TOKEN", "")
        if not token:
            return False
        try:
            from github import Github, Auth
            self._gh = Github(auth=Auth.Token(token))
            self._user = self._gh.get_user()
            logger.info("GitHub connected: %s", self._user.login)
            return True
        except ImportError:
            logger.warning("PyGithub not installed: pip install PyGithub")
            return False
        except Exception as e:
            logger.warning("GitHub init failed: %s", e)
            return False

    @property
    def gh(self):
        self._init()
        return self._gh

    @property
    def user(self):
        self._init()
        return self._user

    @property
    def username(self) -> str:
        if self._init() and self._user:
            return self._user.login
        return os.getenv("GITHUB_USERNAME", "")


_client: Optional[_GitHubClient] = None

def _get_gh() -> _GitHubClient:
    global _client
    if _client is None:
        _client = _GitHubClient()
    return _client


# ── Tools ──

class GitHubCreateRepoTool(BaseTool):
    category = "github"
    name = "github_create_repo"
    description = (
        "Create a new GitHub repository. Can be public or private. "
        "Optionally initialize with a README. Use this to materialize ideas as real code projects."
    )
    is_read_only = False

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Repository name (e.g. 'my-project')."},
                "description": {"type": "string", "description": "Short description."},
                "private": {"type": "boolean", "description": "Private repo (default: false)."},
                "init_readme": {"type": "boolean", "description": "Initialize with README (default: true)."},
            },
            "required": ["name"],
        }

    def needs_confirmation(self, params, config):
        return True  # Creating a repo is significant

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        gh = _get_gh()
        if not gh.gh:
            return ToolResult(error="GitHub not configured. Set GITHUB_TOKEN env var.", is_error=True)

        name = params.get("name", "")
        desc = params.get("description", "")
        private = params.get("private", False)
        init_readme = params.get("init_readme", True)

        if not name:
            return ToolResult(error="Repository name required", is_error=True)

        try:
            repo = gh.user.create_repo(
                name=name,
                description=desc,
                private=private,
                auto_init=init_readme,
            )
            return ToolResult(output=(
                f"Repository created: {repo.html_url}\n"
                f"Clone: git clone {repo.clone_url}\n"
                f"Private: {private}"
            ))
        except Exception as e:
            return ToolResult(error=f"Failed to create repo: {e}", is_error=True)


class GitHubPushFileTool(BaseTool):
    category = "github"
    name = "github_push_file"
    description = (
        "Create or update a file in a GitHub repository. "
        "Provide the repo name, file path, content, and commit message. "
        "Use this to push code, documentation, or any file to a repo."
    )
    is_read_only = False

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository name (e.g. 'my-project'). Assumes your username."},
                "path": {"type": "string", "description": "File path in repo (e.g. 'src/main.py')."},
                "content": {"type": "string", "description": "File content."},
                "message": {"type": "string", "description": "Commit message."},
                "branch": {"type": "string", "description": "Branch (default: main)."},
            },
            "required": ["repo", "path", "content", "message"],
        }

    def needs_confirmation(self, params, config):
        return False  # Pushing code is the point

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        gh = _get_gh()
        if not gh.gh:
            return ToolResult(error="GitHub not configured", is_error=True)

        repo_name = params.get("repo", "")
        file_path = params.get("path", "")
        content = params.get("content", "")
        message = params.get("message", "Update file")
        branch = params.get("branch", "main")

        if not repo_name or not file_path:
            return ToolResult(error="Need repo and path", is_error=True)

        # Prefix with username if not already
        if "/" not in repo_name:
            repo_name = f"{gh.username}/{repo_name}"

        try:
            repo = gh.gh.get_repo(repo_name)

            # Check if file exists (update vs create)
            try:
                existing = repo.get_contents(file_path, ref=branch)
                repo.update_file(
                    file_path, message, content,
                    existing.sha, branch=branch,
                )
                return ToolResult(output=f"Updated: {repo_name}/{file_path} on {branch}")
            except Exception:
                # File doesn't exist — create it
                repo.create_file(
                    file_path, message, content,
                    branch=branch,
                )
                return ToolResult(output=f"Created: {repo_name}/{file_path} on {branch}")

        except Exception as e:
            return ToolResult(error=f"Push failed: {e}", is_error=True)


class GitHubCreateIssueTool(BaseTool):
    category = "github"
    name = "github_create_issue"
    description = "Open an issue on a GitHub repository. Use for tracking ideas, bugs, or tasks."
    is_read_only = False

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository name."},
                "title": {"type": "string", "description": "Issue title."},
                "body": {"type": "string", "description": "Issue body (markdown)."},
                "labels": {"type": "array", "items": {"type": "string"}, "description": "Labels."},
            },
            "required": ["repo", "title"],
        }

    def needs_confirmation(self, params, config):
        return False

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        gh = _get_gh()
        if not gh.gh:
            return ToolResult(error="GitHub not configured", is_error=True)

        repo_name = params.get("repo", "")
        if "/" not in repo_name:
            repo_name = f"{gh.username}/{repo_name}"

        try:
            repo = gh.gh.get_repo(repo_name)
            issue = repo.create_issue(
                title=params.get("title", ""),
                body=params.get("body", ""),
                labels=params.get("labels", []),
            )
            return ToolResult(output=f"Issue #{issue.number}: {issue.html_url}")
        except Exception as e:
            return ToolResult(error=f"Failed: {e}", is_error=True)


class GitHubListReposTool(BaseTool):
    category = "github"
    name = "github_list_repos"
    description = "List your GitHub repositories. Shows name, description, and URL."
    is_read_only = True

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max repos to list (default: 10)."},
            },
        }

    def needs_confirmation(self, params, config):
        return False

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        gh = _get_gh()
        if not gh.gh:
            return ToolResult(error="GitHub not configured", is_error=True)

        limit = params.get("limit", 10)
        try:
            repos = list(gh.user.get_repos(sort="updated")[:limit])
            if not repos:
                return ToolResult(output="No repositories found.")

            lines = [f"{len(repos)} repos:\n"]
            for r in repos:
                vis = "🔒" if r.private else "🌐"
                desc = f" — {r.description[:60]}" if r.description else ""
                lines.append(f"  {vis} {r.name}{desc}\n      {r.html_url}")
            return ToolResult(output="\n".join(lines))
        except Exception as e:
            return ToolResult(error=f"Failed: {e}", is_error=True)


class GitHubReadFileTool(BaseTool):
    category = "github"
    name = "github_read_file"
    description = "Read a file from a GitHub repository."
    is_read_only = True

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Repository name."},
                "path": {"type": "string", "description": "File path in repo."},
                "branch": {"type": "string", "description": "Branch (default: main)."},
            },
            "required": ["repo", "path"],
        }

    def needs_confirmation(self, params, config):
        return False

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        gh = _get_gh()
        if not gh.gh:
            return ToolResult(error="GitHub not configured", is_error=True)

        repo_name = params.get("repo", "")
        if "/" not in repo_name:
            repo_name = f"{gh.username}/{repo_name}"

        try:
            repo = gh.gh.get_repo(repo_name)
            content = repo.get_contents(
                params.get("path", ""),
                ref=params.get("branch", "main"),
            )
            if content.encoding == "base64":
                text = base64.b64decode(content.content).decode("utf-8", errors="replace")
            else:
                text = content.decoded_content.decode("utf-8", errors="replace")

            return ToolResult(output=f"[{repo_name}/{content.path}]\n{text}")
        except Exception as e:
            return ToolResult(error=f"Failed: {e}", is_error=True)
