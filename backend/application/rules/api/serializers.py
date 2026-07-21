from typing import Any, Optional, cast

from rest_framework.serializers import (
    CharField,
    ChoiceField,
    DateTimeField,
    IntegerField,
    ListField,
    ModelSerializer,
    Serializer,
    SerializerMethodField,
    ValidationError,
)

from application.commons.services.functions import validate_vex_remediations
from application.core.api.serializers_observation import ObservationListSerializer
from application.core.api.serializers_product import NestedProductSerializer
from application.core.models import Observation, Product
from application.import_observations.models import Parser
from application.rules.models import Rule, Rule_Simulation
from application.rules.types import Rule_Simulation_Status, Rule_Status, Rule_Type


class GeneralRuleSerializer(ModelSerializer):
    user = CharField(read_only=True)
    approval_status = CharField(read_only=True)
    rejection_remark = CharField(read_only=True)
    approval_date = DateTimeField(read_only=True)
    approval_user = CharField(read_only=True)
    user_full_name = SerializerMethodField()
    approval_user_full_name = SerializerMethodField()

    class Meta:
        model = Rule
        exclude = ["product"]

    def get_user_full_name(self, obj: Rule) -> Optional[str]:
        if obj.user:
            return obj.user.full_name

        return None

    def get_approval_user_full_name(self, obj: Rule) -> Optional[str]:
        if obj.approval_user:
            return obj.approval_user.full_name

        return None

    def validate_description(self, value: str) -> str:
        if not value:
            raise ValidationError("Must be set")

        return value

    def validate_new_vex_remediations(self, value: Any) -> Optional[list[dict]]:
        return validate_vex_remediations(value)

    def validate(self, attrs: dict) -> dict:
        if attrs.get("type") == Rule_Type.RULE_TYPE_REGO and not attrs.get("rego_module"):
            raise ValidationError("Rego module must be set")

        return super().validate(attrs)

    def update(self, instance: Rule, validated_data: dict) -> Rule:
        instance.approval_status = ""
        return super().update(instance, validated_data)


class ProductRuleSerializer(ModelSerializer):
    product_data = NestedProductSerializer(source="product", read_only=True)
    user = CharField(read_only=True)
    approval_status = CharField(read_only=True)
    rejection_remark = CharField(read_only=True)
    approval_date = DateTimeField(read_only=True)
    approval_user = CharField(read_only=True)
    user_full_name = SerializerMethodField()
    approval_user_full_name = SerializerMethodField()

    class Meta:
        model = Rule
        fields = "__all__"

    def get_user_full_name(self, obj: Rule) -> Optional[str]:
        if obj.user:
            return obj.user.full_name

        return None

    def get_approval_user_full_name(self, obj: Rule) -> Optional[str]:
        if obj.approval_user:
            return obj.approval_user.full_name

        return None

    def validate_description(self, value: str) -> str:
        if not value:
            raise ValidationError("Must be set")

        return value

    def validate_product(self, value: Product) -> Product:
        self.instance: Rule
        if self.instance and self.instance.product != value:
            raise ValidationError("Product cannot be changed")

        return value

    def validate(self, attrs: dict) -> dict:
        if attrs.get("type") == Rule_Type.RULE_TYPE_REGO and not attrs.get("rego_module"):
            raise ValidationError("Rego module must be set")

        return super().validate(attrs)

    def update(self, instance: Rule, validated_data: dict) -> Rule:
        instance.approval_status = ""
        return super().update(instance, validated_data)


class RuleApprovalSerializer(Serializer):
    approval_status = ChoiceField(choices=Rule_Status.RULE_STATUS_CHOICES_APPROVAL, required=True)
    rejection_remark = CharField(max_length=255, required=False, allow_blank=True)

    def validate(self, attrs: dict) -> dict:
        if attrs.get("approval_status") == Rule_Status.RULE_STATUS_APPROVED and attrs.get("rejection_remark"):
            raise ValidationError("Remark for rejection cannot be set with approval")

        if attrs.get("approval_status") == Rule_Status.RULE_STATUS_REJECTED and not attrs.get("rejection_remark"):
            raise ValidationError("Rejection needs a remark")

        return super().validate(attrs)


class RuleSimulationRequestSerializer(Serializer):
    products = ListField(
        child=IntegerField(min_value=1),
        required=False,
        default=list,
        max_length=100,
    )
    parser = IntegerField(min_value=1, required=False, allow_null=True)
    scanner_prefix = CharField(max_length=255, required=False, allow_blank=True, default="")

    def validate_products(self, value: list[int]) -> list[int]:
        product_ids = list(dict.fromkeys(value))
        if Product.objects.filter(pk__in=product_ids, is_product_group=False).count() != len(product_ids):
            raise ValidationError("One or more products do not exist")
        return product_ids

    def validate_parser(self, value: Optional[int]) -> Optional[int]:
        if value and not Parser.objects.filter(pk=value).exists():
            raise ValidationError("Parser does not exist")
        return value


class RuleSimulationSerializer(ModelSerializer):
    results = SerializerMethodField()

    class Meta:
        model = Rule_Simulation
        fields = [
            "id",
            "rule",
            "status",
            "products",
            "parser",
            "scanner_prefix",
            "candidate_count",
            "processed_count",
            "match_count",
            "results",
            "error_message",
            "created",
            "started",
            "finished",
        ]
        read_only_fields = fields

    def get_results(self, simulation: Rule_Simulation) -> list[dict[str, Any]]:
        if simulation.status != Rule_Simulation_Status.STATUS_COMPLETED or not simulation.result_observation_ids:
            return []

        observations = Observation.objects.filter(pk__in=simulation.result_observation_ids).select_related(
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
        observations_by_id = {observation.pk: observation for observation in observations}
        ordered_observations = [
            observations_by_id[observation_id]
            for observation_id in simulation.result_observation_ids
            if observation_id in observations_by_id
        ]
        return cast(list[dict[str, Any]], ObservationListSerializer(ordered_observations, many=True).data)
