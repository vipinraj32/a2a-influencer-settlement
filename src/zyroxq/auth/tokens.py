"""Short-lived bearer tokens that act as one user — the in-app assistant passes one to the MCP server,
which forwards it to the REST API, so MCP tools only ever see that user's campaigns."""

from __future__ import annotations

from itsdangerous import BadSignature, URLSafeTimedSerializer

SALT = "zyroxq-api-token"


def issue_user_token(secret: str, user_id: str) -> str:
    return URLSafeTimedSerializer(secret, salt=SALT).dumps({"uid": user_id})


def read_user_token(secret: str, token: str, max_age: int = 3600) -> str | None:
    try:
        data = URLSafeTimedSerializer(secret, salt=SALT).loads(token, max_age=max_age)
    except BadSignature:
        return None
    return data.get("uid") if isinstance(data, dict) else None
