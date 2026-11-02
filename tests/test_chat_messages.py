"""The negotiation chat reads like a conversation: each agent message is a full sentence built from public facts, the
wording is reproducible, and no message names an amount that was not on the table (so no private limit or floor)."""

import re

from zyroxq.agents import chat

from .test_e2e import client_for, server  # noqa: F401 - pytest fixtures
from .test_negotiation_stream import launch, setup

AMOUNT = re.compile(r"\$([\d,]+\.\d\d)")


def amounts(text: str) -> set[str]:
    return {a.replace(",", "") for a in AMOUNT.findall(text)}


def test_pay_words_follow_the_compensation_model(offer):
    assert chat.pay_words(offer.model_copy(update={"compensation_model": "hybrid"})) == (
        f"${offer.fixed_fee:.2f} up front plus ${offer.cpm:.2f} per 1,000 views, up to ${offer.cap:.2f} in total"
    )
    assert chat.pay_words(offer.model_copy(update={"compensation_model": "fixed"})) == f"${offer.fixed_fee:.2f} for the video"
    assert chat.pay_words(offer.model_copy(update={"compensation_model": "cpm"})).startswith(f"${offer.cpm:.2f} per 1,000 views")


def test_messages_are_reproducible_and_sentences(offer):
    first = chat.advertiser_opening("FitFuel", "Ava", offer, "Protein snack launch")
    assert first == chat.advertiser_opening("FitFuel", "Ava", offer, "Protein snack launch")
    assert first.startswith("Hi Ava! I'm the agent for FitFuel.") and "Protein snack launch" in first
    countered = offer.model_copy(update={"round": 2, "fixed_fee": offer.fixed_fee + 1})
    assert "moved up from" in chat.advertiser_counter(countered, offer, last_round=False)
    assert "best and final" in chat.advertiser_counter(countered, offer, last_round=True)
    assert chat.creator_declines("Declined: no agreement within the round limit.") == (
        "Thanks for the offer, but I'll have to pass. No agreement within the round limit."
    )
    assert chat.advertiser_opening("B", "C", offer, "x " * 400).count("…") == 1  # a long brief is shortened


async def test_transcript_reads_as_a_conversation_without_private_amounts(server):
    app, _ = server
    advertiser_id = await setup(app)
    _, deals = await launch(app, advertiser_id)
    for creator in ("ava", "kai", "leo"):
        deal = deals[creator]
        events = (await client_for(app, f"USR-{creator}").get(f"/api/negotiations/{deal['id']}/events")).json()
        messages = [e for e in events if e["event_type"] == "message_completed" and e["agent_role"] != "system"]
        assert messages, creator
        assert messages[0]["agent_role"] == "advertiser" and messages[0]["content"].startswith("Hi ")
        on_table: set[str] = set()
        for m in messages:
            text = m["content"]
            assert len(text.split()) >= 5 and text[-1] in ".!?", text  # a sentence, not a bare label
            if m["offer"]:
                on_table |= {m["offer"][k] for k in ("fixed_fee", "cpm", "cap")}
            on_table = {f"{float(v):.2f}" for v in on_table}
            assert amounts(text) <= on_table, (creator, text, on_table)
        if deal["state"] != "REJECTED":  # the creator's agent says yes in words, and names what it signed
            accepted = next(m for m in messages if m["agent_role"] == "influencer" and m["reply"] == "accept")
            assert "I've signed the terms" in accepted["content"], accepted["content"]
