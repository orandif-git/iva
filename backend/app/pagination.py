import base64
import json

from .errors import ApiError


def encode_cursor(values: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(values, default=str).encode()).decode()


def decode_cursor(cursor: str | None) -> dict | None:
    if not cursor:
        return None
    try:
        return json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except Exception:
        raise ApiError(400, "bad_cursor", "The page cursor is not valid.")
