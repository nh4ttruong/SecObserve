from huey import crontab
from huey.contrib.djhuey import db_periodic_task, db_task

from application.notifications.services.tasks import handle_task_exception
from application.rules.models import Rule_Simulation
from application.rules.services.simulation_jobs import (
    delete_expired_rule_simulations,
    execute_rule_simulation,
)


@db_task(priority=-10)
def run_rule_simulation(simulation_id: str) -> None:
    try:
        execute_rule_simulation(simulation_id)
    except Exception as exception:
        simulation = Rule_Simulation.objects.filter(pk=simulation_id).select_related("user").first()
        handle_task_exception(exception, simulation.user if simulation else None)


@db_periodic_task(crontab(hour="3", minute="17"))
def cleanup_rule_simulations() -> None:
    delete_expired_rule_simulations()
