"""Deterministic money calculation. No HTTP, ORM queries, or booking writes."""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from rest_framework.exceptions import ValidationError

from pricing.duration import MONTH_DAYS, POLICY_VERSION, WEEK_DAYS, rental_days

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
MAX_RATE = Decimal("9999999999.99")
MAX_AMOUNT = Decimal("9999999999999999.99")


def valid_money(value: Decimal, *, maximum: Decimal = MAX_RATE) -> bool:
    return (
        isinstance(value, Decimal)
        and value.is_finite()
        and ZERO <= value <= maximum
        and value == value.quantize(CENT)
    )


def money(value: Decimal) -> str:
    return format(value, ".2f")


@dataclass(frozen=True)
class RateCard:
    daily: Decimal
    weekly: Decimal | None
    monthly: Decimal | None
    currency: str

    def validate(self) -> None:
        if (
            not valid_money(self.daily)
            or any(
                value is not None and not valid_money(value)
                for value in (self.weekly, self.monthly)
            )
            or self.currency != "NPR"
        ):
            raise ValidationError(
                {"vehicle": ["Vehicle rental rates are not configured correctly."]}
            )


@dataclass(frozen=True)
class Discount:
    uuid: UUID
    label: str
    kind: str
    value: Decimal
    currency: str

    def amount(self, subtotal: Decimal, currency: str) -> Decimal:
        if (
            self.kind not in {"FIXED", "PERCENTAGE"}
            or not valid_money(self.value)
            or self.currency != currency
            or (self.kind == "PERCENTAGE" and self.value > 100)
        ):
            raise ValidationError(
                {"discount": ["An eligible discount is not configured correctly."]}
            )
        value = subtotal * self.value / 100 if self.kind == "PERCENTAGE" else self.value
        return min(subtotal, value.quantize(CENT, rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class PriceLine:
    pricing_type: str
    quantity: int
    unit_days: int
    unit_rate: Decimal
    start_date: date
    end_date: date

    @property
    def amount(self) -> Decimal:
        return self.unit_rate * self.quantity

    def as_dict(self) -> dict[str, Any]:
        return {
            "pricing_type": self.pricing_type,
            "quantity": self.quantity,
            "unit_days": self.unit_days,
            "rental_days": self.unit_days * self.quantity,
            "unit_rate": money(self.unit_rate),
            "amount": money(self.amount),
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
        }


@dataclass(frozen=True)
class PriceQuote:
    vehicle_uuid: UUID
    pickup_date: date
    return_date: date
    rental_days: int
    rates: RateCard
    lines: tuple[PriceLine, ...]
    subtotal: Decimal
    discount: Decimal
    applied_discount: Discount | None
    policy_version: str = POLICY_VERSION

    @property
    def total(self) -> Decimal:
        return self.subtotal - self.discount

    def discount_breakdown(self) -> list[dict[str, Any]]:
        item = self.applied_discount
        return (
            []
            if item is None
            else [
                {
                    "uuid": str(item.uuid),
                    "label": item.label,
                    "kind": item.kind,
                    "value": money(item.value),
                    "amount": money(self.discount),
                    "currency": item.currency,
                }
            ]
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "vehicle": str(self.vehicle_uuid),
            "pickup_date": self.pickup_date.isoformat(),
            "return_date": self.return_date.isoformat(),
            "rental_days": self.rental_days,
            "pricing_breakdown": [line.as_dict() for line in self.lines],
            "subtotal": money(self.subtotal),
            "discount": money(self.discount),
            "discount_breakdown": self.discount_breakdown(),
            "total": money(self.total),
            "currency": self.rates.currency,
            "pricing_version": self.policy_version,
        }


def calculate_quote(
    *,
    vehicle_uuid: UUID,
    rates: RateCard,
    pickup_date: date,
    return_date: date,
    discounts: tuple[Discount, ...] = (),
) -> PriceQuote:
    days = rental_days(pickup_date, return_date)
    rates.validate()
    # Exact coverage; unavailable optional tariffs are skipped, explicit zero is valid.
    packages = [
        (kind, length, rate)
        for kind, length, rate in (
            ("MONTHLY", MONTH_DAYS, rates.monthly),
            ("WEEKLY", WEEK_DAYS, rates.weekly),
            ("DAILY", 1, rates.daily),
        )
        if rate is not None
    ]
    # Dynamic programming avoids greedy errors with independent, non-proportional rates.
    # Ties use fewer bundles, then the longer final bundle (stable package order).
    costs: list[tuple[Decimal, int]] = [(ZERO, 0)]
    choices = [0]
    for day in range(1, days + 1):
        amount, count, index = min(
            (costs[day - length][0] + rate, costs[day - length][1] + 1, index)
            for index, (_, length, rate) in enumerate(packages)
            if length <= day
        )
        costs.append((amount, count))
        choices.append(index)
    quantities = [0] * len(packages)
    remaining = days
    while remaining:
        index = choices[remaining]
        quantities[index] += 1
        remaining -= packages[index][1]
    lines = []
    cursor = pickup_date
    for (kind, length, rate), quantity in zip(packages, quantities, strict=True):
        if quantity:
            end = cursor + timedelta(days=length * quantity)
            lines.append(PriceLine(kind, quantity, length, rate, cursor, end))
            cursor = end
    subtotal = sum((line.amount for line in lines), ZERO)
    if not valid_money(subtotal, maximum=MAX_AMOUNT):
        raise ValidationError({"return_date": ["Calculated amount exceeds the supported range."]})
    selected = None
    discount_amount = ZERO
    # Apply the best single eligible rule; equal discounts resolve by stable UUID.
    for candidate in sorted(discounts, key=lambda item: str(item.uuid)):
        amount = candidate.amount(subtotal, rates.currency)
        if amount > discount_amount:
            selected, discount_amount = candidate, amount
    return PriceQuote(
        vehicle_uuid,
        pickup_date,
        return_date,
        days,
        rates,
        tuple(lines),
        subtotal,
        discount_amount,
        selected,
    )
