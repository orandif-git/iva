"""Turns an admin-written request recipe into a real request, safely.

The recipe (URL, headers, JSON body) is parsed JSON. Variables like {{recipient.phone}} are filled into the *values*
of that structure, one string at a time, so a customer-controlled value (a camera name, a plate) can never add a field,
close a quote or inject a header: the structure is fixed before any data touches it.

Two small structural helpers exist for lists, because WhatsApp-style APIs want one list item per variable:
    {"$each": "vars", "do": {"type": "text", "text": "{{item}}"}}   one list item for every entry of `vars`
    {"$if": "media.url", "then": {...}}                              the item is included only when that variable is not empty
"""
import json
import re
from urllib.parse import quote

VAR = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")
MAX_DEPTH = 12
MAX_BODY_BYTES = 64 * 1024

# What a recipe may refer to. Anything else is rejected when the recipe is saved, not discovered at 3 am.
SCALARS = {"recipient.phone", "recipient.phone_digits", "recipient.name", "recipient.email", "template.name", "template.language",
           "media.url", "media.type", "media.is_image", "media.is_video", "link.url", "link.token", "message.id", "alert.title", "alert.body", "alert.camera", "alert.time",
           "email.to", "email.subject", "email.text", "email.html"}
LISTS = {"vars"}
SECRET_PREFIX = "secret."
INDEXED = re.compile(r"^vars\.([1-9][0-9]?)$")
LOOP = {"item", "index"}


class RenderError(ValueError):
    pass


def _is_known(name: str, secrets: set[str], in_loop: bool) -> bool:
    if name in SCALARS or INDEXED.match(name):
        return True
    if name.startswith(SECRET_PREFIX):
        return name[len(SECRET_PREFIX):] in secrets
    return in_loop and name in LOOP


def names_in(s: str) -> list[str]:
    return VAR.findall(s)


def validate_structure(node, secrets: set[str], where: str = "body", depth: int = 0, in_loop: bool = False) -> None:
    """Raises RenderError describing the first problem, in words an administrator can act on."""
    if depth > MAX_DEPTH:
        raise RenderError(f"{where}: nested too deeply")
    if isinstance(node, str):
        if "{{" in VAR.sub("", node) or "}}" in VAR.sub("", node):
            raise RenderError(f"{where}: a {{{{ or }}}} is not closed properly")
        for n in names_in(node):
            if not _is_known(n, secrets, in_loop):
                raise RenderError(f"{where}: unknown variable {{{{{n}}}}}")
    elif isinstance(node, dict):
        for k, v in node.items():
            if not isinstance(k, str) or VAR.search(k):
                raise RenderError(f"{where}: field names cannot contain variables")
            validate_structure(v, secrets, f"{where}.{k}", depth + 1, in_loop)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            if isinstance(v, dict) and "$each" in v:
                if set(v) != {"$each", "do"} or v["$each"] not in LISTS:
                    raise RenderError(f"{where}[{i}]: $each needs a list name ({', '.join(sorted(LISTS))}) and a 'do' part")
                validate_structure(v["do"], secrets, f"{where}[{i}].do", depth + 1, True)
            elif isinstance(v, dict) and "$if" in v:
                if set(v) != {"$if", "then"} or not isinstance(v["$if"], str) or not _is_known(v["$if"], secrets, in_loop) and v["$if"] not in LISTS:
                    raise RenderError(f"{where}[{i}]: $if needs a known variable and a 'then' part")
                validate_structure(v["then"], secrets, f"{where}[{i}].then", depth + 1, in_loop)
            else:
                validate_structure(v, secrets, f"{where}[{i}]", depth + 1, in_loop)
    elif node is None or isinstance(node, (bool, int, float)):
        return
    else:
        raise RenderError(f"{where}: unsupported value")


def _lookup(ctx: dict, name: str) -> str:
    if name.startswith(SECRET_PREFIX):
        return str(ctx.get("secrets", {}).get(name[len(SECRET_PREFIX):], ""))
    m = INDEXED.match(name)
    if m:
        vs = ctx.get("vars", [])
        i = int(m.group(1)) - 1
        return str(vs[i]) if i < len(vs) else ""
    return str(ctx.get("values", {}).get(name, ""))


def fill(s: str, ctx: dict, *, encode: bool = False) -> str:
    def sub(m):
        v = _lookup(ctx, m.group(1))
        return quote(v, safe="") if encode else v
    return VAR.sub(sub, s)


def _truthy(name: str, ctx: dict) -> bool:
    if name in LISTS:
        return bool(ctx.get(name))
    return bool(_lookup(ctx, name))


def render(node, ctx: dict):
    if isinstance(node, str):
        return fill(node, ctx)
    if isinstance(node, dict):
        return {k: render(v, ctx) for k, v in node.items()}
    if isinstance(node, list):
        out = []
        for v in node:
            if isinstance(v, dict) and "$each" in v:
                for i, item in enumerate(ctx.get(v["$each"], []), 1):
                    out.append(render(v["do"], {**ctx, "values": {**ctx.get("values", {}), "item": str(item), "index": str(i)}}))
            elif isinstance(v, dict) and "$if" in v:
                if _truthy(v["$if"], ctx):
                    out.append(render(v["then"], ctx))
            else:
                out.append(render(v, ctx))
        return out
    return node


def render_body(body, ctx: dict) -> bytes:
    data = json.dumps(render(body, ctx), ensure_ascii=False).encode()
    if len(data) > MAX_BODY_BYTES:
        raise RenderError("the request body is too large")
    return data


FORBIDDEN_HEADERS = {"host", "content-length", "transfer-encoding", "connection", "upgrade", "te", "trailer", "proxy-authorization"}
TOKEN = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}$")


def render_headers(headers: dict, ctx: dict) -> dict[str, str]:
    out = {}
    for name, value in headers.items():
        if not TOKEN.match(name) or name.lower() in FORBIDDEN_HEADERS:
            raise RenderError(f"header '{name}' is not allowed")
        v = fill(str(value), ctx)
        if re.search(r"[\r\n\x00]", v):
            raise RenderError(f"header '{name}' would contain a line break")
        out[name] = v
    return out


def mask_secrets(text: str, ctx: dict) -> str:
    """Replaces every secret value in `text` so errors, logs and previews never reveal one."""
    for v in (ctx.get("secrets") or {}).values():
        if v and len(str(v)) >= 4:
            text = text.replace(str(v), "••••")
    return text
