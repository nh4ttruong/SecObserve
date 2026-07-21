import hashlib
import json
from copy import copy
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from django.conf import settings
from django.db.models import QuerySet

from application.core.models import Observation
from application.core.services.observation import normalize_observation_fields
from application.rules.models import Rule
from application.rules.services.rule_engine import Rule_Engine
from application.rules.types import Rule_Type

MAX_OBSERVATIONS = 100


class SimulationCancelled(Exception):
    pass


@dataclass(frozen=True)
class SimulationScope:
    product_ids: tuple[int, ...] = ()
    parser_id: Optional[int] = None
    scanner_prefix: str = ""


def build_simulation_queryset(rule: Rule, scope: Optional[SimulationScope] = None) -> QuerySet[Observation]:
    scope = scope or SimulationScope()

    if rule.product:
        if rule.product.is_product_group:
            observations = Observation.objects.filter(product__product_group=rule.product)
        else:
            observations = Observation.objects.filter(product=rule.product)
    else:
        observations = Observation.objects.filter(
            product__apply_general_rules=True,
            product__is_product_group=False,
        )

    if scope.product_ids:
        observations = observations.filter(product_id__in=scope.product_ids)

    if rule.type == Rule_Type.RULE_TYPE_FIELDS:
        if rule.parser:
            observations = observations.filter(parser=rule.parser)
        if rule.scanner_prefix:
            observations = observations.filter(scanner__istartswith=rule.scanner_prefix)

    if scope.parser_id:
        observations = observations.filter(parser_id=scope.parser_id)
    if scope.scanner_prefix:
        observations = observations.filter(scanner__istartswith=scope.scanner_prefix)

    return observations.order_by("pk").select_related(
        "product",
        "product__product_group",
        "branch",
        "origin_service",
        "parser",
        "general_rule",
        "general_rule_rego",
        "product_rule",
        "product_rule_rego",
    )


def get_rule_definition_hash(rule: Rule) -> str:
    rule_definition = {
        "id": rule.pk,
        "product_id": rule.product_id,
        "type": rule.type,
        "parser_id": rule.parser_id,
        "scanner_prefix": rule.scanner_prefix,
        "title": rule.title,
        "description_observation": rule.description_observation,
        "origin_component_name_version": rule.origin_component_name_version,
        "origin_component_purl": rule.origin_component_purl,
        "origin_docker_image_name_tag": rule.origin_docker_image_name_tag,
        "origin_endpoint_url": rule.origin_endpoint_url,
        "origin_service_name": rule.origin_service_name,
        "origin_source_file": rule.origin_source_file,
        "origin_cloud_qualified_resource": rule.origin_cloud_qualified_resource,
        "origin_kubernetes_qualified_resource": rule.origin_kubernetes_qualified_resource,
        "new_severity": rule.new_severity,
        "new_status": rule.new_status,
        "new_vex_justification": rule.new_vex_justification,
        "new_vex_remediations": rule.new_vex_remediations,
        "rego_module": rule.rego_module,
    }
    encoded_definition = json.dumps(rule_definition, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded_definition).hexdigest()


def simulate_rule(
    rule: Rule,
    scope: Optional[SimulationScope] = None,
    progress_callback: Optional[Callable[[int], None]] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
    chunk_size: Optional[int] = None,
) -> Tuple[int, list[Observation]]:
    number_observations = 0
    processed_observations = 0
    simulation_results: list[Observation] = []
    observations = build_simulation_queryset(rule, scope)
    simulation_chunk_size = chunk_size or settings.RULE_SIMULATION_CHUNK_SIZE
    rule_engine: Optional[Rule_Engine] = None

    if is_cancelled and is_cancelled():
        raise SimulationCancelled()

    for observation in observations.iterator(chunk_size=simulation_chunk_size):
        if rule_engine is None:
            # Simulation evaluates only the selected rule. Reusing one engine avoids
            # loading and compiling every enabled Rego rule once per product.
            rule_engine = Rule_Engine(observation.product, rules=[rule])

        observation_before = copy(observation)
        _reset_rule_fields(observation_before)
        normalize_observation_fields(observation_before)

        if rule_engine.check_rule_for_observation(rule, observation, observation_before, True):
            number_observations += 1
            if len(simulation_results) < MAX_OBSERVATIONS:
                simulation_results.append(observation)

        processed_observations += 1
        if processed_observations % simulation_chunk_size == 0:
            if progress_callback:
                progress_callback(processed_observations)
            if is_cancelled and is_cancelled():
                raise SimulationCancelled()

    if progress_callback:
        progress_callback(processed_observations)

    return number_observations, simulation_results


def _reset_rule_fields(observation: Observation) -> None:
    observation.rule_status = ""
    observation.rule_rego_status = ""
    observation.rule_severity = ""
    observation.rule_rego_severity = ""
    observation.rule_priority = None
    observation.rule_rego_priority = None
    observation.rule_vex_justification = ""
    observation.rule_rego_vex_justification = ""
    observation.rule_vex_remediations = None
    observation.rule_rego_vex_remediations = None
    observation.general_rule = None
    observation.general_rule_rego = None
    observation.product_rule = None
    observation.product_rule_rego = None
