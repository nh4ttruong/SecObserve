from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import override_settings
from django.utils import timezone

from application.access_control.models import User
from application.core.models import Product
from application.import_observations.models import Parser
from application.rules.models import Rule, Rule_Simulation
from application.rules.services.simulation_jobs import (
    RuleDefinitionChanged,
    SimulationLimitExceeded,
    create_rule_simulation,
    delete_expired_rule_simulations,
    execute_rule_simulation,
)
from application.rules.services.simulator import (
    SimulationScope,
    get_rule_definition_hash,
)
from application.rules.types import Rule_Simulation_Status, Rule_Status
from unittests.base_test_case import BaseTestCase


class TestSimulationJobs(BaseTestCase):
    def setUp(self) -> None:
        self.admin = User.objects.create(username="simulation_job_admin", is_superuser=True)
        self.parser = Parser.objects.create(name="simulation_job_parser", type="SAST", source="File")
        Product.objects.bulk_create([Product(name="simulation_job_product")])
        self.product = Product.objects.get(name="simulation_job_product")
        self.rule = Rule.objects.create(
            name="simulation_job_rule",
            type="Fields",
            approval_status=Rule_Status.RULE_STATUS_AUTO_APPROVED,
        )

    @override_settings(RULE_SIMULATION_MAX_CANDIDATES=0, RULE_SIMULATION_MAX_CONCURRENT=1)
    def test_create_rule_simulation_records_scope_and_candidate_count(self):
        scope = SimulationScope(
            product_ids=(self.product.pk,),
            parser_id=self.parser.pk,
            scanner_prefix="scanner",
        )

        simulation = create_rule_simulation(self.rule, self.admin, scope)

        self.assertEqual([self.product.pk], simulation.products)
        self.assertEqual(self.parser.pk, simulation.parser_id)
        self.assertEqual("scanner", simulation.scanner_prefix)
        self.assertEqual(get_rule_definition_hash(self.rule), simulation.rule_definition_hash)

    @override_settings(RULE_SIMULATION_MAX_CANDIDATES=1, RULE_SIMULATION_MAX_CONCURRENT=1)
    @patch("application.rules.services.simulation_jobs.build_simulation_queryset")
    def test_create_rule_simulation_rejects_too_many_candidates(self, mock_build_queryset):
        mock_build_queryset.return_value.count.return_value = 2

        with self.assertRaises(SimulationLimitExceeded):
            create_rule_simulation(self.rule, self.admin, SimulationScope())

    @override_settings(RULE_SIMULATION_MAX_CANDIDATES=0)
    @patch("application.rules.services.simulation_jobs.simulate_rule")
    @patch("application.rules.services.simulation_jobs.build_simulation_queryset")
    def test_execute_rule_simulation_persists_results(self, mock_build_queryset, mock_simulate_rule):
        observation = MagicMock()
        observation.pk = 123
        mock_build_queryset.return_value.count.return_value = 2

        def simulate(*args, **kwargs):
            kwargs["progress_callback"](2)
            return 1, [observation]

        mock_simulate_rule.side_effect = simulate
        simulation = self._create_simulation()

        execute_rule_simulation(str(simulation.pk))

        simulation.refresh_from_db()
        self.assertEqual(Rule_Simulation_Status.STATUS_COMPLETED, simulation.status)
        self.assertEqual(2, simulation.processed_count)
        self.assertEqual(1, simulation.match_count)
        self.assertEqual([observation.pk], simulation.result_observation_ids)
        self.assertIsNotNone(simulation.started)
        self.assertIsNotNone(simulation.finished)

    @patch("application.rules.services.simulation_jobs.simulate_rule")
    def test_cancelled_simulation_does_not_execute(self, mock_simulate_rule):
        simulation = self._create_simulation(status=Rule_Simulation_Status.STATUS_CANCELLED)

        execute_rule_simulation(str(simulation.pk))

        mock_simulate_rule.assert_not_called()

    @patch("application.rules.services.simulation_jobs.simulate_rule")
    @patch("application.rules.services.simulation_jobs.build_simulation_queryset")
    def test_cancellation_during_candidate_count_does_not_start_job(self, mock_build_queryset, mock_simulate_rule):
        simulation = self._create_simulation()

        def count_candidates():
            Rule_Simulation.objects.filter(pk=simulation.pk).update(
                status=Rule_Simulation_Status.STATUS_CANCELLED,
                finished=timezone.now(),
            )
            return 0

        mock_build_queryset.return_value.count.side_effect = count_candidates

        execute_rule_simulation(str(simulation.pk))

        simulation.refresh_from_db()
        self.assertEqual(Rule_Simulation_Status.STATUS_CANCELLED, simulation.status)
        mock_simulate_rule.assert_not_called()

    @patch("application.rules.services.simulation_jobs.simulate_rule")
    def test_completed_simulation_is_not_executed_again(self, mock_simulate_rule):
        simulation = self._create_simulation(status=Rule_Simulation_Status.STATUS_COMPLETED)

        execute_rule_simulation(str(simulation.pk))

        mock_simulate_rule.assert_not_called()

    def test_rule_change_marks_simulation_failed(self):
        simulation = self._create_simulation(rule_definition_hash="outdated")

        with self.assertRaises(RuleDefinitionChanged):
            execute_rule_simulation(str(simulation.pk))

        simulation.refresh_from_db()
        self.assertEqual(Rule_Simulation_Status.STATUS_FAILED, simulation.status)
        self.assertIn("rule changed", simulation.error_message.lower())

    @override_settings(RULE_SIMULATION_MAX_CANDIDATES=0)
    @patch("application.rules.services.simulation_jobs.simulate_rule")
    @patch("application.rules.services.simulation_jobs.build_simulation_queryset")
    def test_rule_change_during_simulation_marks_job_failed(self, mock_build_queryset, mock_simulate_rule):
        mock_build_queryset.return_value.count.return_value = 0
        simulation = self._create_simulation()

        def simulate(*args, **kwargs):
            Rule.objects.filter(pk=self.rule.pk).update(title="changed during simulation")
            kwargs["progress_callback"](0)
            return 0, []

        mock_simulate_rule.side_effect = simulate

        with self.assertRaises(RuleDefinitionChanged):
            execute_rule_simulation(str(simulation.pk))

        simulation.refresh_from_db()
        self.assertEqual(Rule_Simulation_Status.STATUS_FAILED, simulation.status)
        self.assertIn("rule changed", simulation.error_message.lower())

    @override_settings(RULE_SIMULATION_RETENTION_DAYS=7)
    def test_cleanup_deletes_only_expired_terminal_simulations(self):
        expired = self._create_simulation(status=Rule_Simulation_Status.STATUS_COMPLETED)
        recent = self._create_simulation(status=Rule_Simulation_Status.STATUS_FAILED)
        running = self._create_simulation(status=Rule_Simulation_Status.STATUS_RUNNING)
        old_finished = timezone.now() - timedelta(days=8)
        Rule_Simulation.objects.filter(pk__in=[expired.pk, running.pk]).update(finished=old_finished)
        Rule_Simulation.objects.filter(pk=recent.pk).update(finished=timezone.now())

        delete_expired_rule_simulations()

        self.assertFalse(Rule_Simulation.objects.filter(pk=expired.pk).exists())
        self.assertTrue(Rule_Simulation.objects.filter(pk=recent.pk).exists())
        self.assertTrue(Rule_Simulation.objects.filter(pk=running.pk).exists())

    def _create_simulation(
        self,
        status: str = Rule_Simulation_Status.STATUS_QUEUED,
        rule_definition_hash: str = "",
    ) -> Rule_Simulation:
        return Rule_Simulation.objects.create(
            rule=self.rule,
            user=self.admin,
            status=status,
            rule_definition_hash=rule_definition_hash or get_rule_definition_hash(self.rule),
        )
