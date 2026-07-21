from typing import Any, cast

from django.db.models import QuerySet
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.filters import SearchFilter
from rest_framework.mixins import RetrieveModelMixin
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet, ModelViewSet

from application.access_control.models import User
from application.authorization.services.authorization import user_has_permission_or_403
from application.authorization.services.roles_permissions import Permissions
from application.rules.api.filters import GeneralRuleFilter, ProductRuleFilter
from application.rules.api.permissions import (
    UserHasGeneralRulePermission,
    UserHasProductRulePermission,
)
from application.rules.api.serializers import (
    GeneralRuleSerializer,
    ProductRuleSerializer,
    RuleApprovalSerializer,
    RuleSimulationRequestSerializer,
    RuleSimulationSerializer,
)
from application.rules.models import Rule, Rule_Simulation
from application.rules.queries.rule import (
    get_general_rule_by_id,
    get_general_rules,
    get_product_rule_by_id,
    get_product_rules,
)
from application.rules.services.approval import rule_approval
from application.rules.services.simulation_jobs import (
    InvalidSimulationScope,
    SimulationBusy,
    SimulationLimitExceeded,
    cancel_rule_simulation,
    create_rule_simulation,
    mark_rule_simulation_failed,
)
from application.rules.services.simulator import SimulationScope
from application.rules.tasks import run_rule_simulation


class GeneralRuleViewSet(ModelViewSet):
    serializer_class = GeneralRuleSerializer
    filterset_class = GeneralRuleFilter
    queryset = Rule.objects.none()
    permission_classes = (IsAuthenticated, UserHasGeneralRulePermission)
    filter_backends = [SearchFilter, DjangoFilterBackend]
    search_fields = ["name"]

    def get_queryset(self) -> QuerySet[Rule]:
        return get_general_rules()

    @extend_schema(
        methods=["PATCH"],
        request=RuleApprovalSerializer,
        responses={status.HTTP_204_NO_CONTENT: None},
    )
    @action(detail=True, methods=["patch"])
    def approval(self, request: Request, pk: int) -> Response:
        request_serializer = RuleApprovalSerializer(data=request.data)
        if not request_serializer.is_valid():
            raise ValidationError(request_serializer.errors)

        general_rule = get_general_rule_by_id(pk)
        if not general_rule:
            raise NotFound(f"General rule {pk} not found")

        rule_approval(
            general_rule,
            request_serializer.validated_data.get("approval_status"),
            request_serializer.validated_data.get("rejection_remark"),
        )

        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        methods=["POST"],
        request=RuleSimulationRequestSerializer,
        responses={status.HTTP_202_ACCEPTED: RuleSimulationSerializer},
    )
    @action(detail=True, methods=["post"])
    def simulate(self, request: Request, pk: int) -> Response:
        rule = get_general_rule_by_id(pk)
        if not rule:
            raise NotFound()

        return _start_simulation(request, rule)


class ProductRuleViewSet(ModelViewSet):
    serializer_class = ProductRuleSerializer
    filterset_class = ProductRuleFilter
    queryset = Rule.objects.none()
    permission_classes = (IsAuthenticated, UserHasProductRulePermission)
    filter_backends = [SearchFilter, DjangoFilterBackend]
    search_fields = ["name"]

    def get_queryset(self) -> QuerySet[Rule]:
        return get_product_rules()

    @extend_schema(
        methods=["PATCH"],
        request=RuleApprovalSerializer,
        responses={status.HTTP_204_NO_CONTENT: None},
    )
    @action(detail=True, methods=["patch"])
    def approval(self, request: Request, pk: int) -> Response:
        request_serializer = RuleApprovalSerializer(data=request.data)
        if not request_serializer.is_valid():
            raise ValidationError(request_serializer.errors)

        product_rule = get_product_rule_by_id(pk)
        if not product_rule:
            raise NotFound(f"Product rule {pk} not found")

        user_has_permission_or_403(product_rule, Permissions.Product_Rule_Approval)

        rule_approval(
            product_rule,
            request_serializer.validated_data.get("approval_status"),
            request_serializer.validated_data.get("rejection_remark"),
        )

        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        methods=["POST"],
        request=RuleSimulationRequestSerializer,
        responses={status.HTTP_202_ACCEPTED: RuleSimulationSerializer},
    )
    @action(detail=True, methods=["post"])
    def simulate(self, request: Request, pk: int) -> Response:
        rule = get_product_rule_by_id(pk)
        if not rule:
            raise NotFound()

        user_has_permission_or_403(rule.product, Permissions.Observation_View)

        return _start_simulation(request, rule)


class RuleSimulationViewSet(RetrieveModelMixin, GenericViewSet):
    serializer_class = RuleSimulationSerializer
    permission_classes = (IsAuthenticated,)
    queryset = Rule_Simulation.objects.none()

    def get_queryset(self) -> QuerySet[Rule_Simulation]:
        user = cast(User, self.request.user)
        return Rule_Simulation.objects.filter(user=user).select_related("rule", "parser")

    @extend_schema(responses={status.HTTP_204_NO_CONTENT: None})
    def destroy(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        simulation = self.get_object()
        cancel_rule_simulation(simulation)
        return Response(status=status.HTTP_204_NO_CONTENT)


def _start_simulation(request: Request, rule: Rule) -> Response:
    request_serializer = RuleSimulationRequestSerializer(data=request.data)
    if not request_serializer.is_valid():
        raise ValidationError(request_serializer.errors)

    scope = SimulationScope(
        product_ids=tuple(request_serializer.validated_data.get("products", [])),
        parser_id=request_serializer.validated_data.get("parser"),
        scanner_prefix=request_serializer.validated_data.get("scanner_prefix", ""),
    )

    try:
        simulation = create_rule_simulation(rule, cast(User, request.user), scope)
    except (InvalidSimulationScope, SimulationLimitExceeded) as exception:
        raise ValidationError(str(exception)) from exception
    except SimulationBusy as exception:
        return Response(
            status=status.HTTP_409_CONFLICT,
            data={"detail": str(exception)},
        )

    try:
        run_rule_simulation(str(simulation.pk))
    except Exception as exception:
        mark_rule_simulation_failed(simulation, exception)
        return Response(
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
            data={"detail": "The rule simulation could not be queued"},
        )

    response_serializer = RuleSimulationSerializer(simulation)

    return Response(status=status.HTTP_202_ACCEPTED, data=response_serializer.data)
