from unittest.mock import patch

from rest_framework.status import (
    HTTP_202_ACCEPTED,
    HTTP_204_NO_CONTENT,
    HTTP_400_BAD_REQUEST,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_503_SERVICE_UNAVAILABLE,
)
from rest_framework.test import APIClient

from application.access_control.models import User
from application.commons.services.global_request import _requests
from application.core.models import Product
from application.import_observations.models import Parser
from application.rules.models import Rule, Rule_Simulation
from application.rules.services.simulator import get_rule_definition_hash
from application.rules.types import Rule_Simulation_Status, Rule_Status
from unittests.base_test_case import BaseTestCase


class TestRuleSimulationViews(BaseTestCase):
    def setUp(self) -> None:
        self.admin = User.objects.create(username="simulation_admin", is_superuser=True)
        self.internal_user = User.objects.create(username="simulation_user")
        self.parser = Parser.objects.create(name="simulation_parser", type="SAST", source="File")
        Product.objects.bulk_create(
            [
                Product(name="simulation_product"),
                Product(name="other_simulation_product"),
            ]
        )
        self.product = Product.objects.get(name="simulation_product")
        self.other_product = Product.objects.get(name="other_simulation_product")
        self.general_rule = Rule.objects.create(
            name="simulation_general_rule",
            type="Fields",
            approval_status=Rule_Status.RULE_STATUS_AUTO_APPROVED,
        )
        self.product_rule = Rule.objects.create(
            name="simulation_product_rule",
            product=self.product,
            type="Fields",
            approval_status=Rule_Status.RULE_STATUS_AUTO_APPROVED,
        )

    def tearDown(self) -> None:
        # GlobalRequestMiddleware intentionally retains the latest request in
        # the test process. Do not leak rolled-back test users to later tests.
        _requests.clear()
        super().tearDown()

    @patch("application.rules.api.views.run_rule_simulation")
    @patch("application.access_control.services.api_token_authentication.APITokenAuthentication.authenticate")
    def test_general_rule_simulation_is_queued(self, mock_authentication, mock_run_rule_simulation):
        mock_authentication.return_value = self.admin, None

        response = APIClient().post(
            f"/api/general_rules/{self.general_rule.pk}/simulate/",
            data={"products": [self.product.pk], "parser": self.parser.pk, "scanner_prefix": "scanner"},
            format="json",
        )

        self.assertEqual(HTTP_202_ACCEPTED, response.status_code)
        simulation = Rule_Simulation.objects.get(pk=response.data["id"])
        self.assertEqual(Rule_Simulation_Status.STATUS_QUEUED, simulation.status)
        self.assertEqual([self.product.pk], simulation.products)
        self.assertEqual(self.parser.pk, simulation.parser_id)
        mock_run_rule_simulation.assert_called_once_with(str(simulation.pk))

    @patch("application.rules.api.views.run_rule_simulation")
    @patch("application.access_control.services.api_token_authentication.APITokenAuthentication.authenticate")
    def test_general_rule_simulation_requires_superuser(self, mock_authentication, mock_run_rule_simulation):
        mock_authentication.return_value = self.internal_user, None

        response = APIClient().post(f"/api/general_rules/{self.general_rule.pk}/simulate/", data={}, format="json")

        self.assertEqual(HTTP_403_FORBIDDEN, response.status_code)
        mock_run_rule_simulation.assert_not_called()

    @patch("application.rules.api.views.run_rule_simulation")
    @patch("application.access_control.services.api_token_authentication.APITokenAuthentication.authenticate")
    def test_product_rule_rejects_product_outside_rule_scope(self, mock_authentication, mock_run_rule_simulation):
        mock_authentication.return_value = self.admin, None

        response = APIClient().post(
            f"/api/product_rules/{self.product_rule.pk}/simulate/",
            data={"products": [self.other_product.pk]},
            format="json",
        )

        self.assertEqual(HTTP_400_BAD_REQUEST, response.status_code)
        mock_run_rule_simulation.assert_not_called()

    @patch("application.rules.api.views.run_rule_simulation")
    @patch("application.access_control.services.api_token_authentication.APITokenAuthentication.authenticate")
    def test_concurrency_limit_returns_conflict(self, mock_authentication, mock_run_rule_simulation):
        mock_authentication.return_value = self.admin, None
        self._create_simulation(status=Rule_Simulation_Status.STATUS_RUNNING)

        response = APIClient().post(f"/api/general_rules/{self.general_rule.pk}/simulate/", data={}, format="json")

        self.assertEqual(HTTP_409_CONFLICT, response.status_code)
        mock_run_rule_simulation.assert_not_called()

    @patch("application.rules.api.views.run_rule_simulation")
    @patch("application.access_control.services.api_token_authentication.APITokenAuthentication.authenticate")
    def test_enqueue_failure_marks_simulation_failed(self, mock_authentication, mock_run_rule_simulation):
        mock_authentication.return_value = self.admin, None
        mock_run_rule_simulation.side_effect = RuntimeError("queue unavailable")

        response = APIClient().post(f"/api/general_rules/{self.general_rule.pk}/simulate/", data={}, format="json")

        self.assertEqual(HTTP_503_SERVICE_UNAVAILABLE, response.status_code)
        simulation = Rule_Simulation.objects.get()
        self.assertEqual(Rule_Simulation_Status.STATUS_FAILED, simulation.status)
        self.assertEqual("queue unavailable", simulation.error_message)

    @patch("application.access_control.services.api_token_authentication.APITokenAuthentication.authenticate")
    def test_user_can_retrieve_own_simulation(self, mock_authentication):
        mock_authentication.return_value = self.admin, None
        simulation = self._create_simulation(status=Rule_Simulation_Status.STATUS_COMPLETED)

        response = APIClient().get(f"/api/rule_simulations/{simulation.pk}/")

        self.assertEqual(200, response.status_code)
        self.assertEqual(str(simulation.pk), response.data["id"])

    @patch("application.access_control.services.api_token_authentication.APITokenAuthentication.authenticate")
    def test_user_cannot_retrieve_another_users_simulation(self, mock_authentication):
        simulation = self._create_simulation(status=Rule_Simulation_Status.STATUS_COMPLETED)
        mock_authentication.return_value = self.internal_user, None

        response = APIClient().get(f"/api/rule_simulations/{simulation.pk}/")

        self.assertEqual(HTTP_404_NOT_FOUND, response.status_code)

    @patch("application.access_control.services.api_token_authentication.APITokenAuthentication.authenticate")
    def test_delete_cancels_simulation(self, mock_authentication):
        mock_authentication.return_value = self.admin, None
        simulation = self._create_simulation(status=Rule_Simulation_Status.STATUS_RUNNING)

        response = APIClient().delete(f"/api/rule_simulations/{simulation.pk}/")

        self.assertEqual(HTTP_204_NO_CONTENT, response.status_code)
        simulation.refresh_from_db()
        self.assertEqual(Rule_Simulation_Status.STATUS_CANCELLED, simulation.status)
        self.assertIsNotNone(simulation.finished)

    def _create_simulation(self, status: str) -> Rule_Simulation:
        return Rule_Simulation.objects.create(
            rule=self.general_rule,
            user=self.admin,
            status=status,
            rule_definition_hash=get_rule_definition_hash(self.general_rule),
        )
