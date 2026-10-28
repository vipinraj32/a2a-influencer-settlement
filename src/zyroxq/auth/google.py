"""Google OAuth 2.0 (authorization-code flow) for sign-in. Influencers additionally grant
`youtube.readonly`, and `channels.list?mine=true` proves which YouTube channel they own."""

from __future__ import annotations

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


class OAuthError(Exception):
    pass


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

    async def my_channel(self, access_token: str) -> ChannelInfo:
        res = await self._http.get(CHANNELS_URL, params={"part": "snippet,statistics", "mine": "true"}, headers={"Authorization": f"Bearer {access_token}"})
        if res.status_code == 403:
            raise OAuthError("YouTube access was not granted (enable YouTube Data API v3 and allow the YouTube permission)")
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
        )
