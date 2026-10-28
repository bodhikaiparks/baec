"""Fixed vocabularies for the BAEC domain layer.

Each enum notes its source label from docs/RESEARCH_CONTRACT.md:
DEFINITIONAL, PROPOSED, MANAGERIAL, or IMPLEMENTATION. Only the first three
come from the manuscript. Do not add or rename members without updating the
contract.

These are strict enums. A raw string never compares equal to a member, so
unvalidated text cannot pass as a domain value. Each member keeps a readable
string in .value; serialization must use .value explicitly. Constructing an
enum from an unsupported string raises ValueError, which is how invalid
values are rejected.
"""

from enum import Enum


class AccountState(Enum):
    """The three account-management states (RC-20, MANAGERIAL).

    There is deliberately no member for an unclassified account. An account
    that has not been qualified has account_state = None (RC-21).
    """

    ACTIVE_OPPORTUNITY = "ACTIVE_OPPORTUNITY"
    CONDITIONALLY_DORMANT = "CONDITIONALLY_DORMANT"
    NO_PLAUSIBLE_PATH = "NO_PLAUSIBLE_PATH"


class BaecCriterion(Enum):
    """The four constitutive criteria (RC-02, DEFINITIONAL)."""

    PRESENT_NON_EVALUATION = "PRESENT_NON_EVALUATION"  # C1
    PROSPECTIVE_CONDITION = "PROSPECTIVE_CONDITION"  # C2
    BUYER_ARTICULATION = "BUYER_ARTICULATION"  # C3
    EVALUATION_LINKAGE = "EVALUATION_LINKAGE"  # C4


class CriterionFinding(Enum):
    """Finding for a single criterion (RC-13, IMPLEMENTATION).

    UNKNOWN is a legitimate finding, not an error (RC-05).
    """

    MET = "MET"
    NOT_MET = "NOT_MET"
    UNKNOWN = "UNKNOWN"


class ArticulationOrigin(Enum):
    """Who supplied the core prospective condition (RC-10, IMPLEMENTATION).

    Judged by the origin of the condition itself, not by who added detail.
    A seller-supplied condition to which the buyer only agreed, narrowed, or
    attached a threshold is SELLER_SEEDED. UNCERTAIN is used only when the
    evidence does not establish origin.
    """

    BUYER_GENERATED = "BUYER_GENERATED"
    SELLER_SEEDED = "SELLER_SEEDED"
    UNCERTAIN = "UNCERTAIN"


class BaecClassification(Enum):
    """Outcome of applying the four criteria and origin (RC-12, IMPLEMENTATION)."""

    CONFIRMED_BAEC = "CONFIRMED_BAEC"
    NOT_BAEC = "NOT_BAEC"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class ElicitationMode(Enum):
    """How the articulation came about (RC-11, IMPLEMENTATION).

    Recorded for transparency. Does not affect classification.
    """

    SPONTANEOUS = "SPONTANEOUS"
    CEE_ELICITED = "CEE_ELICITED"
    UNKNOWN = "UNKNOWN"


class ProvenanceCategory(Enum):
    """Where a piece of information came from (RC-32, IMPLEMENTATION)."""

    BUYER_FACT = "BUYER_FACT"
    SELLER_OBSERVATION = "SELLER_OBSERVATION"
    EXTERNAL_EVIDENCE = "EXTERNAL_EVIDENCE"
    AI_INFERENCE = "AI_INFERENCE"
    UNKNOWN = "UNKNOWN"


class ThresholdComparator(Enum):
    """How the buyer bounded a stated threshold (RC-18, IMPLEMENTATION).

    Each member mirrors buyer wording so that "more than 15%" can never be
    stored as "15% or more". QUALITATIVE_ONLY means the buyer used a word
    such as "significant" with no number. NONE_STATED means no threshold
    was given.
    """

    GREATER_THAN = "GREATER_THAN"
    AT_LEAST = "AT_LEAST"
    LESS_THAN = "LESS_THAN"
    AT_MOST = "AT_MOST"
    EXACTLY = "EXACTLY"
    APPROXIMATELY = "APPROXIMATELY"
    QUALITATIVE_ONLY = "QUALITATIVE_ONLY"
    NONE_STATED = "NONE_STATED"


class StalenessStatus(Enum):
    """Review currency of a stored BAEC (RC-29, IMPLEMENTATION).

    The manuscript says BAECs can go stale but establishes no expiration
    period. These labels and any intervals behind them are software settings.
    """

    CURRENT = "CURRENT"
    REVIEW_DUE = "REVIEW_DUE"
    STALE = "STALE"
    RETIRED = "RETIRED"


class ReviewAnswer(Enum):
    """A human's answer to a judgment question (RC-22, RC-28, IMPLEMENTATION).

    UNKNOWN means the human considered the question and could not determine
    the answer. NOT_YET means the human has not considered it.
    """

    YES = "YES"
    NO = "NO"
    UNKNOWN = "UNKNOWN"
    NOT_YET = "NOT_YET"


class AuthorizationAction(Enum):
    """What a human authorization applies to (RC-31, IMPLEMENTATION).

    Binding each authorization to one action and one subject prevents an
    approval given for one thing from being reused for another.
    """

    CONFIRM_BAEC = "CONFIRM_BAEC"
    RECORD_DORMANCY_JUDGMENT = "RECORD_DORMANCY_JUDGMENT"
    CHANGE_ACCOUNT_STATE = "CHANGE_ACCOUNT_STATE"


class ClassificationReasonKind(Enum):
    """Why a candidate was not classified CONFIRMED_BAEC (RC-13, IMPLEMENTATION).

    The first two lead to NOT_BAEC; the last two to INSUFFICIENT_EVIDENCE.
    """

    CRITERION_NOT_MET = "CRITERION_NOT_MET"
    ORIGIN_SELLER_SEEDED = "ORIGIN_SELLER_SEEDED"
    CRITERION_UNKNOWN = "CRITERION_UNKNOWN"
    ORIGIN_UNCERTAIN = "ORIGIN_UNCERTAIN"


class NoPlausiblePathGround(Enum):
    """Structured ground for No Plausible Path (RC-20, IMPLEMENTATION).

    A human managerial declaration. It is not cross-checked against other
    judgment objects; the accompanying human-written reason records the
    basis. OTHER exists so the three named grounds are not treated as
    exhaustive.
    """

    NO_PLAUSIBLE_BAEC = "NO_PLAUSIBLE_BAEC"
    CONDITION_TOO_IMPLAUSIBLE = "CONDITION_TOO_IMPLAUSIBLE"
    CLEARLY_SELLER_UNADDRESSABLE = "CLEARLY_SELLER_UNADDRESSABLE"
    OTHER = "OTHER"


class TransitionUnresolvedKind(Enum):
    """Items that did not block a transition but remain open (RC-22)."""

    ADDRESSABILITY_UNKNOWN = "ADDRESSABILITY_UNKNOWN"


class TransitionRejectionKind(Enum):
    """Why an account-state transition was refused (IMPLEMENTATION).

    Member order is the order in which rejections are reported.
    """

    SAME_STATE = "SAME_STATE"
    AUTHORIZATION_MISSING = "AUTHORIZATION_MISSING"
    AUTHORIZATION_WRONG_ACTION = "AUTHORIZATION_WRONG_ACTION"
    AUTHORIZATION_WRONG_SUBJECT = "AUTHORIZATION_WRONG_SUBJECT"
    AUTHORIZATION_WRONG_TARGET = "AUTHORIZATION_WRONG_TARGET"
    NON_EVALUATION_EVIDENCE_MISSING = "NON_EVALUATION_EVIDENCE_MISSING"
    NON_EVALUATION_EVIDENCE_WRONG_ACCOUNT = "NON_EVALUATION_EVIDENCE_WRONG_ACCOUNT"
    BAEC_MISSING = "BAEC_MISSING"
    BAEC_NOT_CONFIRMED = "BAEC_NOT_CONFIRMED"
    BAEC_WRONG_ACCOUNT = "BAEC_WRONG_ACCOUNT"
    BAEC_NOT_CURRENT = "BAEC_NOT_CURRENT"
    JUDGMENT_MISSING = "JUDGMENT_MISSING"
    JUDGMENT_WRONG_BAEC = "JUDGMENT_WRONG_BAEC"
    PLAUSIBILITY_NOT_YES = "PLAUSIBILITY_NOT_YES"
    ADDRESSABILITY_NO = "ADDRESSABILITY_NO"
    ADDRESSABILITY_NOT_YET = "ADDRESSABILITY_NOT_YET"
    EVALUATION_EVIDENCE_MISSING = "EVALUATION_EVIDENCE_MISSING"
    EVALUATION_EVIDENCE_WRONG_ACCOUNT = "EVALUATION_EVIDENCE_WRONG_ACCOUNT"
    GROUND_MISSING = "GROUND_MISSING"
    REASON_MISSING = "REASON_MISSING"
