"""Read-only IMAP scanner for personal email signal.

Privacy / safety notes — read these before enabling:

  * READ-ONLY. The agent never sends, deletes, marks-read, forwards,
    or otherwise modifies any message. It opens INBOX in read-only
    mode (`imap.select(readonly=True)`).
  * Credentials live in env vars (KALSHI_IMAP_*). They are never
    written to disk or to the diagnostic JSON.
  * Only matched evidence — sender, subject, first 120 chars of body
    — is ever surfaced in the report or stored in the diagnostic dump.
    Full message bodies are NEVER persisted.
  * Default lookback is 3 days. Default cap is 200 messages per scan.
  * For Gmail you must enable IMAP access in settings and create an
    app password (the same one you use for the SMTP daily-report
    sender works fine).
"""
from __future__ import annotations

import calendar
import email
import imaplib
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from typing import Iterable

DEFAULT_LOOKBACK_DAYS = 3
DEFAULT_MAX_MESSAGES = 200
EMAIL_BODY_PREVIEW_CHARS = 120
# Each matching email shaves a small amount off prior confidence.
EMAIL_CONFIDENCE_PER_HIT = 0.03
MAX_EMAIL_CONFIDENCE_DRAG = 0.15


class IMAPConfigError(RuntimeError):
    """Raised when KALSHI_IMAP_* env vars are missing or malformed."""


@dataclass(frozen=True)
class EmailItem:
    sender: str
    subject: str
    body_preview: str  # truncated, never the full body
    received: datetime

    def matches(self, patterns: list[re.Pattern]) -> bool:
        haystack = f"{self.subject} {self.body_preview}"
        return any(p.search(haystack) for p in patterns)


@dataclass
class EmailContext:
    items: list[EmailItem] = field(default_factory=list)
    fetch_error: str = ""

    def matching(self, keywords: list[str]) -> list[EmailItem]:
        if not self.items or not keywords:
            return []
        patterns = [re.compile(k, re.IGNORECASE) for k in keywords]
        hits = [m for m in self.items if m.matches(patterns)]
        hits.sort(key=lambda m: m.received, reverse=True)
        return hits[:MAX_EMAILS_PER_OPPORTUNITY]


MAX_EMAILS_PER_OPPORTUNITY = 3


@dataclass
class IMAPConfig:
    host: str
    port: int
    user: str
    password: str
    folder: str
    lookback_days: int
    max_messages: int

    @classmethod
    def from_env(cls) -> "IMAPConfig":
        def req(key: str) -> str:
            v = os.environ.get(key)
            if not v:
                raise IMAPConfigError(f"missing env var: {key}")
            return v

        return cls(
            host=req("KALSHI_IMAP_HOST"),
            port=int(os.environ.get("KALSHI_IMAP_PORT", "993")),
            user=req("KALSHI_IMAP_USER"),
            password=req("KALSHI_IMAP_PASS"),
            folder=os.environ.get("KALSHI_IMAP_FOLDER", "INBOX"),
            lookback_days=int(os.environ.get("KALSHI_IMAP_LOOKBACK_DAYS", str(DEFAULT_LOOKBACK_DAYS))),
            max_messages=int(os.environ.get("KALSHI_IMAP_MAX_MESSAGES", str(DEFAULT_MAX_MESSAGES))),
        )


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _extract_text_body(msg: email.message.Message) -> str:
    """Pull the first text/plain part of a multipart message."""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    try:
                        return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                    except (LookupError, AttributeError):
                        return payload.decode("utf-8", errors="replace")
        return ""
    payload = msg.get_payload(decode=True)
    if isinstance(payload, bytes):
        try:
            return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
        except (LookupError, AttributeError):
            return payload.decode("utf-8", errors="replace")
    return str(payload or "")


def _preview(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if len(cleaned) <= EMAIL_BODY_PREVIEW_CHARS:
        return cleaned
    return cleaned[: EMAIL_BODY_PREVIEW_CHARS - 1].rstrip() + "…"


def _parse_email_message(raw_bytes: bytes) -> EmailItem | None:
    try:
        msg = email.message_from_bytes(raw_bytes)
    except Exception:
        return None
    sender = _decode_header(msg.get("From"))
    subject = _decode_header(msg.get("Subject"))
    date_str = msg.get("Date")
    received = datetime.now(tz=timezone.utc)
    if date_str:
        try:
            parsed = parsedate_to_datetime(date_str)
            if parsed is not None:
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                received = parsed.astimezone(timezone.utc)
        except (TypeError, ValueError, AttributeError):
            pass
    body = _extract_text_body(msg)
    return EmailItem(
        sender=sender,
        subject=subject,
        body_preview=_preview(body),
        received=received,
    )


def _imap_since_date(d: datetime) -> str:
    """IMAP `SEARCH SINCE` requires DD-Mon-YYYY in English. strftime
    is locale-sensitive, so we build the string from calendar.month_abbr."""
    return f"{d.day:02d}-{calendar.month_abbr[d.month]}-{d.year}"


def fetch_emails(config: IMAPConfig | None = None) -> EmailContext:
    """Fetch recent emails from the configured IMAP folder.

    Returns an EmailContext. On any error (auth, network, parse) we
    return a context with `fetch_error` populated and an empty item
    list — the caller should treat that as "no signal" rather than a
    fatal failure.
    """
    try:
        cfg = config or IMAPConfig.from_env()
    except IMAPConfigError as e:
        return EmailContext(fetch_error=f"config: {e}")

    ctx = EmailContext()
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=cfg.lookback_days)
    since_str = _imap_since_date(cutoff)

    try:
        with imaplib.IMAP4_SSL(cfg.host, cfg.port) as imap:
            imap.login(cfg.user, cfg.password)
            imap.select(cfg.folder, readonly=True)
            typ, data = imap.search(None, f'(SINCE "{since_str}")')
            if typ != "OK" or not data or not data[0]:
                return ctx
            ids = data[0].split()
            ids = ids[-cfg.max_messages :]
            if not ids:
                return ctx
            # Single batched FETCH instead of N round-trips. imaplib
            # accepts a comma-separated id set.
            id_set = b",".join(ids)
            typ, msg_data = imap.fetch(id_set, "(RFC822)")
            if typ != "OK" or not msg_data:
                return ctx
            for entry in msg_data:
                if not isinstance(entry, tuple) or len(entry) < 2:
                    continue
                raw = entry[1]
                if not raw:
                    continue
                item = _parse_email_message(raw)
                if item is not None:
                    ctx.items.append(item)
    except imaplib.IMAP4.error as e:
        ctx.fetch_error = f"imap: {e}"
    except Exception as e:
        ctx.fetch_error = f"{type(e).__name__}: {e}"

    return ctx


def email_confidence_adjustment(num_matches: int) -> float:
    """Per-trade drag from email mentions. Capped at MAX_EMAIL_CONFIDENCE_DRAG.

    The semantics are deliberately the same as news pressure: any signal
    that the user is paying attention to a thesis (newsletters, friends'
    tips, alerts) means the *price* is more likely to move on flow that
    the static science prior can't see. We demote the trade in the
    ranker without touching the underlying probability.
    """
    if num_matches <= 0:
        return 0.0
    return min(MAX_EMAIL_CONFIDENCE_DRAG, num_matches * EMAIL_CONFIDENCE_PER_HIT)
