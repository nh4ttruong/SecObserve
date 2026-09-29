import random
from datetime import timedelta
from typing import Optional

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from application.commons.models import Settings
from application.core.models import Branch, Observation, Product
from application.core.types import Severity, Status
from application.import_observations.models import Parser
from application.licenses.models import License_Component
from application.licenses.types import License_Policy_Evaluation_Result
from application.metrics.models import (
    Product_License_Metrics,
    Product_Metrics,
    Product_Metrics_Status,
)
from application.metrics.services.metrics import (
    LICENSE_COUNTS,
    OBSERVATION_COUNTS,
    calculate_product_metrics,
)
from unittests.base_test_case import BaseTestCase

# The test seeds its own data instead of loading fixtures, so that it runs on every database backend.

SEVERITIES = [choice[0] for choice in Severity.SEVERITY_CHOICES]
STATUSES = [choice[0] for choice in Status.STATUS_CHOICES]
EVALUATION_RESULTS = [choice[0] for choice in License_Policy_Evaluation_Result.RESULT_CHOICES]
METRICS_HISTORIES = [
    [],
    [(0, True)],
    [(0, False)],
    [(-1, True)],
    [(-1, False)],
    [(-5, False), (-3, True)],
    [(-2, None)],
    [(1, True)],
    [(1, False)],
    [(0, False), (1, True)],
]


class TestCalculateProductMetricsEquivalence(BaseTestCase):
    """The set-based calculation gives the same metrics as the calculation per product it replaces."""

    def test_equivalence(self):
        random_generator = random.Random(4711)
        self._seed(random_generator)

        for _ in range(3):
            reference_result = self._run(_reference_calculate_product_metrics, rollback=True)
            result = self._run(calculate_product_metrics, rollback=False)

            self.assertEqual(reference_result, result)
            self._change(random_generator)

    def _run(self, calculate, rollback: bool) -> tuple:
        with transaction.atomic():
            message = calculate()
            result = (
                message,
                {
                    (metrics.product_id, metrics.date): [
                        getattr(metrics, field) for field in [*OBSERVATION_COUNTS, "last_observation_change"]
                    ]
                    for metrics in Product_Metrics.objects.all()
                },
                {
                    (metrics.product_id, metrics.date): [
                        getattr(metrics, field) for field in [*LICENSE_COUNTS, "last_license_change"]
                    ]
                    for metrics in Product_License_Metrics.objects.all()
                },
            )
            transaction.set_rollback(rollback)
        return result

    def _seed(self, random_generator: random.Random) -> None:
        self.parser = Parser.objects.create(name="equivalence_parser")
        product_groups = [Product.objects.create(name=f"group_{i}", is_product_group=True) for i in range(2)]
        today = timezone.localdate()

        for i in range(40):
            product = Product.objects.create(
                name=f"product_{i}", product_group=random_generator.choice([None, *product_groups])
            )
            branches: list[Optional[Branch]] = [
                Branch.objects.create(product=product, name=f"branch_{j}", is_default_branch=(j == 0 and i % 3 != 0))
                for j in range(i % 4)
            ]
            product.refresh_from_db()
            if i % 5 != 4:
                self._create_observations(random_generator, product, [None, *branches])
                self._create_license_components(random_generator, product, [None, *branches])

            # Metrics of previous runs, as (days from today, calculated for the current change) per row
            for metrics_model, counts, change_field in (
                (Product_Metrics, OBSERVATION_COUNTS, "last_observation_change"),
                (Product_License_Metrics, LICENSE_COUNTS, "last_license_change"),
            ):
                for days, current in random_generator.choice(METRICS_HISTORIES):
                    change = getattr(product, change_field)
                    metrics_model.objects.create(
                        product=product,
                        date=today + timedelta(days=days),
                        **{change_field: None if current is None else change - timedelta(seconds=0 if current else 1)},
                        **{field: random_generator.randrange(5) for field in counts},
                    )

    def _create_observations(
        self, random_generator: random.Random, product: Product, branches: list[Optional[Branch]]
    ) -> None:
        Observation.objects.bulk_create(
            [
                Observation(
                    title=f"observation_{product.pk}_{i}",
                    product=product,
                    branch=random_generator.choice(branches),
                    parser=self.parser,
                    current_severity=random_generator.choice(SEVERITIES),
                    numerical_severity=1,
                    current_status=random_generator.choice(STATUSES),
                    import_last_seen=timezone.now(),
                    identity_hash=f"{product.pk}_{i}",
                )
                for i in range(random_generator.randrange(40))
            ]
        )

    def _create_license_components(
        self, random_generator: random.Random, product: Product, branches: list[Optional[Branch]]
    ) -> None:
        License_Component.objects.bulk_create(
            [
                License_Component(
                    identity_hash=f"{product.pk}_{i}",
                    product=product,
                    branch=random_generator.choice(branches),
                    component_name=f"component_{i}",
                    component_name_version=f"component_{i}",
                    evaluation_result=(evaluation_result := random_generator.choice(EVALUATION_RESULTS)),
                    numerical_evaluation_result=License_Policy_Evaluation_Result.NUMERICAL_RESULTS[evaluation_result],
                )
                for i in range(random_generator.randrange(15))
            ]
        )

    def _change(self, random_generator: random.Random) -> None:
        for branch in Branch.objects.filter(is_default_branch=False):
            if random_generator.random() < 0.2:
                branch.is_default_branch = True
                branch.save()

        for product in Product.objects.filter(is_product_group=False):
            if random_generator.random() < 0.3:
                self._create_observations(random_generator, product, [None, *product.branch_set.all()])
                product.last_observation_change = timezone.now()
            if random_generator.random() < 0.3:
                self._create_license_components(random_generator, product, [None, *product.branch_set.all()])
                product.last_license_change = timezone.now()
            product.save()


# The calculation per product, before the set-based queries


def _reference_calculate_product_metrics() -> str:
    settings = Settings.load()
    today = timezone.localdate()

    # Metrics of today are up to date if they have been calculated for the current change of the product
    todays_observation_changes = dict(
        Product_Metrics.objects.filter(date=today).values_list("product_id", "last_observation_change")
    )
    todays_license_changes = dict(
        Product_License_Metrics.objects.filter(date=today).values_list("product_id", "last_license_change")
    )

    num_products = 0
    for product in Product.objects.filter(is_product_group=False):
        observations_changed = todays_observation_changes.get(product.pk) != product.last_observation_change
        licenses_changed = todays_license_changes.get(product.pk) != product.last_license_change
        observation_metrics_calculated = observations_changed and _reference_calculate_observation_metrics_for_product(
            product
        )
        license_metrics_calculated = (
            settings.feature_license_management
            and licenses_changed
            and _reference_calculate_license_metrics_for_product(product)
        )
        num_products += bool(observation_metrics_calculated or license_metrics_calculated)

    product_metrics_status = Product_Metrics_Status.load()
    product_metrics_status.last_calculated = timezone.now()
    product_metrics_status.save()

    if num_products == 1:
        return "Calculated metrics for 1 product."

    return f"Calculated metrics for {num_products} products."


def _reference_calculate_observation_metrics_for_product(  # pylint: disable=too-many-branches
    product: Product,
) -> bool:
    # There are quite a lot of branches, but at least they are not nested too much

    metrics_calculated = False
    today = timezone.localdate()

    latest_product_metrics = _reference_get_latest_product_observation_metrics(product)

    if latest_product_metrics and latest_product_metrics.last_observation_change == product.last_observation_change:
        # No relevant changes of observations since the latest metrics, but we might need to update the metrics
        # if there are no metrics for today or previous days.
        iteration_date = latest_product_metrics.date + timedelta(days=1)
        while iteration_date <= today:
            Product_Metrics.objects.create(
                product=product,
                date=iteration_date,
                active_critical=latest_product_metrics.active_critical,
                active_high=latest_product_metrics.active_high,
                active_medium=latest_product_metrics.active_medium,
                active_low=latest_product_metrics.active_low,
                active_none=latest_product_metrics.active_none,
                active_unknown=latest_product_metrics.active_unknown,
                open=latest_product_metrics.open,
                affected=latest_product_metrics.affected,
                resolved=latest_product_metrics.resolved,
                duplicate=latest_product_metrics.duplicate,
                false_positive=latest_product_metrics.false_positive,
                in_review=latest_product_metrics.in_review,
                not_affected=latest_product_metrics.not_affected,
                not_security=latest_product_metrics.not_security,
                risk_accepted=latest_product_metrics.risk_accepted,
                last_observation_change=latest_product_metrics.last_observation_change,
            )
            iteration_date += timedelta(days=1)
            metrics_calculated = True
    else:
        # Either there are relevant changes of observations since the latest metrics or there are no metrics
        # yet at all, so we need to calculate the metrics for today.
        observation_metrics = Observation.objects.filter(
            product=product,
            branch=product.repository_default_branch,
        ).aggregate(
            active_critical=Count(
                "pk",
                filter=Q(current_status__in=Status.STATUS_ACTIVE, current_severity=Severity.SEVERITY_CRITICAL),
            ),
            active_high=Count(
                "pk",
                filter=Q(current_status__in=Status.STATUS_ACTIVE, current_severity=Severity.SEVERITY_HIGH),
            ),
            active_medium=Count(
                "pk",
                filter=Q(current_status__in=Status.STATUS_ACTIVE, current_severity=Severity.SEVERITY_MEDIUM),
            ),
            active_low=Count(
                "pk",
                filter=Q(current_status__in=Status.STATUS_ACTIVE, current_severity=Severity.SEVERITY_LOW),
            ),
            active_none=Count(
                "pk",
                filter=Q(current_status__in=Status.STATUS_ACTIVE, current_severity=Severity.SEVERITY_NONE),
            ),
            active_unknown=Count(
                "pk",
                filter=Q(current_status__in=Status.STATUS_ACTIVE, current_severity=Severity.SEVERITY_UNKNOWN),
            ),
            open=Count("pk", filter=Q(current_status=Status.STATUS_OPEN)),
            affected=Count("pk", filter=Q(current_status=Status.STATUS_AFFECTED)),
            resolved=Count("pk", filter=Q(current_status=Status.STATUS_RESOLVED)),
            duplicate=Count("pk", filter=Q(current_status=Status.STATUS_DUPLICATE)),
            false_positive=Count("pk", filter=Q(current_status=Status.STATUS_FALSE_POSITIVE)),
            in_review=Count("pk", filter=Q(current_status=Status.STATUS_IN_REVIEW)),
            not_affected=Count("pk", filter=Q(current_status=Status.STATUS_NOT_AFFECTED)),
            not_security=Count("pk", filter=Q(current_status=Status.STATUS_NOT_SECURITY)),
            risk_accepted=Count("pk", filter=Q(current_status=Status.STATUS_RISK_ACCEPTED)),
        )

        Product_Metrics.objects.update_or_create(
            product=product,
            date=today,
            defaults=observation_metrics | {"last_observation_change": product.last_observation_change},
        )
        metrics_calculated = True

    return metrics_calculated


def _reference_calculate_license_metrics_for_product(  # pylint: disable=too-many-branches
    product: Product,
) -> bool:
    # There are quite a lot of branches, but at least they are not nested too much

    metrics_calculated = False
    today = timezone.localdate()

    latest_product_license_metrics = _reference_get_latest_product_license_metrics(product)

    if (
        latest_product_license_metrics
        and latest_product_license_metrics.last_license_change == product.last_license_change
    ):
        # No relevant changes of licenses since the latest metrics, but we might need to update the metrics
        # if there are no metrics for today or previous days.
        iteration_date = latest_product_license_metrics.date + timedelta(days=1)
        while iteration_date <= today:
            Product_License_Metrics.objects.create(
                product=product,
                date=iteration_date,
                allowed=latest_product_license_metrics.allowed,
                forbidden=latest_product_license_metrics.forbidden,
                ignored=latest_product_license_metrics.ignored,
                review_required=latest_product_license_metrics.review_required,
                unknown=latest_product_license_metrics.unknown,
                last_license_change=latest_product_license_metrics.last_license_change,
            )
            iteration_date += timedelta(days=1)
            metrics_calculated = True
    else:
        # Either there are relevant changes of licenses since the latest metrics or there are no metrics
        # yet at all, so we need to calculate the metrics for today.
        license_metrics = License_Component.objects.filter(
            product=product,
            branch=product.repository_default_branch,
        ).aggregate(
            allowed=Count(
                "pk",
                filter=Q(evaluation_result=License_Policy_Evaluation_Result.RESULT_ALLOWED),
            ),
            forbidden=Count(
                "pk",
                filter=Q(evaluation_result=License_Policy_Evaluation_Result.RESULT_FORBIDDEN),
            ),
            ignored=Count(
                "pk",
                filter=Q(evaluation_result=License_Policy_Evaluation_Result.RESULT_IGNORED),
            ),
            review_required=Count(
                "pk",
                filter=Q(evaluation_result=License_Policy_Evaluation_Result.RESULT_REVIEW_REQUIRED),
            ),
            unknown=Count(
                "pk",
                filter=Q(evaluation_result=License_Policy_Evaluation_Result.RESULT_UNKNOWN),
            ),
        )

        Product_License_Metrics.objects.update_or_create(
            product=product,
            date=today,
            defaults=license_metrics | {"last_license_change": product.last_license_change},
        )
        metrics_calculated = True

    return metrics_calculated


def _reference_get_latest_product_observation_metrics(product: Product) -> Optional[Product_Metrics]:
    try:
        return Product_Metrics.objects.filter(product=product).latest("date")
    except Product_Metrics.DoesNotExist:
        return None


def _reference_get_latest_product_license_metrics(product: Product) -> Optional[Product_License_Metrics]:
    try:
        return Product_License_Metrics.objects.filter(product=product).latest("date")
    except Product_License_Metrics.DoesNotExist:
        return None
