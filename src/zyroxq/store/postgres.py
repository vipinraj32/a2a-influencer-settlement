"""PostgreSQL store. Documents are JSONB (the pydantic models), with indexed columns for lookups."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from ..models import AuditEvent, Campaign, Deal, User

SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    id          TEXT PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL,
    data        JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS deals (
    id          TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id),
    state       TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    data        JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS deals_campaign_idx ON deals (campaign_id);
CREATE TABLE IF NOT EXISTS audit_events (
    id          BIGSERIAL PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    deal_id     TEXT,
    at          TIMESTAMPTZ NOT NULL,
    actor       TEXT NOT NULL,
    kind        TEXT NOT NULL,
    detail      JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_campaign_idx ON audit_events (campaign_id, id);
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    email       TEXT NOT NULL UNIQUE,
    role        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL,
    data        JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS webhook_receipts (
    request_id  TEXT NOT NULL,
    status      TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (request_id, status)
);
CREATE TABLE IF NOT EXISTS claims (
    kind        TEXT NOT NULL,
    key         TEXT NOT NULL,
    owner       TEXT NOT NULL,
    claimed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (kind, key)
);
CREATE TABLE IF NOT EXISTS budget_reservations (
    deal_id     TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id),
    amount      NUMERIC(20, 6) NOT NULL,
    reserved_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS budget_campaign_idx ON budget_reservations (campaign_id);
CREATE TABLE IF NOT EXISTS versioned_docs (
    kind        TEXT NOT NULL,
    key         TEXT NOT NULL,
    version     INTEGER NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    data        JSONB NOT NULL,
    PRIMARY KEY (kind, key, version)
);
CREATE TABLE IF NOT EXISTS negotiation_decisions (
    id          TEXT PRIMARY KEY,
    deal_id     TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    party       TEXT NOT NULL,
    at          TIMESTAMPTZ NOT NULL,
    data        JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS decisions_deal_idx ON negotiation_decisions (deal_id, at);
"""


class PostgresStore:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    @classmethod
    async def connect(cls, database_url: str) -> PostgresStore:
        pool = AsyncConnectionPool(database_url, min_size=1, max_size=10, open=False)
        await pool.open(wait=True, timeout=10)
        store = cls(pool)
        async with pool.connection() as conn:
            await conn.execute(SCHEMA)
        return store

    async def close(self) -> None:
        await self.pool.close()

    async def save_campaign(self, campaign: Campaign) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO campaigns (id, created_at, data) VALUES (%s, %s, %s) ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data",
                (campaign.spec.id, campaign.created_at, Jsonb(campaign.model_dump(mode="json"))),
            )

    async def get_campaign(self, campaign_id: str) -> Campaign | None:
        async with self.pool.connection() as conn:
            row = await (await conn.execute("SELECT data FROM campaigns WHERE id = %s", (campaign_id,))).fetchone()
        return Campaign.model_validate(row[0]) if row else None

    async def list_campaigns(self) -> list[Campaign]:
        async with self.pool.connection() as conn:
            rows = await (await conn.execute("SELECT data FROM campaigns ORDER BY created_at DESC")).fetchall()
        return [Campaign.model_validate(r[0]) for r in rows]

    async def save_deal(self, deal: Deal) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO deals (id, campaign_id, state, data) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET state = EXCLUDED.state, data = EXCLUDED.data, updated_at = now()",
                (deal.id, deal.campaign_id, deal.state.value, Jsonb(deal.model_dump(mode="json"))),
            )

    async def get_deal(self, deal_id: str) -> Deal | None:
        async with self.pool.connection() as conn:
            row = await (await conn.execute("SELECT data FROM deals WHERE id = %s", (deal_id,))).fetchone()
        return Deal.model_validate(row[0]) if row else None

    async def list_deals(self, campaign_id: str | None = None) -> list[Deal]:
        async with self.pool.connection() as conn:
            if campaign_id is None:
                cur = await conn.execute("SELECT data FROM deals ORDER BY id")
            else:
                cur = await conn.execute("SELECT data FROM deals WHERE campaign_id = %s ORDER BY id", (campaign_id,))
            rows = await cur.fetchall()
        return [Deal.model_validate(r[0]) for r in rows]

    async def add_event(self, event: AuditEvent) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO audit_events (campaign_id, deal_id, at, actor, kind, detail) VALUES (%s, %s, %s, %s, %s, %s)",
                (event.campaign_id, event.deal_id, event.at, event.actor, event.kind, Jsonb(event.detail)),
            )

    async def list_events(self, campaign_id: str, deal_id: str | None = None) -> list[AuditEvent]:
        sql = "SELECT campaign_id, deal_id, at, actor, kind, detail FROM audit_events WHERE campaign_id = %s"
        params: tuple = (campaign_id,)
        if deal_id is not None:
            sql += " AND deal_id = %s"
            params += (deal_id,)
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(sql + " ORDER BY id", params)).fetchall()
        return [AuditEvent(campaign_id=r[0], deal_id=r[1], at=r[2], actor=r[3], kind=r[4], detail=r[5]) for r in rows]

    async def save_user(self, user: User) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO users (id, email, role, created_at, data) VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email, role = EXCLUDED.role, data = EXCLUDED.data",
                (user.id, user.email, user.role, user.created_at, Jsonb(user.model_dump(mode="json"))),
            )

    async def get_user(self, user_id: str) -> User | None:
        async with self.pool.connection() as conn:
            row = await (await conn.execute("SELECT data FROM users WHERE id = %s", (user_id,))).fetchone()
        return User.model_validate(row[0]) if row else None

    async def get_user_by_email(self, email: str) -> User | None:
        async with self.pool.connection() as conn:
            row = await (await conn.execute("SELECT data FROM users WHERE email = %s", (email,))).fetchone()
        return User.model_validate(row[0]) if row else None

    async def list_users(self, role: str | None = None) -> list[User]:
        async with self.pool.connection() as conn:
            if role is None:
                cur = await conn.execute("SELECT data FROM users ORDER BY created_at")
            else:
                cur = await conn.execute("SELECT data FROM users WHERE role = %s ORDER BY created_at", (role,))
            rows = await cur.fetchall()
        return [User.model_validate(r[0]) for r in rows]

    async def claim_webhook(self, request_id: str, status: str) -> bool:
        async with self.pool.connection() as conn:
            cur = await conn.execute("INSERT INTO webhook_receipts (request_id, status) VALUES (%s, %s) ON CONFLICT DO NOTHING", (request_id, status))
            return cur.rowcount == 1

    async def claim(self, kind: str, key: str, owner: str) -> str:
        async with self.pool.connection() as conn:
            await conn.execute("INSERT INTO claims (kind, key, owner) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (kind, key.lower(), owner))
            row = await (await conn.execute("SELECT owner FROM claims WHERE kind = %s AND key = %s", (kind, key.lower()))).fetchone()
        assert row is not None
        return row[0]

    async def reserve_budget(self, campaign_id: str, deal_id: str, amount: Decimal, budget: Decimal) -> bool:
        async with self.pool.connection() as conn, conn.transaction():
            # Row lock on the campaign serializes concurrent reservations across processes
            await conn.execute("SELECT 1 FROM campaigns WHERE id = %s FOR UPDATE", (campaign_id,))
            row = await (await conn.execute("SELECT amount FROM budget_reservations WHERE deal_id = %s", (deal_id,))).fetchone()
            if row is not None:
                return Decimal(row[0]) == amount
            used = await (await conn.execute("SELECT COALESCE(SUM(amount), 0) FROM budget_reservations WHERE campaign_id = %s", (campaign_id,))).fetchone()
            assert used is not None
            if Decimal(used[0]) + amount > budget:
                return False
            await conn.execute("INSERT INTO budget_reservations (deal_id, campaign_id, amount) VALUES (%s, %s, %s)", (deal_id, campaign_id, amount))
            return True

    async def release_budget(self, deal_id: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute("DELETE FROM budget_reservations WHERE deal_id = %s", (deal_id,))

    async def reserved_budget(self, campaign_id: str, exclude: str | None = None) -> Decimal:
        async with self.pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM budget_reservations WHERE campaign_id = %s AND deal_id <> %s", (campaign_id, exclude or "")
            )).fetchone()  # fmt: skip
        assert row is not None
        return Decimal(row[0])

    async def save_version(self, kind: str, key: str, doc: dict[str, Any]) -> dict[str, Any]:
        async with self.pool.connection() as conn, conn.transaction():
            # Serializes writers of the same document so version numbers never collide
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{kind}:{key}",))
            row = await (await conn.execute("SELECT COALESCE(MAX(version), 0) FROM versioned_docs WHERE kind = %s AND key = %s", (kind, key))).fetchone()
            assert row is not None
            saved = {**doc, "version": row[0] + 1, "updated_at": datetime.now(UTC).isoformat()}
            await conn.execute("INSERT INTO versioned_docs (kind, key, version, data) VALUES (%s, %s, %s, %s)", (kind, key, saved["version"], Jsonb(saved)))
        return saved

    async def get_version(self, kind: str, key: str, version: int | None = None) -> dict[str, Any] | None:
        async with self.pool.connection() as conn:
            if version is None:
                cur = await conn.execute("SELECT data FROM versioned_docs WHERE kind = %s AND key = %s ORDER BY version DESC LIMIT 1", (kind, key))
            else:
                cur = await conn.execute("SELECT data FROM versioned_docs WHERE kind = %s AND key = %s AND version = %s", (kind, key, version))
            row = await cur.fetchone()
        return row[0] if row else None

    async def list_versions(self, kind: str, key: str) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn:
            rows = await (await conn.execute("SELECT data FROM versioned_docs WHERE kind = %s AND key = %s ORDER BY version", (kind, key))).fetchall()
        return [r[0] for r in rows]

    async def add_decision(self, record: dict[str, Any]) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO negotiation_decisions (id, deal_id, campaign_id, party, at, data) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (record["id"], record["deal_id"], record["campaign_id"], record["party"], record["at"], Jsonb(record)),
            )

    async def list_decisions(self, deal_id: str, party: str | None = None) -> list[dict[str, Any]]:
        sql, params = "SELECT data FROM negotiation_decisions WHERE deal_id = %s", [deal_id]
        if party is not None:
            sql += " AND party = %s"
            params.append(party)
        async with self.pool.connection() as conn:
            rows = await (await conn.execute(sql + " ORDER BY at, id", params)).fetchall()
        return [r[0] for r in rows]
