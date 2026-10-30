"""Why a proof was rejected and how to fix it, for the creator and the advertiser.

The verdict comes from the deterministic checks; this module only explains it. Each failed check maps to a
fixed template (problem, fix, whether a resubmission can fix it). An LLM, when configured, may rewrite the
summary sentence from those templates; it never sees the video's own text and cannot change the verdict.
"""

from __future__ import annotations

import logging
from datetime import datetime

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from ..models import DealTerms, ProofCheck, ProofIssue, ProofReview

log = logging.getLogger(__name__)

SAME_VIDEO = "Edit the video on YouTube and resubmit the same link."
NEW_VIDEO = "Upload a new video that meets this and submit its link."


def _issue(check: ProofCheck, terms: DealTerms, brand_handle: str, agreed_at: datetime, published_at: datetime | None) -> ProofIssue:
    name, base = check.name, check.name.split(":")[0]
    jurisdiction = name.split(":", 1)[1] if ":" in name else ""
    d = check.detail
    if base == "exists":
        return ProofIssue(check=name, problem="YouTube returned no video for this link.",
                          fix="Check the link, make sure the upload finished and the video is public, then resubmit.")  # fmt: skip
    if base == "public":
        return ProofIssue(check=name, problem=f"The video is {d}, so viewers and the verifier cannot count it.",
                          fix=f"Set its visibility to Public. {SAME_VIDEO}")  # fmt: skip
    if base == "owned_by_deal_channel":
        return ProofIssue(check=name, problem=f"The video is on a different channel than the one in the deal ({terms.channel_id}).",
                          fix=f"Post it on your verified channel {terms.channel_id}. {NEW_VIDEO}")  # fmt: skip
    if base == "published_in_window":
        if published_at is not None and published_at < agreed_at:
            return ProofIssue(check=name, problem="The video was published before the deal was agreed; an older upload cannot count.",
                              fix=f"{NEW_VIDEO} Deadline: {terms.publish_by:%Y-%m-%d %H:%M} UTC.")  # fmt: skip
        return ProofIssue(check=name, problem="The video was published after the deadline.", fix="It can no longer count for this deal.",
                          fixable=False)  # fmt: skip
    if base == "disclosure_and_brand":
        missing = d.removeprefix("missing ")
        return ProofIssue(check=name, problem=f"The title and description do not contain {missing}.",
                          fix=f"Add {' and '.join([*terms.required_tags, brand_handle])} to the title or description. {SAME_VIDEO}")  # fmt: skip
    if base == "no_ai_directed_instructions":
        return ProofIssue(check=name, problem=f"The title or description contains text aimed at an automated reviewer ({d.split('→')[0].strip()}).",
                          fix=f"Remove that phrase. {SAME_VIDEO}")  # fmt: skip
    if base == "video_length":
        return ProofIssue(check=name, problem=f"The video length does not fit the deal: {d}.",
                          fix=f"Upload a version between {terms.min_duration_seconds} and {terms.max_duration_seconds} seconds. {NEW_VIDEO}")  # fmt: skip
    if base == "disclosure_label":
        return ProofIssue(check=name, problem=f"No approved sponsorship label for {jurisdiction}: {d}.",
                          fix=f"Put the label at the start of the title or description. {SAME_VIDEO}")  # fmt: skip
    if base == "disclosure_in_video":
        return ProofIssue(check=name, problem=f"The disclosure inside the video was not confirmed ({jurisdiction} rules).",
                          fix="Make sure the video shows or says that it is sponsored, then tick the confirmation when you resubmit.")  # fmt: skip
    if base == "content":
        return ProofIssue(check=name, problem=d or "A content requirement was not met in the video.",
                          fix="Re-record so the name is said clearly the required number of times and upload the new video. If you did say it"
                          " and YouTube's automatic captions misheard it, add correct captions in YouTube Studio and resubmit the same link.")  # fmt: skip
    if base == "disclosure_policy_known":
        return ProofIssue(check=name, problem=f"The deal names a disclosure policy this server does not know ({d}).",
                          fix="This needs the platform operator; resubmitting will not help.", fixable=False)  # fmt: skip
    return ProofIssue(check=name, problem=d or "This check failed.", fix="Fix it and resubmit.")


def template_summary(issues: list[ProofIssue], can_resubmit: bool, resubmit_by: datetime | None) -> str:
    n = len(issues)
    head = f"{n} check{'s' if n != 1 else ''} failed: " + "; ".join(i.problem.rstrip(".") for i in issues[:3]) + ("…" if n > 3 else ".")
    if can_resubmit and resubmit_by:
        return f"{head} Fix {'them' if n != 1 else 'it'} and resubmit before {resubmit_by:%Y-%m-%d %H:%M} UTC."
    return f"{head} This cannot be fixed by resubmitting before the deadline."


class _Summary(BaseModel):
    summary: str = Field(max_length=400)


async def _llm_summary(llm: BaseChatModel, issues: list[ProofIssue], can_resubmit: bool, resubmit_by: datetime | None) -> str | None:
    listed = "\n".join(f"- {i.problem} Fix: {i.fix}" for i in issues)
    when = f"before {resubmit_by:%Y-%m-%d %H:%M} UTC" if can_resubmit and resubmit_by else "not possible: the deadline cannot be met"
    try:
        out = await llm.with_structured_output(_Summary, method="json_schema").ainvoke([
            SystemMessage(
                "You explain to a YouTube creator, in two or three plain, friendly sentences, why their sponsored post was not "
                "accepted and what to do next. Use only the facts given. Do not promise payment, do not add requirements, "
                "do not say the decision may change without a resubmission."
            ),
            HumanMessage(f"Failed checks:\n{listed}\nResubmission: {when}."),
        ])  # fmt: skip
    except Exception as e:  # noqa: BLE001 - the template summary stands
        log.warning("proof explanation LLM failed: %s", e)
        return None
    text = getattr(out, "summary", "").strip()
    return text or None


async def explain_rejection(checks: list[ProofCheck], terms: DealTerms, brand_handle: str, agreed_at: datetime, now: datetime,
                            published_at: datetime | None = None, llm: BaseChatModel | None = None) -> ProofReview:  # fmt: skip
    issues = [_issue(c, terms, brand_handle, agreed_at, published_at) for c in checks if not c.passed]
    can_resubmit = now <= terms.publish_by and all(i.fixable for i in issues)
    resubmit_by = terms.publish_by if can_resubmit else None
    summary = template_summary(issues, can_resubmit, resubmit_by)
    explained_by = "rules"
    if llm is not None and issues:
        if text := await _llm_summary(llm, issues, can_resubmit, resubmit_by):
            summary, explained_by = text, "llm"
    return ProofReview(decided_by="verifier", explained_by=explained_by, summary=summary, issues=issues,
                       can_resubmit=can_resubmit, resubmit_by=resubmit_by, at=now)  # fmt: skip


def explain_sent_back(note: str, terms: DealTerms, now: datetime) -> ProofReview:
    """The advertiser's disclosure review sent a verified post back."""
    can_resubmit = now <= terms.publish_by
    issue = ProofIssue(check="advertiser_review", problem=note.strip() or "The advertiser's disclosure review did not approve the post.",
                       fix=f"Change the post as described. {SAME_VIDEO}", fixable=can_resubmit)  # fmt: skip
    deadline = f" Resubmit before {terms.publish_by:%Y-%m-%d %H:%M} UTC." if can_resubmit else " The publish deadline has passed."
    return ProofReview(decided_by="advertiser", explained_by="rules", summary=f"The advertiser sent the post back: {issue.problem}{deadline}",
                       issues=[issue], can_resubmit=can_resubmit, resubmit_by=terms.publish_by if can_resubmit else None, at=now)  # fmt: skip
