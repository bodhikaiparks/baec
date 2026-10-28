"""Derived Measurements: deterministic Decimal arithmetic on Observations (Stage A Section 7.3).

Only the transformations the locked Stage B corpus defines exist here, each a
fixed Python function selected by a closed enum. There is no expression
evaluation and no AI arithmetic. The rounding rule is "none": a result that
cannot be represented exactly is refused rather than rounded, so no value can
be rounded across the buyer's threshold.

A Derived Measurement never changes the buyer's threshold, comparator, or
unit; it only produces a value to compare with them. Each input holds the
Observation it was taken from, so a measurement belongs to exactly the plan
version of its inputs and cannot be built from Observations of different
versions. Whether it may support a
finding is decided by the evidence ledger (classification.py), which treats a
measurement with a superseded or retracted input as no longer usable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, Inexact, localcontext
from enum import Enum

from .domain import Observation, plan_key
from .errors import DerivedMeasurementError, Engine2ValidationError

ROUNDING_NONE = "none: exact decimal arithmetic"
OUTPUT_UNIT = "%"


class Transformation(Enum):
    """The closed set of transformations used by the locked corpus. Value = identifier."""

    PERCENT_CHANGE_FROM_TWO_PRICES = "corpus.percent_change_from_two_prices"
    BASIS_POINTS_TO_PERCENT = "corpus.basis_points_to_percent"

    @property
    def version(self) -> str:
        return "1"

    @property
    def formula(self) -> str:
        return _FORMULAS[self]


_FORMULAS = {
    Transformation.PERCENT_CHANGE_FROM_TWO_PRICES: "(new_price - baseline_price) / baseline_price * 100",
    Transformation.BASIS_POINTS_TO_PERCENT: "basis_points / 100",
}
_QUANTITIES = {
    Transformation.PERCENT_CHANGE_FROM_TWO_PRICES: ("baseline_price", "new_price"),
    Transformation.BASIS_POINTS_TO_PERCENT: ("basis_points",),
}


@dataclass(frozen=True)
class MeasurementInput:
    """One exact input value, taken from an Observation and verified by a human."""

    observation: Observation
    quantity: str
    value: Decimal
    unit: str

    def __post_init__(self) -> None:
        if not isinstance(self.observation, Observation):
            raise Engine2ValidationError("MeasurementInput.observation must be an Observation")
        for name in ("quantity", "unit"):
            text = getattr(self, name)
            if not isinstance(text, str) or not text.strip():
                raise Engine2ValidationError(f"MeasurementInput.{name} must be a non-empty string")
        if not isinstance(self.value, Decimal) or not self.value.is_finite():
            raise Engine2ValidationError("MeasurementInput.value must be a finite Decimal")

    @property
    def observation_id(self) -> str:
        return self.observation.observation_id


def _exact(operation) -> Decimal:
    with localcontext() as context:
        context.prec = 60
        context.traps[Inexact] = True
        try:
            return operation()
        except Inexact as error:
            raise DerivedMeasurementError(
                "result is not exactly representable; the rounding rule 'none' forbids rounding"
            ) from error


def _apply(transformation: Transformation, inputs: tuple[MeasurementInput, ...]) -> Decimal:
    by_quantity = {i.quantity: i for i in inputs}
    expected = _QUANTITIES[transformation]
    if len(inputs) != len(expected) or set(by_quantity) != set(expected):
        raise DerivedMeasurementError(f"{transformation.value} requires exactly the inputs {expected}")
    if transformation is Transformation.PERCENT_CHANGE_FROM_TWO_PRICES:
        baseline, new = by_quantity["baseline_price"], by_quantity["new_price"]
        if baseline.unit != new.unit:
            raise DerivedMeasurementError("baseline and new price must be in the same unit")
        if baseline.value <= 0:
            raise DerivedMeasurementError("baseline price must be positive")
        return _exact(lambda: (new.value - baseline.value) / baseline.value * 100)
    basis_points = by_quantity["basis_points"]
    if basis_points.unit != "basis points":
        raise DerivedMeasurementError("basis_points input must be in basis points")
    return _exact(lambda: basis_points.value / 100)


@dataclass(frozen=True)
class DerivedMeasurement:
    """A reproducible value computed from Observations. Validated by recomputation."""

    measurement_id: str
    transformation: Transformation
    inputs: tuple[MeasurementInput, ...]
    output_value: Decimal
    calculated_at: datetime
    rounding_rule: str = ROUNDING_NONE
    output_unit: str = OUTPUT_UNIT

    def __post_init__(self) -> None:
        if not isinstance(self.measurement_id, str) or not self.measurement_id.strip():
            raise Engine2ValidationError("DerivedMeasurement.measurement_id must be a non-empty string")
        if not isinstance(self.transformation, Transformation):
            raise Engine2ValidationError("DerivedMeasurement.transformation must be a Transformation")
        if not isinstance(self.inputs, tuple) or not self.inputs or not all(isinstance(i, MeasurementInput) for i in self.inputs):
            raise Engine2ValidationError("DerivedMeasurement.inputs must be a non-empty tuple of MeasurementInput")
        if len({plan_key(i.observation) for i in self.inputs}) != 1:
            raise Engine2ValidationError("DerivedMeasurement inputs come from different plan versions; plan versions never mix")
        if not isinstance(self.calculated_at, datetime) or self.calculated_at.utcoffset() is None:
            raise Engine2ValidationError("DerivedMeasurement.calculated_at must be timezone-aware")
        if self.rounding_rule != ROUNDING_NONE or self.output_unit != OUTPUT_UNIT:
            raise Engine2ValidationError("only exact arithmetic with a percent output is defined")
        if not isinstance(self.output_value, Decimal) or self.output_value != _apply(self.transformation, self.inputs):
            raise Engine2ValidationError("DerivedMeasurement.output_value does not equal the deterministic result")

    @property
    def source_observation_ids(self) -> frozenset[str]:
        return frozenset(i.observation_id for i in self.inputs)

    @property
    def monitoring_plan_id(self) -> str:
        return self.inputs[0].observation.monitoring_plan_id

    @property
    def plan_version(self) -> int:
        """Derived from the inputs, which are validated to share one exact plan version."""
        return self.inputs[0].observation.plan_version

    @property
    def transformation_version(self) -> str:
        return self.transformation.version

    @property
    def formula(self) -> str:
        return self.transformation.formula


def compute_derived_measurement(
    measurement_id: str,
    transformation: Transformation,
    inputs: tuple[MeasurementInput, ...],
    calculated_at: datetime,
) -> DerivedMeasurement:
    """Compute a Derived Measurement with exact Decimal arithmetic (no AI, no rounding)."""
    if not isinstance(transformation, Transformation):
        raise Engine2ValidationError("transformation must be a Transformation")
    return DerivedMeasurement(
        measurement_id=measurement_id,
        transformation=transformation,
        inputs=inputs,
        output_value=_apply(transformation, inputs),
        calculated_at=calculated_at,
    )
