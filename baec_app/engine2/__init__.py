"""BAEC Engine 2: Monitoring + Correspondence, deterministic domain core (Stage C).

Authority: the locked Stage A contract
(docs/engine2/ENGINE2_MONITORING_CORRESPONDENCE_DOMAIN_CONTRACT.md) and the
locked Stage B corpus (docs/engine2/corpus/). Every concept here is
IMPLEMENTATION, not a manuscript construct.

Boundary. This package reads Engine 1 vocabularies but never imports Engine 1
services, never writes Engine 1 records, and never decides BAEC validity,
currentness, or account state. It contains no AI, no persistence, no
interface, no source connectors, and no seller action. Correspondence narrows
attention; it does not establish buyer evaluation, purchase intent, or an
opportunity.

Modules:
    domain          vocabularies, Monitoring Plan activation, sources, Observations, candidates
    measurement     Derived Measurements (closed transformations, exact Decimal arithmetic)
    classification  evidence ledger, Human Correspondence Review, deterministic outcome rule
    errors          typed failures
"""
