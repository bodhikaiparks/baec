"""Typed failures for the Engine 2 domain core.

Every Engine 2 rule violation raises one of these. Nothing is silently
corrected: an invalid object, a refused source, or an inconsistent review is
an error, never a default value.
"""


class Engine2Error(ValueError):
    """Base class for every Engine 2 domain failure."""


class Engine2ValidationError(Engine2Error):
    """A domain object would break a locked Stage A or Stage B rule."""


class MonitoringPlanActivationError(Engine2Error):
    """A Monitoring Plan cannot become active (Stage A Section 4.3, fail closed).

    Raised when Engine 1 currentness is missing, unreadable, not CURRENT, or
    does not match the plan. No Observation can be captured without an active
    plan, so processing stops before correspondence classification.
    """


class SourceNotAuthorizedError(Engine2Error):
    """A source item is not from an Authorized Source of the active plan.

    Raised before any Observation exists (Stage A Sections 6 and 7.1). The
    item never becomes evidence.
    """


class DerivedMeasurementError(Engine2Error):
    """A Derived Measurement cannot be computed exactly (Stage A Section 7.3)."""


class ReviewInconsistentError(Engine2ValidationError):
    """A Human Correspondence Review is internally inconsistent and is refused."""
