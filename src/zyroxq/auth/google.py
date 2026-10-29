"""Google OAuth 2.0 (authorization-code flow) for sign-in. Influencers additionally grant
`youtube.readonly`, and `channels.list?mine=true` proves which YouTube channel they own."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx

from ..models import ChannelInfo

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"

BASE_SCOPES = ["openid", "email", "profile"]
YOUTUBE_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"


NO_YOUTUBE_PERMISSION = (
    "The YouTube permission was not granted. Sign in again with “Continue as an influencer” and keep "
    "“View your YouTube account” ticked on Google's consent screen."
)


class OAuthError(Exception):
    pass


def _youtube_forbidden(res: httpx.Response) -> str:
    """Turns YouTube's 403 into the one fix that applies."""
    try:
        error = res.json().get("error", {})
    except ValueError:
        error = {}
    reasons = {e.get("reason") for e in error.get("errors", [])} | {d.get("reason") for d in error.get("details", []) if isinstance(d, dict)}
    message = str(error.get("message", ""))
    if reasons & {"accessNotConfigured", "SERVICE_DISABLED"}:
        project = re.search(r"project[= ](\d+)", message)
        where = f" in Google Cloud project {project.group(1)} (the project of your OAuth client)" if project else " in your OAuth client's Google Cloud project"
        link = f" Enable it at https://console.developers.google.com/apis/api/youtube.googleapis.com/overview?project={project.group(1)}" if project else ""
        return f"YouTube Data API v3 is not enabled{where}.{link} then wait a few minutes and sign in again."
    if reasons & {"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}:
        return NO_YOUTUBE_PERMISSION
    if reasons & {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"}:
        return "YouTube API quota is exhausted for today; try again later."
    return f"YouTube refused access: {message or res.text[:200]}"


class GoogleOAuth:
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str, http: httpx.AsyncClient | None = None) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self._http = http or httpx.AsyncClient(timeout=15)

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def authorize_url(self, state: str, youtube: bool) -> str:
        scopes = BASE_SCOPES + ([YOUTUBE_SCOPE] if youtube else [])
        query = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "state": state,
            "access_type": "online",
            "include_granted_scopes": "true",
            "prompt": "select_account",
        }
        return f"{AUTH_URL}?{urlencode(query)}"

    async def exchange(self, code: str) -> dict[str, Any]:
        res = await self._http.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": self.redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if res.status_code != 200:
            raise OAuthError(f"token exchange failed: {res.text[:200]}")
        return res.json()

    async def userinfo(self, access_token: str) -> dict[str, Any]:
        res = await self._http.get(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"})
        if res.status_code != 200:
            raise OAuthError("could not read the Google profile")
        info = res.json()
        if not info.get("email_verified"):
            raise OAuthError("Google account email is not verified")
        return info

    async def my_channel(self, access_token: str, granted_scope: str | None = None) -> ChannelInfo:
        """`granted_scope` is the token response's `scope`: users can untick single permissions on Google's consent screen."""
        if granted_scope is not None and YOUTUBE_SCOPE not in granted_scope.split():
            raise OAuthError(NO_YOUTUBE_PERMISSION)
        res = await self._http.get(CHANNELS_URL, params={"part": "snippet,statistics", "mine": "true"}, headers={"Authorization": f"Bearer {access_token}"})
        if res.status_code == 403:
            raise OAuthError(_youtube_forbidden(res))
        if res.status_code != 200:
            raise OAuthError(f"YouTube channels.list failed: {res.text[:200]}")
        items = res.json().get("items", [])
        if not items:
            raise OAuthError("this Google account has no YouTube channel")
        item = items[0]
        stats = item.get("statistics", {})
        thumbs = item["snippet"].get("thumbnails", {})
        return ChannelInfo(
            channel_id=item["id"],
            title=item["snippet"]["title"],
            subscribers=int(stats.get("subscriberCount", 0)),
            thumbnail_url=(thumbs.get("medium") or thumbs.get("default") or {}).get("url", ""),
            verified_at=datetime.now(UTC),
            # Missing statistics stay None (unknown), never 0
            view_count=int(stats["viewCount"]) if "viewCount" in stats else None,
            video_count=int(stats["videoCount"]) if "videoCount" in stats else None,
            subscribers_hidden=bool(stats.get("hiddenSubscriberCount", False)),
        )
