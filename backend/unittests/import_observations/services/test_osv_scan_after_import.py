from unittest.mock import patch

from django.core.files.base import File
from django.core.management import call_command

from application.commons.models import Settings
from application.core.models import Branch, Product
from application.import_observations.services.import_observations import (
    FileUploadParameters,
    file_upload_observations,
)
from unittests.base_test_case import BaseTestCase

SBOM_FILE = "unittests/import_observations/parsers/cyclone_dx/files/licenses_1.json"


class TestOSVScanAfterImport(BaseTestCase):
    def setUp(self):
        call_command(
            "loaddata",
            [
                "unittests/fixtures/initial_license_data.json",
                "unittests/fixtures/unittests_fixtures.json",
                "unittests/fixtures/unittests_license_fixtures.json",
                "unittests/fixtures/import_observations_fixtures.json",
            ],
        )

        settings = Settings.load()
        settings.feature_license_management = True
        settings.save()

        self.product = Product.objects.get(id=1)
        self.branch = Branch.objects.get(id=1)

    def _upload_sbom(self):
        with open(SBOM_FILE, "r", encoding="utf-8") as sbom:
            file_upload_observations(
                FileUploadParameters(
                    product=self.product,
                    branch=self.branch,
                    file=File(sbom),
                    service_name="",
                    docker_image_name_tag="",
                    endpoint_url="",
                    kubernetes_cluster="",
                    kubernetes_namespace="",
                    kubernetes_resource_type="",
                    kubernetes_resource_name="",
                    suppress_licenses=False,
                    sbom=True,
                )
            )

    @patch("application.import_observations.services.import_observations.request_osv_scan")
    @patch("application.import_observations.services.import_observations.process_license_components")
    def test_new_components_request_a_scan(self, mock_process_license_components, mock_request_osv_scan):
        mock_process_license_components.return_value = (2, 0, 0)

        self._upload_sbom()

        mock_request_osv_scan.assert_called_once_with(self.product, self.branch, None)

    @patch("application.import_observations.services.import_observations.request_osv_scan")
    @patch("application.import_observations.services.import_observations.process_license_components")
    def test_deleted_components_request_a_scan(self, mock_process_license_components, mock_request_osv_scan):
        mock_process_license_components.return_value = (0, 0, 3)

        self._upload_sbom()

        mock_request_osv_scan.assert_called_once_with(self.product, self.branch, None)

    @patch("application.import_observations.services.import_observations.request_osv_scan")
    @patch("application.import_observations.services.import_observations.process_license_components")
    def test_unchanged_components_do_not_request_a_scan(self, mock_process_license_components, mock_request_osv_scan):
        # Updated components are counted for every import that has seen a component again, so they
        # are not a signal that the component set has changed.
        mock_process_license_components.return_value = (0, 5, 0)

        self._upload_sbom()

        mock_request_osv_scan.assert_not_called()

    @patch("application.import_observations.services.import_observations.request_osv_scan")
    @patch("application.import_observations.services.import_observations.process_license_components")
    def test_no_scan_when_license_management_is_disabled(self, mock_process_license_components, mock_request_osv_scan):
        settings = Settings.load()
        settings.feature_license_management = False
        settings.save()

        self._upload_sbom()

        mock_process_license_components.assert_not_called()
        mock_request_osv_scan.assert_not_called()
