"""Twitter Tool — Search, scan, generate replies, post as Compagnon tools.

Uses tweepy for Twitter API v2. Requires API keys (set via env vars or config).

Tools:
  twitter_search  — Search recent tweets by keywords
  twitter_reply   — Generate + post a reply to a tweet
  twitter_scan    — Scan for tweets matching keywords, generate responses
  twitter_post    — Post a new tweet

Env vars:
  TWITTER_API_KEY, TWITTER_API_SECRET
  TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET
  TWITTER_BEARER_TOKEN
  TWITTER_TASK — Default task description for response generation
  TWITTER_KEYWORDS — Comma-separated search keywords
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from tool_registry import BaseTool, ToolResult, ToolContext

import logging
logger = logging.getLogger(__name__)

# Tweet age limit for scanning
MAX_TWEET_AGE_HOURS = 3


class _TwitterClient:
    """Lazy-loaded Twitter API client."""

    def __init__(self):
        self._client = None
        self._api = None

    def _init(self) -> bool:
        if self._client is not None:
            return True
        try:
            import tweepy

            api_key = os.getenv("TWITTER_API_KEY", "")
            api_secret = os.getenv("TWITTER_API_SECRET", "")
            access_token = os.getenv("TWITTER_ACCESS_TOKEN", "")
            access_secret = os.getenv("TWITTER_ACCESS_TOKEN_SECRET", "")
            bearer = os.getenv("TWITTER_BEARER_TOKEN", "")

            if not all([api_key, api_secret, access_token, access_secret]):
                logger.warning("Twitter API keys not configured")
                return False

            # v2 client for search + post
            self._client = tweepy.Client(
                bearer_token=bearer or None,
                consumer_key=api_key,
                consumer_secret=api_secret,
                access_token=access_token,
                access_token_secret=access_secret,
                wait_on_rate_limit=True,
            )
            return True

        except ImportError:
            logger.warning("tweepy not installed: pip install tweepy")
            return False
        except Exception as e:
            logger.warning("Twitter init failed: %s", e)
            return False

    @property
    def client(self):
        self._init()
        return self._client

    def search(self, query: str, max_results: int = 20) -> list[dict]:
        if not self._init():
            return []
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_TWEET_AGE_HOURS)
            resp = self._client.search_recent_tweets(
                query=f"{query} -is:retweet -is:reply",
                max_results=min(max_results, 100),
                start_time=cutoff,
                tweet_fields=["created_at", "author_id", "public_metrics"],
                user_fields=["username", "name"],
                expansions=["author_id"],
            )

            users = {}
            if resp.includes and "users" in resp.includes:
                for u in resp.includes["users"]:
                    users[u.id] = {"username": u.username, "name": u.name}

            tweets = []
            if resp.data:
                for t in resp.data:
                    age = datetime.now(timezone.utc) - t.created_at
                    user = users.get(t.author_id, {})
                    tweets.append({
                        "id": str(t.id),
                        "text": t.text,
                        "author": user.get("username", "unknown"),
                        "author_name": user.get("name", ""),
                        "created_at": t.created_at.isoformat(),
                        "age_minutes": int(age.total_seconds() / 60),
                        "metrics": t.public_metrics or {},
                    })
            return tweets
        except Exception as e:
            logger.warning("Twitter search failed: %s", e)
            return []

    def reply(self, tweet_id: str, text: str) -> dict:
        if not self._init():
            return {"error": "Not configured"}
        try:
            result = self._client.create_tweet(text=text[:280], in_reply_to_tweet_id=tweet_id)
            return {"success": True, "reply_id": str(result.data["id"]), "text": text[:280]}
        except Exception as e:
            return {"error": str(e)}

    def post(self, text: str) -> dict:
        if not self._init():
            return {"error": "Not configured"}
        try:
            result = self._client.create_tweet(text=text[:280])
            return {"success": True, "tweet_id": str(result.data["id"]), "text": text[:280]}
        except Exception as e:
            return {"error": str(e)}


# Singleton
_tw: Optional[_TwitterClient] = None

def _get_tw() -> _TwitterClient:
    global _tw
    if _tw is None:
        _tw = _TwitterClient()
    return _tw


# ── Tools ──────────────────────────────────────────────────────

class TwitterSearchTool(BaseTool):
    category = "twitter"
    name = "twitter_search"
    description = (
        "Search recent tweets (last 3 hours) by keywords. "
        "Returns tweet text, author, age, and engagement metrics."
    )
    is_read_only = True

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (keywords, hashtags, @mentions)."},
                "max_results": {"type": "integer", "description": "Max results (default: 10)."},
            },
            "required": ["query"],
        }

    def needs_confirmation(self, params, config):
        return False

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        query = params.get("query", "")
        max_results = params.get("max_results", 10)
        if not query:
            return ToolResult(error="Empty query", is_error=True)

        tw = _get_tw()
        tweets = tw.search(query, max_results)
        if not tweets:
            return ToolResult(output=f"No tweets found for: {query}")

        lines = [f"Found {len(tweets)} tweets for '{query}':\n"]
        for i, t in enumerate(tweets, 1):
            metrics = t.get("metrics", {})
            likes = metrics.get("like_count", 0)
            rts = metrics.get("retweet_count", 0)
            lines.append(
                f"{i}. @{t['author']} ({t['age_minutes']}m ago) [{likes}♥ {rts}🔁]\n"
                f"   {t['text'][:200]}\n"
                f"   ID: {t['id']}\n"
            )
        return ToolResult(output="\n".join(lines))


class TwitterReplyTool(BaseTool):
    category = "twitter"
    name = "twitter_reply"
    description = (
        "Reply to a tweet. Provide the tweet ID and your response text. "
        "Max 280 characters."
    )
    is_read_only = False

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "tweet_id": {"type": "string", "description": "ID of the tweet to reply to."},
                "text": {"type": "string", "description": "Reply text (max 280 chars)."},
            },
            "required": ["tweet_id", "text"],
        }

    def needs_confirmation(self, params, config):
        return True  # Always confirm before posting

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        tweet_id = params.get("tweet_id", "")
        text = params.get("text", "")
        if not tweet_id or not text:
            return ToolResult(error="Need tweet_id and text", is_error=True)

        tw = _get_tw()
        result = tw.reply(tweet_id, text)
        if result.get("error"):
            return ToolResult(error=f"Reply failed: {result['error']}", is_error=True)
        return ToolResult(output=f"Replied to {tweet_id}: {result.get('text', '')}")


class TwitterPostTool(BaseTool):
    category = "twitter"
    name = "twitter_post"
    description = "Post a new tweet. Max 280 characters."
    is_read_only = False

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Tweet text (max 280 chars)."},
            },
            "required": ["text"],
        }

    def needs_confirmation(self, params, config):
        return True

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        text = params.get("text", "")
        if not text:
            return ToolResult(error="Empty tweet", is_error=True)

        tw = _get_tw()
        result = tw.post(text)
        if result.get("error"):
            return ToolResult(error=f"Post failed: {result['error']}", is_error=True)
        return ToolResult(output=f"Posted: {result.get('text', '')} (ID: {result.get('tweet_id', '')})")


class TwitterScanTool(BaseTool):
    category = "twitter"
    name = "twitter_scan"
    description = (
        "Scan for recent tweets matching keywords and generate suggested responses. "
        "Does NOT auto-post — returns the tweets with AI-generated reply suggestions. "
        "Use twitter_reply to post individual replies."
    )
    is_read_only = True

    def get_input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Keywords to search for.",
                },
                "task": {
                    "type": "string",
                    "description": "What kind of responses to generate (e.g. 'respond helpfully about crypto trading').",
                },
                "max_results": {"type": "integer", "description": "Max tweets to scan (default: 5)."},
            },
            "required": ["keywords"],
        }

    def needs_confirmation(self, params, config):
        return False

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        keywords = params.get("keywords", [])
        task = params.get("task", os.getenv("TWITTER_TASK", "respond helpfully and professionally"))
        max_results = params.get("max_results", 5)

        if not keywords:
            kw_env = os.getenv("TWITTER_KEYWORDS", "")
            keywords = [k.strip() for k in kw_env.split(",") if k.strip()]

        if not keywords:
            return ToolResult(error="No keywords provided", is_error=True)

        query = " OR ".join(keywords)
        tw = _get_tw()
        tweets = tw.search(query, max_results)

        if not tweets:
            return ToolResult(output=f"No tweets found for: {query}")

        # Return tweets with metadata — the LLM will generate replies in context
        lines = [
            f"Found {len(tweets)} tweets matching [{', '.join(keywords)}].\n"
            f"Task: {task}\n"
            f"Generate appropriate replies for each:\n"
        ]
        for i, t in enumerate(tweets, 1):
            lines.append(
                f"--- Tweet {i} ---\n"
                f"@{t['author']}: {t['text']}\n"
                f"ID: {t['id']} | {t['age_minutes']}m ago\n"
            )

        return ToolResult(output="\n".join(lines))
