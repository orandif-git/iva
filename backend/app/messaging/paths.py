"""Reads values out of a JSON answer using a dotted path such as  messages.0.id"""
_MISSING = object()


def dig(data, path: str, default=None):
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return default
    return cur


def check_rule(data, rule: dict) -> bool:
    """rule: {"path": "status", "equals": "ok"} | {"path": "...", "in": [..]} | {"path": "error", "absent": true}"""
    v = dig(data, rule["path"], _MISSING)
    if rule.get("absent"):
        return v is _MISSING or v in (None, "", False)
    if v is _MISSING:
        return False
    if "equals" in rule:
        return v == rule["equals"]
    if "in" in rule:
        return v in rule["in"]
    return True
