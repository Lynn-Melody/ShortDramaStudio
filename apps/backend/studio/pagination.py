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
    return str(UUID(value))


def _datetime(value):
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


def encode_cursor(row, keys):
    payload = json.dumps([_encode_value(_value(row, key.field)) for key in keys], separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_cursor(cursor, keys):
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        values = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        if not isinstance(values, list) or len(values) != len(keys):
            raise ValueError("Cursor shape")
        return [CASTS[key.cast](value) for key, value in zip(keys, values, strict=True)]
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ValidationError({"cursor": "Invalid cursor."}) from exc


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


def paginate(queryset, keys, *, cursor=None, limit):
    """Return (rows, next_cursor). Key fields must be non-null and together unique."""
    if cursor:
        queryset = queryset.filter(after(keys, decode_cursor(cursor, keys)))
    rows = list(queryset.order_by(*order_by(keys))[: limit + 1])
    if len(rows) <= limit:
        return rows, None
    rows = rows[:limit]
    return rows, encode_cursor(rows[-1], keys)
