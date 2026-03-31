"""
Email delivery service.

This module is the ONLY place that talks to an SMTP server.
It is a pure side-effect layer — it never reads from or writes to the
database, never raises, and can be disabled entirely via config.

Usage
-----
    from app.services.email_service import send_email

    send_email(
        to_email="alice@example.com",
        subject="New task assigned",
        body="You have a new task: 'Review Q2 report'.",
    )

Configuration (environment variables)
--------------------------------------
    EMAIL_ENABLED   = true          # master switch; default false
    SMTP_HOST       = smtp.gmail.com
    SMTP_PORT       = 587           # TLS port
    EMAIL_USER      = you@gmail.com
    EMAIL_PASSWORD  = <app-password>
    EMAIL_FROM_NAME = Employee CRM  # display name in From header

For Gmail you MUST use an App Password (not your account password).
See: https://support.google.com/accounts/answer/185833
"""
from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.core.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send_email(to_email: str, subject: str, body: str) -> bool:
    """
    Send a plain-text email to *to_email*.

    Returns True if the message was accepted by the SMTP server, False in
    every other case (disabled, mis-configured, network error, etc.).
    This function NEVER raises — the caller is always safe.
    """
    # ── Guard: feature switch ───────────────────────────────────────────────
    if not settings.EMAIL_ENABLED:
        logger.debug("Email disabled (EMAIL_ENABLED=false). Skipping send to %s.", to_email)
        return False

    # ── Guard: basic sanity checks ──────────────────────────────────────────
    if not to_email or "@" not in to_email:
        logger.warning("send_email called with invalid address: %r", to_email)
        return False

    if not settings.EMAIL_USER or not settings.EMAIL_PASSWORD:
        logger.warning(
            "EMAIL_USER or EMAIL_PASSWORD not configured. "
            "Set them as environment variables to enable email delivery."
        )
        return False

    # ── Build the message ───────────────────────────────────────────────────
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{settings.EMAIL_FROM_NAME} <{settings.EMAIL_USER}>"
        msg["To"] = to_email

        # Plain-text part
        msg.attach(MIMEText(body, "plain", "utf-8"))

        # Basic HTML wrapper — keeps it readable in modern clients
        html_body = _wrap_html(subject, body)
        msg.attach(MIMEText(html_body, "html", "utf-8"))

    except Exception as exc:  # pragma: no cover
        logger.error("Failed to build email message for %s: %s", to_email, exc)
        return False

    # ── Send via STARTTLS ───────────────────────────────────────────────────
    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(settings.EMAIL_USER, settings.EMAIL_PASSWORD)
            server.sendmail(settings.EMAIL_USER, to_email, msg.as_string())

        logger.info("Email sent to %s — subject: %r", to_email, subject)
        return True

    except smtplib.SMTPAuthenticationError:
        logger.error(
            "SMTP authentication failed for %s. "
            "Check EMAIL_USER / EMAIL_PASSWORD (use an App Password for Gmail).",
            settings.EMAIL_USER,
        )
    except smtplib.SMTPRecipientsRefused:
        logger.error("SMTP server refused recipient address: %s", to_email)
    except smtplib.SMTPException as exc:
        logger.error("SMTP error while sending to %s: %s", to_email, exc)
    except OSError as exc:
        # Covers ConnectionRefusedError, timeout, DNS failure, etc.
        logger.error(
            "Network error reaching %s:%s — %s",
            settings.SMTP_HOST,
            settings.SMTP_PORT,
            exc,
        )
    except Exception as exc:  # last-resort catch-all
        logger.error("Unexpected error in send_email to %s: %s", to_email, exc)

    return False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _wrap_html(title: str, body: str) -> str:
    """Return a minimal but clean HTML email body."""
    # Convert newlines in plain body to <br> for HTML rendering
    html_body = body.replace("\n", "<br>")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <style>
    body {{ font-family: Arial, sans-serif; background: #f4f4f4; margin: 0; padding: 0; }}
    .container {{ max-width: 560px; margin: 32px auto; background: #ffffff;
                  border-radius: 8px; overflow: hidden;
                  box-shadow: 0 2px 8px rgba(0,0,0,.08); }}
    .header {{ background: #2563eb; padding: 24px 32px; }}
    .header h1 {{ color: #ffffff; font-size: 18px; margin: 0; }}
    .body {{ padding: 28px 32px; color: #374151; font-size: 15px; line-height: 1.6; }}
    .footer {{ padding: 16px 32px; background: #f9fafb;
               color: #9ca3af; font-size: 12px; text-align: center; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header"><h1>{settings.EMAIL_FROM_NAME}</h1></div>
    <div class="body">
      <p><strong>{title}</strong></p>
      <p>{html_body}</p>
    </div>
    <div class="footer">
      This is an automated notification — please do not reply to this email.
    </div>
  </div>
</body>
</html>"""
