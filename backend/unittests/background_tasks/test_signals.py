import unittest
from unittest.mock import patch

from application.import_observations.services.osv_scan_request import osv_scan_requested
from application.rules.services.evaluator import request_general_rule_evaluation


class TestGeneralRuleEvaluationRequested(unittest.TestCase):
    """
    application.rules must not import application.background_tasks, so the API view requests an
    evaluation through a signal. Nothing statically links the two sides, which is why the receiver
    connected in BackgroundTasksConfig.ready() is verified here.
    """

    @patch("application.background_tasks.signals.evaluate_general_rule_task")
    def test_receiver_enqueues_the_task(self, mock_evaluate_general_rule_task):
        request_general_rule_evaluation(42)

        mock_evaluate_general_rule_task.assert_called_once_with(42)


class TestOSVScanRequested(unittest.TestCase):
    """
    application.import_observations must not import application.background_tasks, so an import
    requests the scan through a signal. Nothing statically links the two sides, which is why the
    receiver connected in BackgroundTasksConfig.ready() is verified here.
    """

    @patch("application.background_tasks.signals.osv_scan_task")
    def test_receiver_enqueues_the_task(self, mock_osv_scan_task):
        osv_scan_requested.send(sender=None, product_id=1, branch_id=2, service_id=3)

        mock_osv_scan_task.assert_called_once_with(1, 2, 3)

    @patch("application.background_tasks.signals.osv_scan_task")
    def test_receiver_enqueues_the_task_without_branch_and_service(self, mock_osv_scan_task):
        osv_scan_requested.send(sender=None, product_id=1, branch_id=None, service_id=None)

        mock_osv_scan_task.assert_called_once_with(1, None, None)
