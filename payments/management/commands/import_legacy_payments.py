import sqlite3
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from db.import_sqlite_to_orm import import_payments


class Command(BaseCommand):
    help = "Перенести данные SQLite ПР7 в базу Django, сохранив ID и связи."

    def add_arguments(self, parser):
        parser.add_argument("--source", type=Path, default=settings.PAYMENTS_DATABASE)

    def handle(self, *args, **options):
        try:
            counts = import_payments(options["source"])
        except (
            OSError,
            ValueError,
            ValidationError,
            DatabaseError,
            sqlite3.Error,
        ) as error:
            raise CommandError(f"Ошибка переноса: {error}") from error
        self.stdout.write(
            self.style.SUCCESS(f"Перенос завершён. В базе Django: {counts}")
        )
