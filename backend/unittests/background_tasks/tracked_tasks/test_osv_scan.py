from unittest.mock import patch

from django.core.management import call_command

from application.background_tasks.models import Periodic_Task
from application.background_tasks.tracked_tasks.osv_scan import (
    TASK_NAME,
    _process_osv_scan,
)
from application.background_tasks.types import Status
from application.core.models import Branch, Product, Service
from application.import_observations.models import Scan_Request
from unittests.base_test_case import BaseTestCase


class TestOSVScanTask(BaseTestCase):
    def setUp(self):
        call_command(
            "loaddata",
            [
                "unittests/fixtures/initial_license_data.json",
                "unittests/fixtures/unittests_fixtures.json",
            ],
        )

        self.product = Product.objects.get(id=1)
        self.branch = Branch.objects.filter(product=self.product).first()
        self.service = Service.objects.filter(product=self.product).first()

    @patch("application.background_tasks.tracked_tasks.osv_scan.OSVScanner.scan_scope")
    def test_scan_scope_is_called_with_resolved_objects(self, mock_scan_scope):
        mock_scan_scope.return_value = (1, 2, 3)

        _process_osv_scan(self.product.pk, self.branch.pk, self.service.pk)

        mock_scan_scope.assert_called_once_with(self.product, self.branch, self.service)

        periodic_task = Periodic_Task.objects.get(task=TASK_NAME)
        self.assertEqual(Status.STATUS_SUCCESS, periodic_task.status)
        self.assertIn("1 new, 2 updated, 3 resolved", periodic_task.message)

    @patch("application.background_tasks.tracked_tasks.osv_scan.OSVScanner.scan_scope")
    def test_scan_without_branch_and_service(self, mock_scan_scope):
        mock_scan_scope.return_value = (0, 0, 0)

        _process_osv_scan(self.product.pk, None, None)

        mock_scan_scope.assert_called_once_with(self.product, None, None)

    @patch("application.background_tasks.tracked_tasks.osv_scan.OSVScanner.scan_scope")
    def test_scan_request_is_deleted_before_the_scan(self, mock_scan_scope):
        Scan_Request.objects.create(product=self.product, branch=self.branch, service=self.service)

        def assert_request_is_gone(*args, **kwargs):
            # The invariant: an import committing from here on requests a new scan instead of being
            # swallowed by this one.
            self.assertEqual(0, Scan_Request.objects.count())
            return (0, 0, 0)

        mock_scan_scope.side_effect = assert_request_is_gone

        _process_osv_scan(self.product.pk, self.branch.pk, self.service.pk)

        mock_scan_scope.assert_called_once()
        self.assertEqual(0, Scan_Request.objects.count())

    @patch("application.background_tasks.tracked_tasks.osv_scan.OSVScanner.scan_scope")
    def test_unknown_product_is_not_scanned(self, mock_scan_scope):
        _process_osv_scan(99999, None, None)

        mock_scan_scope.assert_not_called()

        periodic_task = Periodic_Task.objects.get(task=TASK_NAME)
        self.assertEqual(Status.STATUS_SUCCESS, periodic_task.status)
        self.assertIn("not found", periodic_task.message)

    @patch("application.background_tasks.tracked_tasks.osv_scan.handle_task_exception")
    @patch("application.background_tasks.tracked_tasks.osv_scan.OSVScanner.scan_scope")
    def test_failure_is_recorded_and_re_raised_for_the_retry(self, mock_scan_scope, mock_handle_task_exception):
        mock_scan_scope.side_effect = Exception("OSV is not reachable")

        with self.assertRaises(Exception) as e:
            _process_osv_scan(self.product.pk, None, None)

        self.assertEqual("OSV is not reachable", str(e.exception))

        periodic_task = Periodic_Task.objects.get(task=TASK_NAME)
        self.assertEqual(Status.STATUS_FAILURE, periodic_task.status)
        self.assertEqual("OSV is not reachable", periodic_task.message)
        mock_handle_task_exception.assert_called_once()
