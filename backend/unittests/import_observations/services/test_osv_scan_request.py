from unittest.mock import patch

from django.core.management import call_command

from application.commons.models import Settings
from application.core.models import Branch, Product, Service
from application.import_observations.models import Scan_Request
from application.import_observations.services.osv_scan_request import (
    osv_scan_after_import_enabled,
    request_osv_scan,
)
from unittests.base_test_case import BaseTestCase


class TestRequestOSVScan(BaseTestCase):
    def setUp(self):
        call_command(
            "loaddata",
            [
                "unittests/fixtures/initial_license_data.json",
                "unittests/fixtures/unittests_fixtures.json",
            ],
        )

        self.product = Product.objects.get(id=1)
        self.product.osv_enabled = True
        self.product.automatic_osv_scanning_enabled = True
        self.product.save()

        self.branch = Branch.objects.filter(product=self.product).first()
        self.service = Service.objects.filter(product=self.product).first()

        settings = Settings.load()
        settings.feature_osv_scan_after_import = True
        settings.save()

    @patch("application.background_tasks.signals.osv_scan_task")
    def test_request_creates_scan_request_and_task(self, mock_task):
        self.assertTrue(request_osv_scan(self.product, self.branch, self.service))

        self.assertEqual(
            1,
            Scan_Request.objects.filter(product=self.product, branch=self.branch, service=self.service).count(),
        )
        mock_task.assert_called_once_with(self.product.pk, self.branch.pk, self.service.pk)

    @patch("application.background_tasks.signals.osv_scan_task")
    def test_request_without_branch_and_service(self, mock_task):
        self.assertTrue(request_osv_scan(self.product, None, None))

        mock_task.assert_called_once_with(self.product.pk, None, None)

    @patch("application.background_tasks.signals.osv_scan_task")
    def test_second_request_for_same_scope_is_coalesced(self, mock_task):
        self.assertTrue(request_osv_scan(self.product, self.branch, self.service))
        self.assertFalse(request_osv_scan(self.product, self.branch, self.service))

        self.assertEqual(
            1,
            Scan_Request.objects.filter(product=self.product, branch=self.branch, service=self.service).count(),
        )
        mock_task.assert_called_once()

    @patch("application.background_tasks.signals.osv_scan_task")
    def test_no_request_when_feature_is_disabled(self, mock_task):
        settings = Settings.load()
        settings.feature_osv_scan_after_import = False
        settings.save()

        self.assertFalse(request_osv_scan(self.product, self.branch, self.service))

        self.assertEqual(0, Scan_Request.objects.count())
        mock_task.assert_not_called()

    @patch("application.background_tasks.signals.osv_scan_task")
    def test_no_request_when_osv_is_disabled_for_product(self, mock_task):
        self.product.osv_enabled = False
        self.product.save()

        self.assertFalse(request_osv_scan(self.product, self.branch, self.service))

        self.assertEqual(0, Scan_Request.objects.count())
        mock_task.assert_not_called()

    @patch("application.background_tasks.signals.osv_scan_task")
    def test_no_request_when_automatic_scanning_is_disabled_for_product(self, mock_task):
        self.product.automatic_osv_scanning_enabled = False
        self.product.save()

        self.assertFalse(request_osv_scan(self.product, self.branch, self.service))

        self.assertEqual(0, Scan_Request.objects.count())
        mock_task.assert_not_called()

    def test_osv_scan_after_import_enabled(self):
        self.assertTrue(osv_scan_after_import_enabled(self.product))

        self.product.osv_enabled = False
        self.assertFalse(osv_scan_after_import_enabled(self.product))
