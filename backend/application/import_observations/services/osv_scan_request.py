import logging
from typing import Optional

from django.dispatch import Signal

from application.commons.models import Settings
from application.core.models import Branch, Product, Service
from application.import_observations.models import Scan_Request

logger = logging.getLogger("secobserve.import_observations")

# Requests a background OSV scan of one scope. The receiver lives in
# application.background_tasks, which is a higher layer and must not be imported from here.
osv_scan_requested = Signal()


def request_osv_scan(product: Product, branch: Optional[Branch], service: Optional[Service]) -> bool:
    if not osv_scan_after_import_enabled(product):
        return False

    _, created = Scan_Request.objects.get_or_create(product=product, branch=branch, service=service)
    if not created:
        # A scan for this scope is already pending. It deletes the request before it reads the
        # components, so it will still see everything that has been imported until then.
        logger.debug(
            "OSV scan already requested - product %s / branch %s / service %s",
            product.pk,
            branch.pk if branch else None,
            service.pk if service else None,
        )
        return False

    osv_scan_requested.send(
        sender=Product,
        product_id=product.pk,
        branch_id=branch.pk if branch else None,
        service_id=service.pk if service else None,
    )
    return True


def osv_scan_after_import_enabled(product: Product) -> bool:
    settings = Settings.load()
    return bool(
        settings.feature_osv_scan_after_import and product.osv_enabled and product.automatic_osv_scanning_enabled
    )
