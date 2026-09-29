from unittest.mock import MagicMock, call, patch

from application.background_tasks.models import Periodic_Task
from application.background_tasks.periodic_tasks.import_observations_tasks import (
    task_api_import,
    task_osv_scan_product,
)
from application.background_tasks.types import Status
from application.commons.models import Settings
from application.core.models import Product
from unittests.base_test_case import BaseTestCase

TASK_NAME = "Import observations from API configurations, OSV and VulnerableCode"


def _create_product(name: str, osv: bool = True, vulnerablecode: bool = True) -> Product:
    return Product.objects.create(
        name=name,
        osv_enabled=osv,
        automatic_osv_scanning_enabled=osv,
        vulnerablecode_enabled=vulnerablecode,
        automatic_vulnerablecode_scanning_enabled=vulnerablecode,
    )


class TestImportObservationsTasks(BaseTestCase):
    def _get_task(self) -> Periodic_Task:
        return Periodic_Task.objects.filter(task=TASK_NAME).latest("start_time")

    def _get_task_message(self) -> str:
        # so_periodic_task swallows the return value of the task and stores it on the Periodic_Task
        # entry, so the composed message can only be asserted through the database.
        return self._get_task().message

    # ---------------------------------------------------------------
    # task_api_import
    # ---------------------------------------------------------------

    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.VulnerableCodeScanner.scan_product")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.OSVScanner.scan_product")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.api_import_observations")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.Api_Configuration.objects.filter")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.Settings.load")
    def test_task_api_import_all_enabled(
        self,
        mock_settings_load,
        mock_api_config_filter,
        mock_api_import_observations,
        mock_scan_product_osv,
        mock_scan_product_vc,
    ):
        # Setup
        # Mock settings
        settings = Settings()
        settings.feature_automatic_api_import = True
        settings.feature_automatic_osv_scanning = True
        settings.feature_automatic_vulnerablecode_scanning = True
        settings.vulnerablecode_base_url = "http://vulnerablecode.example.com"
        mock_settings_load.return_value = settings

        # Mock API configurations
        mock_api_config = MagicMock()
        mock_api_config.automatic_import_branch = self.branch_1
        mock_api_config.automatic_import_service = self.service_1
        mock_api_config.automatic_import_docker_image_name_tag = "image:tag"
        mock_api_config.automatic_import_endpoint_url = "https://example.com"
        mock_api_config.automatic_import_kubernetes_cluster = "cluster1"
        mock_api_config_filter.return_value = [mock_api_config]

        product = _create_product("product_osv_vulnerablecode")
        _create_product("product_none", osv=False, vulnerablecode=False)

        # Mock import results
        mock_api_import_observations.return_value = (1, 2, 3)  # new, updated, resolved
        mock_scan_product_osv.return_value = (4, 5, 6)  # new, updated, resolved
        mock_scan_product_vc.return_value = (7, 8, 9)  # new, updated, resolved

        # Execute
        task_api_import()

        # Assert
        # Check API import was called with correct parameters
        mock_api_config_filter.assert_called_once_with(automatic_import_enabled=True)
        mock_api_import_observations.assert_called_once()
        api_import_params = mock_api_import_observations.call_args[0][0]
        self.assertEqual(api_import_params.api_configuration, mock_api_config)
        self.assertEqual(api_import_params.branch, mock_api_config.automatic_import_branch)
        self.assertEqual(api_import_params.service_name, mock_api_config.automatic_import_service.name)
        self.assertEqual(
            api_import_params.docker_image_name_tag, mock_api_config.automatic_import_docker_image_name_tag
        )
        self.assertEqual(api_import_params.endpoint_url, mock_api_config.automatic_import_endpoint_url)
        self.assertEqual(api_import_params.kubernetes_cluster, mock_api_config.automatic_import_kubernetes_cluster)

        # Check VulnerableCode scanning was called, and OSV scanning by the enqueued task
        mock_scan_product_vc.assert_called_once_with(product)
        mock_scan_product_osv.assert_called_once_with(product)

        # Check the composed message
        self.assertEqual(
            "Imported observations for 1 products from API configurations."
            "\nImported observations for 1 products from VulnerableCode scanning."
            "\nEnqueued OSV scanning for 1 products as separate background tasks.",
            self._get_task_message(),
        )

    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.task_osv_scan_product")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.Settings.load")
    def test_task_api_import_enqueues_eligible_products(self, mock_settings_load, mock_task_osv_scan_product):
        settings = Settings()
        settings.feature_automatic_api_import = False
        settings.feature_automatic_osv_scanning = True
        settings.feature_automatic_vulnerablecode_scanning = False
        mock_settings_load.return_value = settings

        product_1 = _create_product("product_osv_1")
        product_2 = _create_product("product_osv_2")
        Product.objects.create(name="product_osv_manual", osv_enabled=True, automatic_osv_scanning_enabled=False)
        Product.objects.create(name="product_no_osv", osv_enabled=False, automatic_osv_scanning_enabled=True)

        task_api_import()

        self.assertCountEqual([call(product_1.pk), call(product_2.pk)], mock_task_osv_scan_product.call_args_list)
        self.assertEqual(Status.STATUS_SUCCESS, self._get_task().status)
        self.assertTrue(
            self._get_task_message().endswith("\nEnqueued OSV scanning for 2 products as separate background tasks.")
        )

    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.VulnerableCodeScanner.scan_product")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.OSVScanner.scan_product")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.api_import_observations")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.Api_Configuration.objects.filter")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.Settings.load")
    def test_task_api_import_api_disabled(
        self,
        mock_settings_load,
        mock_api_config_filter,
        mock_api_import_observations,
        mock_scan_product_osv,
        mock_scan_product_vc,
    ):
        # Setup
        # Mock settings
        settings = Settings()
        settings.feature_automatic_api_import = False
        settings.feature_automatic_osv_scanning = True
        settings.feature_automatic_vulnerablecode_scanning = True
        settings.vulnerablecode_base_url = "http://vulnerablecode.example.com"
        mock_settings_load.return_value = settings

        product = _create_product("product_osv_vulnerablecode")

        # Mock import results
        mock_scan_product_osv.return_value = (4, 5, 6)  # new, updated, resolved
        mock_scan_product_vc.return_value = (7, 8, 9)  # new, updated, resolved

        # Execute
        task_api_import()

        # Assert
        # Check API import was not called
        mock_api_config_filter.assert_not_called()
        mock_api_import_observations.assert_not_called()

        # Check OSV and VulnerableCode scanning was called
        mock_scan_product_osv.assert_called_once_with(product)
        mock_scan_product_vc.assert_called_once_with(product)

    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.VulnerableCodeScanner.scan_product")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.task_osv_scan_product")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.api_import_observations")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.Api_Configuration.objects.filter")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.Settings.load")
    def test_task_api_import_osv_disabled(
        self,
        mock_settings_load,
        mock_api_config_filter,
        mock_api_import_observations,
        mock_task_osv_scan_product,
        mock_scan_product_vc,
    ):
        # Setup
        # Mock settings
        settings = Settings()
        settings.feature_automatic_api_import = True
        settings.feature_automatic_osv_scanning = False
        settings.feature_automatic_vulnerablecode_scanning = True
        settings.vulnerablecode_base_url = "http://vulnerablecode.example.com"
        mock_settings_load.return_value = settings

        # Mock API configurations
        mock_api_config = MagicMock()
        mock_api_config_filter.return_value = [mock_api_config]

        product = _create_product("product_osv_vulnerablecode")

        # Mock import results
        mock_api_import_observations.return_value = (1, 2, 3)  # new, updated, resolved
        mock_scan_product_vc.return_value = (7, 8, 9)  # new, updated, resolved

        # Execute
        task_api_import()

        # Assert
        # Check API import was called
        mock_api_config_filter.assert_called_once_with(automatic_import_enabled=True)
        mock_api_import_observations.assert_called_once()

        # Check no OSV scan was enqueued, but VulnerableCode scanning was called
        mock_task_osv_scan_product.assert_not_called()
        mock_scan_product_vc.assert_called_once_with(product)
        self.assertTrue(self._get_task_message().endswith("\nOSV scanning is disabled in settings."))

    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.VulnerableCodeScanner.scan_product")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.OSVScanner.scan_product")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.api_import_observations")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.Api_Configuration.objects.filter")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.Settings.load")
    def test_task_api_import_vulnerablecode_disabled(
        self,
        mock_settings_load,
        mock_api_config_filter,
        mock_api_import_observations,
        mock_scan_product_osv,
        mock_scan_product_vc,
    ):
        # Setup
        # Mock settings
        settings = Settings()
        settings.feature_automatic_api_import = True
        settings.feature_automatic_osv_scanning = True
        settings.feature_automatic_vulnerablecode_scanning = False
        settings.vulnerablecode_base_url = "http://vulnerablecode.example.com"
        mock_settings_load.return_value = settings

        # Mock API configurations
        mock_api_config = MagicMock()
        mock_api_config_filter.return_value = [mock_api_config]

        product = _create_product("product_osv_vulnerablecode")

        # Mock import results
        mock_api_import_observations.return_value = (1, 2, 3)  # new, updated, resolved
        mock_scan_product_osv.return_value = (4, 5, 6)  # new, updated, resolved

        # Execute
        task_api_import()

        # Assert
        # Check API import was called
        mock_api_config_filter.assert_called_once_with(automatic_import_enabled=True)
        mock_api_import_observations.assert_called_once()

        # Check VulnerableCode scanning was not called, but OSV scanning was
        mock_scan_product_vc.assert_not_called()
        mock_scan_product_osv.assert_called_once_with(product)

    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.VulnerableCodeScanner.scan_product")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.OSVScanner.scan_product")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.api_import_observations")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.Api_Configuration.objects.filter")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.Settings.load")
    def test_task_api_import_vulnerablecode_no_base_url(
        self,
        mock_settings_load,
        mock_api_config_filter,
        mock_api_import_observations,
        mock_scan_product_osv,
        mock_scan_product_vc,
    ):
        # Setup
        # Mock settings
        settings = Settings()
        settings.feature_automatic_api_import = True
        settings.feature_automatic_osv_scanning = True
        settings.feature_automatic_vulnerablecode_scanning = True
        settings.vulnerablecode_base_url = ""
        mock_settings_load.return_value = settings

        # Mock API configurations
        mock_api_config = MagicMock()
        mock_api_config_filter.return_value = [mock_api_config]

        product = _create_product("product_osv_vulnerablecode")

        # Mock import results
        mock_api_import_observations.return_value = (1, 2, 3)  # new, updated, resolved
        mock_scan_product_osv.return_value = (4, 5, 6)  # new, updated, resolved

        # Execute
        task_api_import()

        # Assert
        # Check API import was called
        mock_api_config_filter.assert_called_once_with(automatic_import_enabled=True)
        mock_api_import_observations.assert_called_once()

        # Check VulnerableCode scanning was not called, but OSV scanning was
        mock_scan_product_vc.assert_not_called()
        mock_scan_product_osv.assert_called_once_with(product)

    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.handle_task_exception")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.api_import_observations")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.Api_Configuration.objects.filter")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.Settings.load")
    def test_task_api_import_api_exception_handling(
        self,
        mock_settings_load,
        mock_api_config_filter,
        mock_api_import_observations,
        mock_handle_task_exception,
    ):
        # Setup
        # Mock settings
        settings = Settings()
        settings.feature_automatic_api_import = True
        settings.feature_automatic_osv_scanning = False
        settings.feature_automatic_vulnerablecode_scanning = False
        mock_settings_load.return_value = settings

        # Mock API configurations
        mock_api_config = MagicMock()
        mock_api_config_filter.return_value = [mock_api_config]

        # Mock API import to raise exception
        test_exception = Exception("Test API import exception")
        mock_api_import_observations.side_effect = test_exception

        # Execute
        task_api_import()

        # Assert
        # Check exception was handled
        mock_handle_task_exception.assert_called_once_with(test_exception, product=mock_api_config.product)

        # Check the task is marked as failed, although the remaining imports have been executed
        self.assertEqual(Status.STATUS_FAILURE, self._get_task().status)

    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.handle_task_exception")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.VulnerableCodeScanner.scan_product")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.Product.objects.filter")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.Settings.load")
    def test_task_api_import_vulnerablecode_exception_handling(
        self,
        mock_settings_load,
        mock_product_filter,
        mock_scan_product,
        mock_handle_task_exception,
    ):
        # Setup
        # Mock settings
        settings = Settings()
        settings.feature_automatic_api_import = False
        settings.feature_automatic_osv_scanning = False
        settings.feature_automatic_vulnerablecode_scanning = True
        settings.vulnerablecode_base_url = "http://vulnerablecode.example.com"
        mock_settings_load.return_value = settings

        # Mock products
        mock_product = MagicMock()
        mock_product_filter.return_value = [mock_product]

        # Mock scan_product to raise exception
        test_exception = Exception("Test VulnerableCode scanning exception")
        mock_scan_product.side_effect = test_exception

        # Execute
        task_api_import()

        # Assert
        # Check exception was handled
        mock_handle_task_exception.assert_called_once_with(test_exception, product=mock_product)

        # Check the failed imports are counted with the VulnerableCode counter
        self.assertIn("\nVulnerableCode scanning failed for 1 products.", self._get_task_message())

        # Check the task is marked as failed, although the remaining imports have been executed
        self.assertEqual(Status.STATUS_FAILURE, self._get_task().status)

    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.handle_task_exception")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.VulnerableCodeScanner.scan_product")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.OSVScanner.scan_product")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.api_import_observations")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.Api_Configuration.objects.filter")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.Settings.load")
    def test_task_api_import_all_failed(
        self,
        mock_settings_load,
        mock_api_config_filter,
        mock_api_import_observations,
        mock_scan_product_osv,
        mock_scan_product_vc,
        mock_handle_task_exception,
    ):
        # Setup
        # Mock settings
        settings = Settings()
        settings.feature_automatic_api_import = True
        settings.feature_automatic_osv_scanning = True
        settings.feature_automatic_vulnerablecode_scanning = True
        settings.vulnerablecode_base_url = "http://vulnerablecode.example.com"
        mock_settings_load.return_value = settings

        # Mock API configurations
        mock_api_config = MagicMock()
        mock_api_config_filter.return_value = [mock_api_config]

        _create_product("product_osv_vulnerablecode")

        # Mock every stage to raise an exception
        mock_api_import_observations.side_effect = Exception("Test API import exception")
        mock_scan_product_osv.side_effect = Exception("Test OSV scanning exception")
        mock_scan_product_vc.side_effect = Exception("Test VulnerableCode scanning exception")

        # Execute
        task_api_import()

        # Assert
        # Check every exception was handled, the one of OSV scanning by the enqueued task
        self.assertEqual(3, mock_handle_task_exception.call_count)

        # A failed OSV scan fails its own task, not the one that has enqueued it
        message = self._get_task_message()
        self.assertIn("\nAPI import failed for 1 configurations.", message)
        self.assertIn("\nVulnerableCode scanning failed for 1 products.", message)
        self.assertNotIn("OSV scanning failed", message)

        self.assertEqual(Status.STATUS_FAILURE, self._get_task().status)

    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.api_import_observations")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.Api_Configuration.objects.filter")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.Settings.load")
    def test_task_api_import_no_service(
        self,
        mock_settings_load,
        mock_api_config_filter,
        mock_api_import_observations,
    ):
        # Setup
        # Mock settings
        settings = Settings()
        settings.feature_automatic_api_import = True
        settings.feature_automatic_osv_scanning = False
        settings.feature_automatic_vulnerablecode_scanning = False
        mock_settings_load.return_value = settings

        # Mock API configuration without a service
        mock_api_config = MagicMock()
        mock_api_config.automatic_import_service = None
        mock_api_config_filter.return_value = [mock_api_config]

        # Mock import results
        mock_api_import_observations.return_value = (1, 2, 3)  # new, updated, resolved

        # Execute
        task_api_import()

        # Assert
        # Check the service name defaults to an empty string
        mock_api_import_observations.assert_called_once()
        api_import_params = mock_api_import_observations.call_args[0][0]
        self.assertEqual("", api_import_params.service_name)


class TestTaskOSVScanProduct(BaseTestCase):
    def test_priority_below_default(self):
        # Huey dequeues tasks with a higher priority first, all other tasks have the default priority 0
        self.assertLess(task_osv_scan_product.s(1).priority, 0)

    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.OSVScanner.scan_product")
    def test_scan(self, mock_scan_product):
        product = _create_product("product_osv")
        mock_scan_product.return_value = (1, 2, 3)  # new, updated, resolved

        with self.assertLogs("secobserve.import_observations", level="INFO") as logs:
            task_osv_scan_product.call_local(product.pk)

        mock_scan_product.assert_called_once_with(product)
        self.assertIn("OSV scanning - product_osv: 1 new, 2 updated, 3 resolved", logs.output[0])

    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.OSVScanner.scan_product")
    def test_product_deleted(self, mock_scan_product):
        product = _create_product("product_osv")
        product_id = product.pk
        product.delete()

        task_osv_scan_product.call_local(product_id)

        mock_scan_product.assert_not_called()

    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.OSVScanner.scan_product")
    def test_product_disabled(self, mock_scan_product):
        product_manual = Product.objects.create(
            name="product_osv_manual", osv_enabled=True, automatic_osv_scanning_enabled=False
        )
        product_no_osv = Product.objects.create(
            name="product_no_osv", osv_enabled=False, automatic_osv_scanning_enabled=True
        )

        task_osv_scan_product.call_local(product_manual.pk)
        task_osv_scan_product.call_local(product_no_osv.pk)

        mock_scan_product.assert_not_called()

    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.OSVScanner.scan_product")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.Settings.load")
    def test_feature_disabled(self, mock_settings_load, mock_scan_product):
        settings = Settings()
        settings.feature_automatic_osv_scanning = False
        mock_settings_load.return_value = settings
        product = _create_product("product_osv")

        task_osv_scan_product.call_local(product.pk)

        mock_scan_product.assert_not_called()

    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.handle_task_exception")
    @patch("application.background_tasks.periodic_tasks.import_observations_tasks.OSVScanner.scan_product")
    def test_exception(self, mock_scan_product, mock_handle_task_exception):
        product = _create_product("product_osv")
        test_exception = Exception("Test OSV scanning exception")
        mock_scan_product.side_effect = test_exception

        with self.assertRaises(Exception) as context:
            task_osv_scan_product.call_local(product.pk)

        self.assertIs(test_exception, context.exception)
        mock_handle_task_exception.assert_called_once_with(test_exception, product=product)
