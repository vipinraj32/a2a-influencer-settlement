from ..models import AuditEvent, Campaign, Deal, User


class MemoryStore:
    def __init__(self) -> None:
        self._campaigns: dict[str, Campaign] = {}
        self._deals: dict[str, Deal] = {}
        self._events: list[AuditEvent] = []
        self._webhooks: set[tuple[str, str]] = set()
        self._users: dict[str, User] = {}

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
