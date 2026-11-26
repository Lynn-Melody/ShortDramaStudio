from threading import Event

from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Start the development scheduler process."

    def add_arguments(self, parser):
        parser.add_argument("--check", action="store_true", help="Check the database and exit.")

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        connection.close()
        if not options["check"]:
            self.stdout.write("Scheduler process ready. Task dispatch is pending implementation.")
            Event().wait()
