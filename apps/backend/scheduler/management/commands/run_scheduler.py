import logging
from time import sleep

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import InterfaceError, OperationalError, close_old_connections, connection
from django.utils.module_loading import import_string

from scheduler.worker import Worker, configured_scheduler


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run the task scheduler and durable dispatcher."

    def add_arguments(self, parser):
        parser.add_argument("--check", action="store_true", help="Check the database and exit.")
        parser.add_argument("--once", action="store_true", help="Run one scheduling tick and exit.")
        parser.add_argument("--poll-interval", type=float, default=0.5, help="Idle polling interval in seconds.")

    def handle(self, *args, **options):
        if options["check"]:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            return
        if options["poll_interval"] <= 0:
            raise CommandError("Polling interval must be positive")
        dispatcher = getattr(settings, "SCHEDULER_WORKFLOW_DISPATCHER", None)
        worker = Worker(configured_scheduler(), dispatch_workflow=import_string(dispatcher) if dispatcher else None)
        if options["once"]:
            worker.tick()
            return
        self.stdout.write("Scheduler ready.")
        try:
            while True:
                try:
                    close_old_connections()
                    did_work = worker.tick()
                except (InterfaceError, OperationalError):
                    logger.exception("Scheduler database unavailable")
                    connection.close()
                    did_work = False
                if not did_work:
                    sleep(options["poll_interval"])
        except KeyboardInterrupt:
            return
