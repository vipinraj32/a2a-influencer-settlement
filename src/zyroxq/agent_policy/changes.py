"""Which edits loosen a hard constraint. Those need explicit confirmation before a new version is saved,
because they let the agent agree to deals it would have refused before."""

from __future__ import annotations

from .models import AdvertiserPolicy, InfluencerPolicy


def material_changes(old: InfluencerPolicy | AdvertiserPolicy | None, new: InfluencerPolicy | AdvertiserPolicy) -> list[str]:
    if old is None or old.role != new.role:
        return []
    out: list[str] = []

    def lower(label: str, a, b) -> None:
        if b < a:
            out.append(f"{label} lowered from {a} to {b}")

    def higher(label: str, a, b) -> None:
        if b > a:
            out.append(f"{label} raised from {a} to {b}")

    def enabled(label: str, a: bool, b: bool) -> None:
        if b and not a:
            out.append(f"{label} now allowed")

    def removed(label: str, a: list[str], b: list[str]) -> None:
        gone = {x.lower() for x in a} - {x.lower() for x in b}
        if gone:
            out.append(f"{label} no longer excludes {sorted(gone)}")

    if isinstance(old, InfluencerPolicy) and isinstance(new, InfluencerPolicy):
        o, n = old.hard, new.hard
        lower("minimum fixed fee", o.min_fixed_fee, n.min_fixed_fee)
        lower("minimum CPM", o.min_cpm, n.min_cpm)
        lower("minimum turnaround days", o.min_turnaround_days, n.min_turnaround_days)
        higher("maximum revisions", o.max_revisions, n.max_revisions)
        higher("maximum license days", o.max_license_days, n.max_license_days)
        higher("maximum exclusivity days", o.max_exclusivity_days, n.max_exclusivity_days)
        enabled("paid amplification", o.allow_paid_amplification, n.allow_paid_amplification)
        enabled("exclusivity", o.allow_exclusivity, n.allow_exclusivity)
        if set(n.accepted_models) - set(o.accepted_models):
            out.append(f"newly accepted compensation models {sorted(set(n.accepted_models) - set(o.accepted_models))}")
        removed("forbidden categories", o.forbidden_categories, n.forbidden_categories)
        if o.require_brief and not n.require_brief:
            out.append("a written brief is no longer required")
    elif isinstance(old, AdvertiserPolicy) and isinstance(new, AdvertiserPolicy):
        o, n = old.hard, new.hard
        lower("minimum verified subscribers", o.min_verified_subscribers, n.min_verified_subscribers)
        if o.require_verified_channel and not n.require_verified_channel:
            out.append("unverified channels now allowed")
        removed("blocked niches", o.blocked_niches, n.blocked_niches)
        a, b = old.preferences.escalate_fixed_above, new.preferences.escalate_fixed_above
        if a is not None and (b is None or b > a):
            out.append(f"human escalation threshold raised from {a} to {b or 'none'}")
    return out
