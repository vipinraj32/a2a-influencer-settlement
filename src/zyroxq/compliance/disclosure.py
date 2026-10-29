"""Sponsorship-disclosure policies by jurisdiction: versioned data plus deterministic checks.

The built-in policies summarize public regulator guidance as ZyroXQ product rules. They are not legal
advice, do not guarantee compliance, and must be reviewed whenever the guidance changes; operators
publish new versions through PUT /api/disclosure-policies/{jurisdiction}. A campaign snapshots the
versions in force when it is created ("US@1"), and the signed terms carry them, so a later update never
changes what an agreed deal had to satisfy.
"""

from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, Field, computed_field

from ..models import ProofCheck
from ..verifier.youtube import VideoInfo

DISCLAIMER = "Summary of public guidance for product rules only. Not legal advice; no guarantee of compliance."
IN_VIDEO = "in_video_disclosure"  # attestation key: the disclosure is shown or said in the video itself


class DisclosurePolicy(BaseModel):
    jurisdiction: str = Field(pattern=r"^[A-Z]{2}$")
    version: int = 0  # assigned by the store
    effective_from: date
    sources: list[str] = Field(min_length=1)  # the guidance this summary is based on
    approved_labels: list[str] = Field(min_length=1)  # at least one must appear where viewers see it first
    discouraged_labels: list[str] = Field(default_factory=list)  # ambiguous: never count as the disclosure
    placement_chars: int = Field(default=100, ge=20, le=5000)  # product rule: title or the first N characters of the description
    in_video_required: bool = True  # creator must attest the disclosure is in the video itself
    livestream_rule: str = ""
    notes: str = ""
    disclaimer: str = DISCLAIMER

    @computed_field  # type: ignore[prop-decorator]
    @property
    def id(self) -> str:
        return f"{self.jurisdiction}@{self.version}"


BUILT_IN: dict[str, DisclosurePolicy] = {
    "US": DisclosurePolicy(
        jurisdiction="US",
        effective_from=date(2023, 6, 29),
        sources=[
            "FTC Guides Concerning the Use of Endorsements and Testimonials in Advertising, 16 CFR Part 255 (revised 2023)",
            "FTC, Disclosures 101 for Social Media Influencers — https://www.ftc.gov/business-guidance/resources/disclosures-101-social-media-influencers",
        ],
        approved_labels=["#ad", "#sponsored", "Advertisement", "Ad", "Sponsored", "Paid partnership"],
        discouraged_labels=["#sp", "#spon", "#collab", "#thanks", "#ambassador"],
        in_video_required=True,
        livestream_rule="Repeat the disclosure periodically so viewers who join late see or hear it.",
        notes="Disclosures must be hard to miss and in the endorsement itself; for video, in the video and not only the description.",
    ),
    "IN": DisclosurePolicy(
        jurisdiction="IN",
        effective_from=date(2023, 1, 20),
        sources=[
            "Department of Consumer Affairs, Government of India — 'Endorsements Know-hows!' guidelines for celebrities, "
            "influencers and virtual influencers on social media platforms (2023), https://consumeraffairs.nic.in",
            "ASCI Guidelines for Influencer Advertising in Digital Media",
        ],
        approved_labels=["#ad", "Advertisement", "Ad", "Sponsored", "Collaboration", "Partnership", "Paid promotion"],
        discouraged_labels=["#sp", "#spon", "#thanks"],
        in_video_required=True,
        livestream_rule="Display the disclosure continuously and prominently for the whole stream.",
        notes="Disclosures must be clear, prominent and hard to miss, in the same language as the endorsement; "
        "for video, in the video itself as well as the description.",
    ),
}


def _label_pattern(label: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w#]){re.escape(label)}(?!\w)", re.IGNORECASE)


def find_label(text: str, labels: list[str]) -> str | None:
    return next((label for label in labels if _label_pattern(label).search(text)), None)


def campaign_problems(policies: dict[str, DisclosurePolicy | None], required_tags: list[str]) -> list[str]:
    """Gate at campaign creation: every jurisdiction needs an active policy and a required tag it approves."""
    if not policies:
        return ["choose at least one jurisdiction for sponsorship disclosure"]
    problems = []
    for jur, policy in policies.items():
        if policy is None:
            problems.append(f"no disclosure policy is configured for {jur}")
        elif not approved_in_all(required_tags, [policy]):
            problems.append(f"required tags {required_tags} contain no disclosure label approved for {jur} ({', '.join(policy.approved_labels)})")
    return problems


def approved_in_all(required_tags: list[str], policies: list[DisclosurePolicy]) -> bool:
    """Used by the creator's agent: the offer carries a disclosure every listed policy accepts."""
    if not policies:
        return bool(required_tags)
    return all(any(tag.lower() in map(str.lower, p.approved_labels) for tag in required_tags) for p in policies)


def proof_checks(policies: list[DisclosurePolicy], video: VideoInfo, attestations: dict[str, bool]) -> list[ProofCheck]:
    """Deterministic disclosure checks on the published post. What the API cannot see is an attestation, labelled as one."""
    checks: list[ProofCheck] = []
    for p in policies:
        visible = f"{video.title}\n{video.description[: p.placement_chars]}"
        label = find_label(visible, p.approved_labels)
        checks.append(
            ProofCheck(
                name=f"disclosure_label:{p.jurisdiction}",
                passed=label is not None,
                detail=f"found {label!r} ({p.id})" if label else
                f"no approved label ({', '.join(p.approved_labels)}) in the title or first {p.placement_chars} characters ({p.id})",
            )
        )  # fmt: skip
        if p.in_video_required:
            attested = attestations.get(IN_VIDEO) is True
            checks.append(
                ProofCheck(
                    name=f"disclosure_in_video:{p.jurisdiction}",
                    passed=attested,
                    detail="creator attested (not machine-verified)" if attested else f"creator must confirm the disclosure is in the video ({p.id})",
                )
            )
    return checks
