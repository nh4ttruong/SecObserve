from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.utils import timezone

from application.access_control.models import User
from application.core.models import Product
from application.rules.models import Rule, Rule_Simulation
from application.rules.services.simulator import (
    SimulationCancelled,
    SimulationScope,
    build_simulation_queryset,
    get_rule_definition_hash,
    simulate_rule,
)
from application.rules.types import Rule_Simulation_Status


class SimulationBusy(Exception):
    pass


class SimulationLimitExceeded(Exception):
    def __init__(self, candidate_count: int, maximum_candidates: int) -> None:
        self.candidate_count = candidate_count
        self.maximum_candidates = maximum_candidates
        super().__init__(
            f"Simulation has {candidate_count} candidates, exceeding the configured limit of {maximum_candidates}. "
            "Narrow the scope by product, parser, or scanner prefix."
        )


class InvalidSimulationScope(Exception):
    pass


class RuleDefinitionChanged(Exception):
    pass


def create_rule_simulation(rule: Rule, user: User, scope: SimulationScope) -> Rule_Simulation:
    _validate_scope(rule, scope)
    _check_concurrency_limit()

    candidate_count = build_simulation_queryset(rule, scope).count()
    _check_candidate_limit(candidate_count)

    return Rule_Simulation.objects.create(
        rule=rule,
        user=user,
        products=list(scope.product_ids),
        parser_id=scope.parser_id,
        scanner_prefix=scope.scanner_prefix,
        candidate_count=candidate_count,
        rule_definition_hash=get_rule_definition_hash(rule),
    )


def execute_rule_simulation(simulation_id: str) -> None:
    simulation = Rule_Simulation.objects.select_related("rule", "rule__product").get(pk=simulation_id)
    if simulation.status != Rule_Simulation_Status.STATUS_QUEUED:
        return

    try:
        simulation.rule = _get_unchanged_rule(simulation)
        scope = SimulationScope(
            product_ids=tuple(simulation.products),
            parser_id=simulation.parser_id,
            scanner_prefix=simulation.scanner_prefix,
        )
        candidate_count = build_simulation_queryset(simulation.rule, scope).count()
        _check_candidate_limit(candidate_count)

        transitioned = Rule_Simulation.objects.filter(
            pk=simulation.pk,
            status=Rule_Simulation_Status.STATUS_QUEUED,
        ).update(
            status=Rule_Simulation_Status.STATUS_RUNNING,
            candidate_count=candidate_count,
            processed_count=0,
            started=timezone.now(),
            error_message="",
        )
        if not transitioned:
            # A cancellation may arrive while the candidate count is running.
            return

        match_count, observations = simulate_rule(
            simulation.rule,
            scope=scope,
            progress_callback=lambda processed: _update_progress(simulation.pk, processed),
            is_cancelled=lambda: _is_cancelled(simulation.pk),
        )

        _get_unchanged_rule(simulation)
        Rule_Simulation.objects.filter(
            pk=simulation.pk,
            status=Rule_Simulation_Status.STATUS_RUNNING,
        ).update(
            status=Rule_Simulation_Status.STATUS_COMPLETED,
            match_count=match_count,
            result_observation_ids=[observation.pk for observation in observations],
            finished=timezone.now(),
        )
    except SimulationCancelled:
        Rule_Simulation.objects.filter(
            pk=simulation.pk,
            status__in=Rule_Simulation_Status.ACTIVE_STATUSES,
        ).update(
            status=Rule_Simulation_Status.STATUS_CANCELLED,
            finished=timezone.now(),
        )
    except Exception as exception:
        mark_rule_simulation_failed(simulation, exception)
        raise


def cancel_rule_simulation(simulation: Rule_Simulation) -> None:
    if simulation.status in Rule_Simulation_Status.ACTIVE_STATUSES:
        simulation.status = Rule_Simulation_Status.STATUS_CANCELLED
        simulation.finished = timezone.now()
        simulation.save(update_fields=["status", "finished"])


def mark_rule_simulation_failed(simulation: Rule_Simulation, exception: Exception) -> None:
    Rule_Simulation.objects.filter(
        pk=simulation.pk,
        status__in=Rule_Simulation_Status.ACTIVE_STATUSES,
    ).update(
        status=Rule_Simulation_Status.STATUS_FAILED,
        error_message=str(exception)[:2048],
        finished=timezone.now(),
    )


def delete_expired_rule_simulations() -> int:
    cutoff = timezone.now() - timedelta(days=settings.RULE_SIMULATION_RETENTION_DAYS)
    expired_simulations = Rule_Simulation.objects.filter(
        status__in=Rule_Simulation_Status.TERMINAL_STATUSES,
        finished__lt=cutoff,
    )
    deleted_count, _ = expired_simulations.delete()
    return deleted_count


def _validate_scope(rule: Rule, scope: SimulationScope) -> None:
    if not scope.product_ids:
        return

    requested_product_ids = set(scope.product_ids)
    products = Product.objects.filter(pk__in=requested_product_ids, is_product_group=False)

    if rule.product:
        if rule.product.is_product_group:
            products = products.filter(product_group=rule.product)
        else:
            products = products.filter(pk=rule.product.pk)
    else:
        products = products.filter(apply_general_rules=True)

    if set(products.values_list("pk", flat=True)) != requested_product_ids:
        raise InvalidSimulationScope("One or more products are outside the rule scope")


def _check_concurrency_limit() -> None:
    maximum_concurrent = settings.RULE_SIMULATION_MAX_CONCURRENT
    if maximum_concurrent <= 0:
        return

    active_count = Rule_Simulation.objects.filter(status__in=Rule_Simulation_Status.ACTIVE_STATUSES).count()
    if active_count >= maximum_concurrent:
        raise SimulationBusy("The maximum number of concurrent rule simulations is already running")


def _check_candidate_limit(candidate_count: int) -> None:
    maximum_candidates = settings.RULE_SIMULATION_MAX_CANDIDATES
    if 0 < maximum_candidates < candidate_count:
        raise SimulationLimitExceeded(candidate_count, maximum_candidates)


def _get_unchanged_rule(simulation: Rule_Simulation) -> Rule:
    current_rule = Rule.objects.select_related("product").get(pk=simulation.rule_id)
    if get_rule_definition_hash(current_rule) != simulation.rule_definition_hash:
        raise RuleDefinitionChanged("The rule changed while its simulation was queued or running")
    return current_rule


def _update_progress(simulation_id: UUID, processed_count: int) -> None:
    Rule_Simulation.objects.filter(
        pk=simulation_id,
        status=Rule_Simulation_Status.STATUS_RUNNING,
    ).update(processed_count=processed_count)


def _is_cancelled(simulation_id: UUID) -> bool:
    return Rule_Simulation.objects.filter(
        pk=simulation_id,
        status=Rule_Simulation_Status.STATUS_CANCELLED,
    ).exists()
