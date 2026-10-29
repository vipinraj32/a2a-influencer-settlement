from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from ..models import AuditEvent, Campaign, Deal, User


class MemoryStore:
    def __init__(self) -> None:
        self._campaigns: dict[str, Campaign] = {}
        self._deals: dict[str, Deal] = {}
        self._events: list[AuditEvent] = []
        self._webhooks: set[tuple[str, str]] = set()
        self._users: dict[str, User] = {}
        self._claims: dict[tuple[str, str], str] = {}
        self._reservations: dict[str, tuple[str, Decimal]] = {}  # deal → (campaign, amount)
        self._versions: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._decisions: list[dict[str, Any]] = []

    async def save_campaign(self, campaign: Campaign) -> None:
        self._campaigns[campaign.spec.id] = campaign.model_copy(deep=True)

    async def get_campaign(self, campaign_id: str) -> Campaign | None:
        c = self._campaigns.get(campaign_id)
        return c.model_copy(deep=True) if c else None

    async def list_campaigns(self) -> list[Campaign]:
        return sorted((c.model_copy(deep=True) for c in self._campaigns.values()), key=lambda c: c.created_at, reverse=True)

    async def save_deal(self, deal: Deal) -> None:
        self._deals[deal.id] = deal.model_copy(deep=True)

    async def get_deal(self, deal_id: str) -> Deal | None:
        d = self._deals.get(deal_id)
        return d.model_copy(deep=True) if d else None

    async def list_deals(self, campaign_id: str | None = None) -> list[Deal]:
        return [d.model_copy(deep=True) for d in self._deals.values() if campaign_id is None or d.campaign_id == campaign_id]

    async def add_event(self, event: AuditEvent) -> None:
        self._events.append(event)

    async def list_events(self, campaign_id: str, deal_id: str | None = None) -> list[AuditEvent]:
        return [e for e in self._events if e.campaign_id == campaign_id and (deal_id is None or e.deal_id == deal_id)]

    async def save_user(self, user: User) -> None:
        self._users[user.id] = user.model_copy(deep=True)

    async def get_user(self, user_id: str) -> User | None:
        u = self._users.get(user_id)
        return u.model_copy(deep=True) if u else None

    async def get_user_by_email(self, email: str) -> User | None:
        return next((u.model_copy(deep=True) for u in self._users.values() if u.email == email), None)

    async def list_users(self, role: str | None = None) -> list[User]:
        return sorted((u.model_copy(deep=True) for u in self._users.values() if role is None or u.role == role), key=lambda u: u.created_at)

    async def claim_webhook(self, request_id: str, status: str) -> bool:
        key = (request_id, status)
        if key in self._webhooks:
            return False
        self._webhooks.add(key)
        return True

    # No awaits below: each method runs atomically on the event loop
    async def claim(self, kind: str, key: str, owner: str) -> str:
        return self._claims.setdefault((kind, key.lower()), owner)

    async def reserve_budget(self, campaign_id: str, deal_id: str, amount: Decimal, budget: Decimal) -> bool:
        if deal_id in self._reservations:
            return self._reservations[deal_id][1] == amount
        used = sum((a for c, a in self._reservations.values() if c == campaign_id), Decimal("0"))
        if used + amount > budget:
            return False
        self._reservations[deal_id] = (campaign_id, amount)
        return True

    async def release_budget(self, deal_id: str) -> None:
        self._reservations.pop(deal_id, None)

    async def reserved_budget(self, campaign_id: str, exclude: str | None = None) -> Decimal:
        return sum((a for d, (c, a) in self._reservations.items() if c == campaign_id and d != exclude), Decimal("0"))

    async def save_version(self, kind: str, key: str, doc: dict[str, Any]) -> dict[str, Any]:
        versions = self._versions.setdefault((kind, key), [])
        saved = {**doc, "version": len(versions) + 1, "updated_at": datetime.now(UTC).isoformat()}
        versions.append(saved)
        return dict(saved)

    async def get_version(self, kind: str, key: str, version: int | None = None) -> dict[str, Any] | None:
        versions = self._versions.get((kind, key), [])
        if version is None:
            return dict(versions[-1]) if versions else None
        return dict(versions[version - 1]) if 0 < version <= len(versions) else None

    async def list_versions(self, kind: str, key: str) -> list[dict[str, Any]]:
        return [dict(v) for v in self._versions.get((kind, key), [])]

    async def add_decision(self, record: dict[str, Any]) -> None:
        self._decisions.append(dict(record))

    async def list_decisions(self, deal_id: str, party: str | None = None) -> list[dict[str, Any]]:
        return [dict(r) for r in self._decisions if r["deal_id"] == deal_id and (party is None or r["party"] == party)]
