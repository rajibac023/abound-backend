from datetime import date, datetime, timedelta
from decimal import Decimal
from random import Random
from uuid import UUID, uuid4

from django.test import SimpleTestCase, override_settings
from rest_framework.exceptions import ValidationError

from pricing.duration import rental_days
from pricing.engine import Discount, RateCard, calculate_quote


class PricingEngineTests(SimpleTestCase):
    def quote(
        self, days=1, *, daily="2000.00", weekly="12000.00", monthly="45000.00", discounts=()
    ):
        return calculate_quote(
            vehicle_uuid=uuid4(),
            pickup_date=date(2028, 1, 1),
            return_date=date(2028, 1, 1) + timedelta(days=days),
            rates=RateCard(
                Decimal(daily),
                Decimal(weekly) if weekly is not None else None,
                Decimal(monthly) if monthly is not None else None,
                "NPR",
            ),
            discounts=discounts,
        )

    def test_one_day_and_multiple_daily_days(self) -> None:
        for days, total in ((1, "2000.00"), (3, "6000.00"), (6, "12000.00")):
            quote = self.quote(days)
            self.assertEqual(quote.rental_days, days)
            self.assertEqual(quote.total, Decimal(total))
            self.assertEqual(quote.lines[0].pricing_type, "DAILY")
            self.assertEqual(quote.lines[0].quantity, days)

    def test_weekly_boundaries_and_daily_remainder(self) -> None:
        for days, total in ((7, "12000.00"), (8, "14000.00"), (14, "24000.00"), (29, "50000.00")):
            self.assertEqual(self.quote(days).total, Decimal(total))
        self.assertEqual(
            [(line.pricing_type, line.quantity) for line in self.quote(8).lines],
            [("WEEKLY", 1), ("DAILY", 1)],
        )

    def test_monthly_boundaries_and_mixed_bundles(self) -> None:
        for days, total in (
            (30, "45000.00"),
            (31, "47000.00"),
            (37, "57000.00"),
            (60, "90000.00"),
            (68, "104000.00"),
        ):
            self.assertEqual(self.quote(days).total, Decimal(total))
        self.assertEqual(
            [line.pricing_type for line in self.quote(68).lines], ["MONTHLY", "WEEKLY", "DAILY"]
        )

    def test_calendar_month_and_dst_do_not_change_day_units(self) -> None:
        cases = (
            (date(2028, 2, 28), date(2028, 3, 1), 2),
            (date(2027, 2, 28), date(2027, 3, 1), 1),
            (date(2028, 1, 31), date(2028, 2, 1), 1),
            (date(2028, 12, 31), date(2029, 1, 1), 1),
            (date(2026, 3, 7), date(2026, 3, 9), 2),
        )
        for pickup, returned, days in cases:
            with override_settings(TIME_ZONE="America/New_York"):
                self.assertEqual(rental_days(pickup, returned), days)
        self.assertEqual(rental_days(date(2028, 2, 1), date(2028, 3, 1)), 29)

    def test_invalid_date_ranges_and_timestamps(self) -> None:
        for start, end in (
            (date(2028, 1, 1), date(2028, 1, 1)),
            (date(2028, 2, 1), date(2028, 1, 1)),
            (datetime(2028, 1, 1), date(2028, 1, 2)),
            ("2028-01-01", date(2028, 1, 2)),
        ):
            with self.assertRaises(ValidationError):
                rental_days(start, end)  # type: ignore[arg-type]  # Exercise invalid runtime input.

    @override_settings(PRICING_MAX_RENTAL_DAYS=30)
    def test_duration_limit_is_shared_and_inclusive(self) -> None:
        self.assertEqual(self.quote(30).rental_days, 30)
        with self.assertRaises(ValidationError):
            self.quote(31)

    def test_missing_optional_rates_fall_back_without_becoming_free(self) -> None:
        self.assertEqual(self.quote(30, weekly=None, monthly=None).total, Decimal("60000.00"))
        self.assertEqual(self.quote(30, monthly=None).total, Decimal("52000.00"))
        self.assertEqual(self.quote(7, weekly="0").total, Decimal("0.00"))
        self.assertEqual(self.quote(1, daily="0").total, Decimal("0.00"))

    def test_independent_rates_do_not_force_more_expensive_bundles(self) -> None:
        self.assertEqual(self.quote(7, weekly="20000").total, Decimal("14000.00"))
        self.assertEqual(self.quote(30, monthly="90000").total, Decimal("52000.00"))
        # Greedily using a month would cost 60 + four days (40), instead of five weeks (75).
        quote = self.quote(35, daily="10", weekly="15", monthly="60")
        self.assertEqual(quote.total, Decimal("75.00"))
        self.assertEqual(
            [(line.pricing_type, line.quantity) for line in quote.lines], [("WEEKLY", 5)]
        )

    def test_exact_coverage_never_buys_a_longer_unused_period(self) -> None:
        self.assertEqual(self.quote(6, weekly="1").total, Decimal("12000.00"))
        self.assertEqual(self.quote(29, monthly="1").total, Decimal("50000.00"))

    def test_optimizer_matches_independent_exhaustive_bundle_enumeration(self) -> None:
        random = Random(615)
        for _ in range(12):
            daily, weekly, monthly = (Decimal(random.randrange(0, 10000)) / 100 for _ in range(3))
            for days in range(1, 91):
                candidates = [
                    months * monthly + weeks * weekly + (days - months * 30 - weeks * 7) * daily
                    for months in range(days // 30 + 1)
                    for weeks in range((days - months * 30) // 7 + 1)
                ]
                quote = self.quote(days, daily=str(daily), weekly=str(weekly), monthly=str(monthly))
                self.assertEqual(quote.subtotal, min(candidates))
                self.assertEqual(sum(line.unit_days * line.quantity for line in quote.lines), days)
                self.assertEqual(sum(line.amount for line in quote.lines), quote.subtotal)
                self.assertEqual(quote.lines[0].start_date, quote.pickup_date)
                self.assertEqual(quote.lines[-1].end_date, quote.return_date)
                for first, second in zip(quote.lines, quote.lines[1:], strict=False):
                    self.assertEqual(first.end_date, second.start_date)

    def test_zero_discount_and_percentage_rounding_half_up(self) -> None:
        self.assertEqual(self.quote().discount_breakdown(), [])
        rule = Discount(uuid4(), "Half off", "PERCENTAGE", Decimal("50.00"), "NPR")
        quote = self.quote(daily="0.05", discounts=(rule,))
        self.assertEqual(quote.discount, Decimal("0.03"))
        self.assertEqual(quote.total, Decimal("0.02"))
        self.assertEqual(quote.as_dict()["total"], "0.02")

    def test_fixed_discount_is_capped_and_never_stacked(self) -> None:
        fixed = Discount(uuid4(), "Fixed", "FIXED", Decimal("5000"), "NPR")
        percentage = Discount(uuid4(), "Percentage", "PERCENTAGE", Decimal("50"), "NPR")
        quote = self.quote(discounts=(percentage, fixed))
        self.assertEqual(quote.total, Decimal("0.00"))
        self.assertEqual(quote.discount, quote.subtotal)
        self.assertEqual(len(quote.discount_breakdown()), 1)
        self.assertEqual(quote.applied_discount, fixed)

    def test_discount_selection_is_deterministic_on_equal_savings(self) -> None:
        first = Discount(UUID(int=1), "First", "FIXED", Decimal("1000"), "NPR")
        second = Discount(UUID(int=2), "Second", "PERCENTAGE", Decimal("50"), "NPR")
        for rules in ((first, second), (second, first)):
            self.assertEqual(self.quote(discounts=rules).applied_discount, first)

    def test_invalid_rates_and_discounts_fail_in_service_not_only_serializer(self) -> None:
        for value in ("NaN", "Infinity", "-1", "0.001", "10000000000"):
            with self.assertRaises(ValidationError):
                self.quote(daily=value)
        for kind, value, currency in (
            ("FIXED", "-1", "NPR"),
            ("PERCENTAGE", "100.01", "NPR"),
            ("FIXED", "NaN", "NPR"),
            ("OTHER", "1", "NPR"),
            ("FIXED", "1", "USD"),
        ):
            with self.assertRaises(ValidationError):
                self.quote(
                    discounts=(Discount(uuid4(), "Invalid", kind, Decimal(value), currency),)
                )

    def test_large_amounts_and_decimal_strings_are_exact(self) -> None:
        quote = self.quote(3660, daily="9999999999.99", weekly=None, monthly=None)
        self.assertEqual(quote.as_dict()["total"], "36599999999963.40")
        self.assertEqual(self.quote(3, daily="0.10").as_dict()["total"], "0.30")
