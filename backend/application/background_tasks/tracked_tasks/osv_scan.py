import logging
from datetime import timedelta
from typing import Optional

from django.utils import timezone
from huey.contrib.djhuey import on_commit_task

from application.background_tasks.models import Periodic_Task
from application.background_tasks.services.task_base import delete_older_task_entries
from application.background_tasks.types import Status
from application.core.models import Branch, Product, Service
from application.import_observations.models import Scan_Request
from application.import_observations.scanners.osv_scanner import OSVScanner
from application.notifications.services.tasks import handle_task_exception

logger = logging.getLogger("secobserve.import_observations")

TASK_NAME = "Scan components with OSV after import"


@on_commit_task(retries=2, retry_delay=60, retry_backoff=2)
def osv_scan_task(product_id: int, branch_id: Optional[int], service_id: Optional[int]) -> None:
    _process_osv_scan(product_id, branch_id, service_id)


def _process_osv_scan(product_id: int, branch_id: Optional[int], service_id: Optional[int]) -> None:
    task_record = Periodic_Task(
        task=TASK_NAME,
        start_time=timezone.now(),
        status=Status.STATUS_RUNNING,
    )
    task_record.save()

    delete_older_task_entries(TASK_NAME)

    product = None

    try:
        # Deleted before the components are read, so an import that commits while this scan runs
        # requests a new scan instead of being swallowed by this one. The price is that two scans of
        # the same scope can overlap in that window, the same exposure the periodic and the manual
        # scan already have.
        scan_requests = Scan_Request.objects.filter(product_id=product_id)
        scan_requests = (
            scan_requests.filter(branch__isnull=True) if branch_id is None else scan_requests.filter(branch=branch_id)
        )
        scan_requests = (
            scan_requests.filter(service__isnull=True)
            if service_id is None
            else scan_requests.filter(service=service_id)
        )
        scan_requests.delete()

        product = Product.objects.filter(pk=product_id).first()
        if not product:
            message = f"Product {product_id} not found, nothing to scan"
        else:
            branch = Branch.objects.filter(pk=branch_id, product=product).first() if branch_id else None
            service = Service.objects.filter(pk=service_id, product=product).first() if service_id else None

            observations_new, observations_updated, observations_resolved = OSVScanner().scan_scope(
                product, branch, service
            )
            message = (
                f"{product.name} / {branch.name if branch else '-'} / {service.name if service else '-'}: "
                f"{observations_new} new, {observations_updated} updated, {observations_resolved} resolved"
            )

        task_record.status = Status.STATUS_SUCCESS
        task_record.duration = (timezone.now() - task_record.start_time) / timedelta(milliseconds=1)
        task_record.message = message[:255]
        task_record.save()
    except Exception as e:
        task_record.status = Status.STATUS_FAILURE
        task_record.duration = (timezone.now() - task_record.start_time) / timedelta(milliseconds=1)
        task_record.message = str(e)[:255]
        task_record.save()

        handle_task_exception(e, product=product)

        # Re-raised so that Huey retries the task; a swallowed exception would end it silently.
        raise
