import uuid
from typing import Any

from django.db.models import (
    CASCADE,
    PROTECT,
    SET_NULL,
    BigIntegerField,
    BooleanField,
    CharField,
    DateTimeField,
    ForeignKey,
    Index,
    JSONField,
    Model,
    TextField,
    UUIDField,
)

from application.access_control.models import User
from application.access_control.services.current_user import get_current_user
from application.commons.models import Settings
from application.core.models import Product
from application.core.types import Severity, Status, VEX_Justification
from application.rules.types import Rule_Simulation_Status, Rule_Status, Rule_Type


class Rule(Model):
    name = CharField(max_length=255)
    description = TextField(max_length=2048, blank=True)
    product = ForeignKey(Product, blank=True, null=True, on_delete=CASCADE)
    type = CharField(max_length=8, choices=Rule_Type.RULE_TYPE_CHOICES, default=Rule_Type.RULE_TYPE_FIELDS)

    parser = ForeignKey("import_observations.Parser", null=True, on_delete=CASCADE)
    scanner_prefix = CharField(max_length=255, blank=True)
    title = CharField(max_length=255, blank=True)
    description_observation = CharField(max_length=255, blank=True)
    origin_component_name_version = CharField(max_length=513, blank=True)
    origin_component_purl = CharField(max_length=255, blank=True)
    origin_docker_image_name_tag = CharField(max_length=513, blank=True)
    origin_endpoint_url = TextField(max_length=2048, blank=True)
    origin_service_name = CharField(max_length=255, blank=True)
    origin_source_file = CharField(max_length=255, blank=True)
    origin_cloud_qualified_resource = CharField(max_length=255, blank=True)
    origin_kubernetes_qualified_resource = CharField(max_length=255, blank=True)
    new_severity = CharField(max_length=12, choices=Severity.SEVERITY_CHOICES, blank=True)
    new_status = CharField(max_length=16, choices=Status.STATUS_CHOICES, blank=True)
    new_vex_justification = CharField(max_length=64, choices=VEX_Justification.VEX_JUSTIFICATION_CHOICES, blank=True)
    new_vex_remediations = JSONField(blank=True, null=True)

    rego_module = TextField(blank=True)

    enabled = BooleanField(default=True)
    user = ForeignKey(
        User,
        related_name="rule",
        on_delete=PROTECT,
        null=True,
    )
    approval_status = CharField(max_length=16, choices=Rule_Status.RULE_STATUS_CHOICES)
    rejection_remark = TextField(max_length=255, blank=True)
    approval_date = DateTimeField(null=True)
    approval_user = ForeignKey(
        User,
        related_name="rule_approver",
        on_delete=PROTECT,
        null=True,
    )

    class Meta:
        unique_together = (
            "product",
            "name",
        )
        indexes = [
            Index(fields=["name"]),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.approval_status:
            self.user = get_current_user()

            self.rejection_remark = ""
            self.approval_date = None
            self.approval_user = None

            needs_approval = False
            if not self.product:
                settings = Settings.load()
                needs_approval = settings.feature_general_rules_need_approval
            else:
                if self.product.product_group:
                    product_group_product_rules_needs_approval = self.product.product_group.product_rules_need_approval
                    needs_approval = (
                        self.product.product_rules_need_approval or product_group_product_rules_needs_approval
                    )
                else:
                    needs_approval = self.product.product_rules_need_approval

            if needs_approval:
                self.approval_status = Rule_Status.RULE_STATUS_NEEDS_APPROVAL
            else:
                self.approval_status = Rule_Status.RULE_STATUS_AUTO_APPROVED

        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class Rule_Simulation(Model):
    id = UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rule = ForeignKey(Rule, on_delete=CASCADE, related_name="simulations")
    user = ForeignKey(User, on_delete=PROTECT, related_name="rule_simulations")
    status = CharField(
        max_length=16,
        choices=Rule_Simulation_Status.STATUS_CHOICES,
        default=Rule_Simulation_Status.STATUS_QUEUED,
    )

    products = JSONField(default=list, blank=True)
    parser = ForeignKey("import_observations.Parser", null=True, blank=True, on_delete=SET_NULL)
    scanner_prefix = CharField(max_length=255, blank=True)

    candidate_count = BigIntegerField(default=0)
    processed_count = BigIntegerField(default=0)
    match_count = BigIntegerField(default=0)
    result_observation_ids = JSONField(default=list, blank=True)

    rule_definition_hash = CharField(max_length=64)
    error_message = TextField(max_length=2048, blank=True)
    created = DateTimeField(auto_now_add=True)
    started = DateTimeField(null=True)
    finished = DateTimeField(null=True)

    class Meta:
        indexes = [
            Index(fields=["user", "-created"], name="rules_sim_user_created_idx"),
            Index(fields=["status", "-created"], name="rules_sim_status_created_idx"),
            Index(fields=["rule", "status"], name="rules_sim_rule_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.rule.name} / {self.status}"
