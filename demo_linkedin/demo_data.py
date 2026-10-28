"""Synthetic, prerecorded content for the LinkedIn product demo.

Every name, statement, notice, and prepared output below is fictional. Nothing here is generated at run time,
fetched from a service, or derived from real customer, employer, or account data.
"""

from __future__ import annotations

ACCOUNT = "Alder Ridge Clinics"
SELLER = "Cobalt Imaging Supply"
BUYER_ROLE = "Director of Operations"
BUYER_FIRST_NAME = "Jordan"
SELLER_FIRST_NAME = "Alex"

# The buyer's statement, verbatim. The highlighted phrases must appear in it exactly as written.
BUYER_STATEMENT = (
    "If Cobalt raises the price of our imaging service contract by more than 8% at the July renewal, "
    "we would put the contract out for review."
)

# (label, value, phrase in BUYER_STATEMENT that the value was read from)
EXTRACTED_FIELDS = (
    ("Condition", "Price increase > 8%", "by more than 8%"),
    ("Timing", "July renewal", "at the July renewal"),
    ("Potential action", "Contract review", "put the contract out for review"),
)

THRESHOLD_PERCENT = 8
OBSERVED_PERCENT = 9

EVIDENCE_TYPE = "Renewal Update"
EVIDENCE_TITLE = "July renewal notice"
EVIDENCE_CHANGE = "Price change: +9%"
EVIDENCE_STATEMENT = "July renewal notice indicates a 9% service-contract increase."

RECOGNIZE_BUYER_SAID = "Price increase above 8% at July renewal could trigger contract review."
RECOGNIZE_WHAT_CHANGED = "July renewal notice indicates a 9% increase."

WHY_NOW = (
    "Alder Ridge previously said that a price increase above 8% around the July renewal could make the imaging "
    "contract worth reviewing. A recent renewal notice indicates a 9% increase. This creates a grounded reason to "
    "revisit the earlier conversation, without assuming the buyer is currently evaluating alternatives."
)

EMAIL_SUBJECT = "Checking back on your July renewal"
EMAIL_BODY = (
    "Hi Jordan,",
    "When we last spoke, you mentioned that a price increase above 8% around the July renewal could make it worth "
    "revisiting the imaging contract.",
    "I wanted to reconnect because that condition may now be relevant.",
    "Has anything changed on your side, and would it be useful to revisit the conversation?",
    "Best,\nAlex",
)

CALL_OPENING = (
    "When we last spoke, you mentioned that a price increase above 8% around the July renewal could make the "
    "contract worth revisiting. I wanted to check back because that condition may now be relevant. Has anything "
    "changed on your side?"
)

DISCOVERY_QUESTIONS = (
    "Has anything changed since we last discussed the July renewal?",
    "Is the 8% threshold still meaningful for your team?",
    "Has the renewal changed how you're thinking about the current agreement?",
    "Would it be useful to revisit the options we discussed?",
)
