from typing import Any, Optional

from django.dispatch import receiver

from application.background_tasks.tracked_tasks.general_rule_evaluator import (
    evaluate_general_rule_task,
)
from application.background_tasks.tracked_tasks.osv_scan import osv_scan_task
from application.import_observations.services.osv_scan_request import (
    osv_scan_requested,
)
from application.rules.services.evaluator import general_rule_evaluation_requested


@receiver(general_rule_evaluation_requested)
def general_rule_evaluation_requested_handler(  # pylint: disable=unused-argument
    sender: Any, rule_id: int, **kwargs: Any
) -> None:
    # application.rules must not import application.background_tasks, so the API view requests the
    # evaluation through a signal instead of calling the task directly.
    evaluate_general_rule_task(rule_id)


@receiver(osv_scan_requested)
def osv_scan_requested_handler(  # pylint: disable=unused-argument
    sender: Any, product_id: int, branch_id: Optional[int], service_id: Optional[int], **kwargs: Any
) -> None:
    # application.import_observations must not import application.background_tasks, so the import
    # requests the scan through a signal instead of calling the task directly.
    osv_scan_task(product_id, branch_id, service_id)
