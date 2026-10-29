"""Sponsorship-disclosure compliance. `DisclosureRegistry` keeps every published policy version in memory
(loaded from the store at startup), so agents and the verifier resolve "US@1" without I/O."""

from __future__ import annotations

from typing import Any

from ..store import Store
from .disclosure import BUILT_IN, DISCLAIMER, IN_VIDEO, DisclosurePolicy, approved_in_all, campaign_problems, find_label, proof_checks

KIND = "disclosure_policy"


class DisclosureRegistry:
    def __init__(self, store: Store) -> None:
        self.store = store
        self._versions: dict[str, DisclosurePolicy] = {}  # "US@1" → policy
        self._latest: dict[str, DisclosurePolicy] = {}

    async def load(self) -> None:
        """Seeds the built-in policies as version 1 the first time, then loads every stored version."""
        for jur, policy in BUILT_IN.items():
            if not await self.store.list_versions(KIND, jur):
                await self.store.save_version(KIND, jur, policy.model_dump(mode="json", exclude={"version", "id"}))
            for doc in await self.store.list_versions(KIND, jur):
                self._remember(DisclosurePolicy.model_validate(doc))

    def _remember(self, policy: DisclosurePolicy) -> None:
        self._versions[policy.id] = policy
        current = self._latest.get(policy.jurisdiction)
        if current is None or policy.version > current.version:
            self._latest[policy.jurisdiction] = policy

    def latest(self, jurisdiction: str) -> DisclosurePolicy | None:
        return self._latest.get(jurisdiction)

    def resolve(self, policy_id: str) -> DisclosurePolicy | None:
        return self._versions.get(policy_id)

    def resolve_all(self, policy_ids: list[str]) -> list[DisclosurePolicy]:
        return [p for i in policy_ids if (p := self.resolve(i)) is not None]

    def all_latest(self) -> list[DisclosurePolicy]:
        return sorted(self._latest.values(), key=lambda p: p.jurisdiction)

    async def publish(self, jurisdiction: str, data: dict[str, Any]) -> DisclosurePolicy:
        draft = DisclosurePolicy.model_validate({**data, "jurisdiction": jurisdiction, "version": 0})
        doc = await self.store.save_version(KIND, jurisdiction, draft.model_dump(mode="json", exclude={"version", "id"}))
        policy = DisclosurePolicy.model_validate(doc)
        self._remember(policy)
        return policy


__all__ = [
    "BUILT_IN",
    "DISCLAIMER",
    "IN_VIDEO",
    "DisclosurePolicy",
    "DisclosureRegistry",
    "approved_in_all",
    "campaign_problems",
    "find_label",
    "proof_checks",
]
