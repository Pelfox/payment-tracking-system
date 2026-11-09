import os

import django
import pytest
from django.db import transaction
from django.test import Client
from django.test.utils import (
    setup_databases,
    setup_test_environment,
    teardown_databases,
    teardown_test_environment,
)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()


@pytest.fixture(scope="session")
def django_database():
    setup_test_environment()
    configuration = setup_databases(verbosity=0, interactive=False)
    try:
        yield
    finally:
        teardown_databases(configuration, verbosity=0)
        teardown_test_environment()


@pytest.fixture
def orm_database(django_database):
    with transaction.atomic():
        yield
        transaction.set_rollback(True)


@pytest.fixture
def client(orm_database):
    return Client()
