"""Email through any SMTP server. The password is stored encrypted like every other provider secret."""
import asyncio
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from . import netguard
from .http_provider import Result

PERMANENT = {550, 551, 552, 553, 554, 556}


def validate_config(cfg: dict, *, allow_private: bool = False) -> None:
    from .render import RenderError
    if not isinstance(cfg.get("host"), str) or not cfg["host"] or any(c in cfg["host"] for c in " /\\@"):
        raise RenderError("host is required, for example smtp.example.com")
    if not isinstance(cfg.get("port"), int) or not 1 <= cfg["port"] <= 65535:
        raise RenderError("port is required, for example 587")
    if cfg.get("security", "starttls") not in ("starttls", "ssl", "none"):
        raise RenderError("security must be starttls, ssl or none")
    if "@" not in str(cfg.get("from_email", "")):
        raise RenderError("from_email is required")
    if not allow_private and cfg.get("security", "starttls") == "none":
        raise RenderError("security 'none' sends passwords in clear text and is not allowed")


def _deliver(cfg: dict, secrets: dict, msg: EmailMessage, ctx: ssl.SSLContext | None) -> None:
    sec = cfg.get("security", "starttls")
    ctx = ctx or ssl.create_default_context()
    cls = smtplib.SMTP_SSL if sec == "ssl" else smtplib.SMTP
    kw = {"context": ctx} if sec == "ssl" else {}
    with cls(cfg["host"], cfg["port"], timeout=20, **kw) as s:
        if sec == "starttls":
            s.starttls(context=ctx)
        if cfg.get("username"):
            s.login(cfg["username"], str(secrets.get("password", "")))
        s.send_message(msg)


async def send(cfg: dict, secrets: dict, msg: dict, *, allow_private: bool = False, ssl_context=None) -> Result:
    """msg: to, name, subject, text, html"""
    try:
        await netguard.resolve(cfg["host"], cfg["port"], allow_private=allow_private)
    except netguard.BlockedAddress as e:
        return Result(False, permanent=True, error=str(e))
    m = EmailMessage()
    m["From"] = formataddr((cfg.get("from_name", "Jas iVision"), cfg["from_email"]))
    m["To"] = formataddr((msg.get("name") or "", msg["to"]))
    m["Subject"] = msg["subject"].replace("\r", " ").replace("\n", " ")[:200]
    mid = make_msgid(domain=cfg["from_email"].split("@")[-1])
    m["Message-ID"] = mid
    m.set_content(msg["text"])
    if msg.get("html"):
        m.add_alternative(msg["html"], subtype="html")
    for fname, data, mime in msg.get("attachments", []):
        main, _, sub = mime.partition("/")
        m.add_attachment(data, maintype=main, subtype=sub, filename=fname)
    try:
        await asyncio.to_thread(_deliver, cfg, secrets, m, ssl_context)
    except smtplib.SMTPRecipientsRefused as e:
        return Result(False, permanent=True, error="The address was refused: " + "; ".join(str(v[1], "utf-8", "replace") for v in e.recipients.values())[:200])
    except smtplib.SMTPResponseException as e:
        return Result(False, permanent=e.smtp_code in PERMANENT or e.smtp_code == 535, error=f"The mail server answered {e.smtp_code}.")
    except (smtplib.SMTPException, OSError) as e:
        return Result(False, permanent=False, error=f"Could not send the email ({type(e).__name__}).")
    return Result(True, message_id=mid)
