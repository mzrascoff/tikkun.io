"""SMTP email sender for the daily Kalshi report.

Configuration via environment variables (placed in your shell rc file
or a .env loaded by launchd):

  KALSHI_SMTP_HOST    e.g. smtp.gmail.com
  KALSHI_SMTP_PORT    e.g. 587
  KALSHI_SMTP_USER    e.g. you@gmail.com
  KALSHI_SMTP_PASS    e.g. a Gmail "app password" (NOT your real pw)
  KALSHI_EMAIL_FROM   defaults to KALSHI_SMTP_USER
  KALSHI_EMAIL_TO     comma-separated recipient list

For Gmail you must enable 2FA and create an app password under
https://myaccount.google.com/apppasswords — Gmail will not accept your
real password over SMTP.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage


class EmailConfigError(RuntimeError):
    pass


@dataclass
class SMTPConfig:
    host: str
    port: int
    user: str
    password: str
    sender: str
    recipients: list[str]

    @classmethod
    def from_env(cls) -> "SMTPConfig":
        def req(key: str) -> str:
            v = os.environ.get(key)
            if not v:
                raise EmailConfigError(f"missing env var: {key}")
            return v

        host = req("KALSHI_SMTP_HOST")
        port = int(os.environ.get("KALSHI_SMTP_PORT", "587"))
        user = req("KALSHI_SMTP_USER")
        password = req("KALSHI_SMTP_PASS")
        sender = os.environ.get("KALSHI_EMAIL_FROM") or user
        to_raw = req("KALSHI_EMAIL_TO")
        recipients = [r.strip() for r in to_raw.split(",") if r.strip()]
        if not recipients:
            raise EmailConfigError("KALSHI_EMAIL_TO is empty")
        return cls(host, port, user, password, sender, recipients)


def send_html_email(
    *,
    subject: str,
    html_body: str,
    text_fallback: str = "",
    config: SMTPConfig | None = None,
) -> None:
    cfg = config or SMTPConfig.from_env()

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.sender
    msg["To"] = ", ".join(cfg.recipients)
    msg.set_content(text_fallback or "Open in an HTML-capable client to view this report.")
    msg.add_alternative(html_body, subtype="html")

    ctx = ssl.create_default_context()
    with smtplib.SMTP(cfg.host, cfg.port, timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls(context=ctx)
        smtp.ehlo()
        smtp.login(cfg.user, cfg.password)
        smtp.send_message(msg)
