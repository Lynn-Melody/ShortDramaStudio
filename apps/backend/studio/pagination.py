"""Stable keyset cursors for public list endpoints."""

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.db.models import Q
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import ValidationError


def _uuid(value):
    if not isinstance(value, str):
        raise ValueError("Invalid UUID")
    return str(UUID(value))


def _datetime(value):
    if not isinstance(value, str):
        raise ValueError("Invalid timestamp")
    parsed = parse_datetime(value)
    if parsed is None:
        raise ValueError("Invalid timestamp")
    return parsed


def _integer(value):
    if type(value) is not int:
        raise ValueError("Invalid integer")
    return value


def _text(value):
    if not isinstance(value, str):
        raise ValueError("Invalid text")
    return value


CASTS = {"uuid": _uuid, "datetime": _datetime, "int": _integer, "text": _text}


@dataclass(frozen=True)
class Key:
    field: str
    cast: str
    descending: bool = False


def _encode_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def _value(row, field):
    return row[field] if isinstance(row, dict) else getattr(row, field)


def invalid_cursor():
    return ValidationError({"cursor": ["Invalid cursor."]})


def encode_cursor(row, keys, context=None):
    payload = {"k": [_encode_value(_value(row, key.field)) for key in keys], "c": context}
    data = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(data.encode()).decode().rstrip("=")


def _payload(cursor):
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    except (ValueError, TypeError, UnicodeError) as exc:
        raise invalid_cursor() from exc
    if not isinstance(payload, dict) or payload.keys() != {"k", "c"} or not isinstance(payload["k"], list):
        raise invalid_cursor()
    return payload


def cursor_context(cursor):
    """The query context bound into a cursor when it was issued."""
    return _payload(cursor)["c"]


def decode_cursor(cursor, keys, context=None):
    payload = _payload(cursor)
    values = payload["k"]
    if payload["c"] != context or len(values) != len(keys):
        raise invalid_cursor()
    try:
        return [CASTS[key.cast](value) for key, value in zip(keys, values, strict=True)]
    except (ValueError, TypeError, AttributeError) as exc:
        raise invalid_cursor() from exc


def after(keys, values):
    """Rows strictly after values in the lexicographic order given by keys."""
    condition = Q(pk__in=[])
    for index, key in enumerate(keys):
        step = Q(**{f"{key.field}__{'lt' if key.descending else 'gt'}": values[index]})
        for previous, value in zip(keys[:index], values[:index], strict=True):
            step &= Q(**{previous.field: value})
        condition |= step
    return condition


def order_by(keys):
    return [f"-{key.field}" if key.descending else key.field for key in keys]


def paginate(queryset, keys, *, cursor=None, limit, context=None):
    """Return (rows, next_cursor). Key fields must be non-null and together unique.

    context is a JSON value bound into issued cursors; a cursor issued for another context is rejected.
    """
    if cursor:
        queryset = queryset.filter(after(keys, decode_cursor(cursor, keys, context)))
    rows = list(queryset.order_by(*order_by(keys))[: limit + 1])
    if len(rows) <= limit:
        return rows, None
    rows = rows[:limit]
    return rows, encode_cursor(rows[-1], keys, context)
