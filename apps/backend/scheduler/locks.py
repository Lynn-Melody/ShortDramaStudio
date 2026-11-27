from contextlib import contextmanager

from django.db import connection


def _key(execution_id):
    return int.from_bytes(execution_id.bytes[:8], signed=True)


def try_local_lease_lock(execution_id):
    """While leasing, leave a running decision's existing lease untouched."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_xact_lock(%s::bigint)", [_key(execution_id)])
        return cursor.fetchone()[0]


@contextmanager
def decision_lock(execution_id):
    """Serialize pure decisions across workers without holding a row transaction."""
    key = _key(execution_id)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s::bigint)", [key])
        acquired = cursor.fetchone()[0]
    try:
        yield acquired
    finally:
        if acquired:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s::bigint)", [key])
