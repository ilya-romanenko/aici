from __future__ import annotations

import logging
import os
import smtplib
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from typing import Any

logger = logging.getLogger("ai_crypto_index.email")

_FLAG_TRUE = {"1", "true", "yes", "on"}
_DEFAULT_RECIPIENT = os.getenv("AICI_DEFAULT_EMAIL_RECIPIENT", "aicryptoindex@gmail.com")
_APP_BASE_URL = os.getenv("AICI_AUTH_APP_URL") or "https://aici.pro"
_APP_OVERVIEW_URL = f"{_APP_BASE_URL.rstrip('/')}/app/overview"


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    username: str | None
    password: str | None
    sender: str
    recipients: tuple[str, ...]
    use_tls: bool
    use_ssl: bool


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in _FLAG_TRUE


def _truncate_payload(text: str | None, *, limit: int = 1200) -> str | None:
    if not text:
        return None
    if len(text) <= limit:
        return text
    return f"{text[:limit]}... (truncated)"


_EMAIL_ENABLED = _env_flag("AICI_EMAIL_ENABLED", True)
_SMTP_MAX_ATTEMPTS = max(1, int(os.getenv("AICI_SMTP_MAX_ATTEMPTS", "2")))
_SMTP_RETRY_DELAY_SECONDS = max(0.5, float(os.getenv("AICI_SMTP_RETRY_DELAY_SECONDS", "1.0")))


def _parse_recipients(raw_value: str | None) -> tuple[str, ...]:
    if not raw_value:
        return (_DEFAULT_RECIPIENT,)
    recipients: list[str] = []
    for item in raw_value.split(","):
        trimmed = item.strip()
        if trimmed:
            recipients.append(trimmed)
    return tuple(recipients or (_DEFAULT_RECIPIENT,))


def _load_smtp_config() -> SmtpConfig:
    host = os.getenv("AICI_SMTP_HOST")
    if not host:
        raise RuntimeError("AICI_SMTP_HOST is required to send email notifications.")

    try:
        port = int(os.getenv("AICI_SMTP_PORT", "587"))
    except ValueError as exc:  # pragma: no cover - defensive parsing
        raise RuntimeError("AICI_SMTP_PORT must be an integer.") from exc

    username = os.getenv("AICI_SMTP_USERNAME")
    password = os.getenv("AICI_SMTP_PASSWORD")
    sender = os.getenv("AICI_EMAIL_SENDER") or username or f"no-reply@{host}"
    recipients = _parse_recipients(os.getenv("AICI_EMAIL_RECIPIENTS"))
    use_ssl = _env_flag("AICI_SMTP_USE_SSL", False)
    use_tls = _env_flag("AICI_SMTP_USE_TLS", not use_ssl)

    return SmtpConfig(
        host=host,
        port=port,
        username=username,
        password=password,
        sender=sender,
        recipients=recipients,
        use_tls=use_tls,
        use_ssl=use_ssl,
    )


def _send_with_client(client: smtplib.SMTP, config: SmtpConfig, message: EmailMessage) -> None:
    client.ehlo()
    if config.use_tls and not isinstance(client, smtplib.SMTP_SSL):
        client.starttls()
        client.ehlo()
    if config.username:
        client.login(config.username, config.password or "")

    mail_options: list[str] = []
    if client.has_extn("dsn"):
        # Request a single final failure DSN to avoid repeated delay notices.
        mail_options = ["NOTIFY=FAILURE", "RET=HDRS"]

    client.send_message(message, mail_options=mail_options)


def _deliver_message(config: SmtpConfig, message: EmailMessage) -> None:
    last_error: Exception | None = None
    for attempt in range(1, _SMTP_MAX_ATTEMPTS + 1):
        try:
            if config.use_ssl:
                with smtplib.SMTP_SSL(config.host, config.port, timeout=20) as client:
                    _send_with_client(client, config, message)
            else:
                with smtplib.SMTP(config.host, config.port, timeout=20) as client:
                    _send_with_client(client, config, message)
            return
        except (smtplib.SMTPException, OSError) as exc:
            last_error = exc
            logger.warning(
                "email_delivery_retry",
                extra={
                    "attempt": attempt,
                    "max_attempts": _SMTP_MAX_ATTEMPTS,
                    "error": repr(exc),
                },
            )
            if attempt >= _SMTP_MAX_ATTEMPTS:
                raise
            time.sleep(_SMTP_RETRY_DELAY_SECONDS)
    if last_error:
        raise last_error


def _format_lines(fields: Iterable[tuple[str, Any]]) -> list[str]:
    lines: list[str] = []
    for label, value in fields:
        display_value: str
        if value is None:
            display_value = "—"
        elif isinstance(value, bool):
            display_value = "Yes" if value else "No"
        elif isinstance(value, (list, tuple, set)):
            display_value = ", ".join(str(item) for item in value) or "—"
        else:
            display_value = str(value).strip() or "—"
        lines.append(f"- {label}: {display_value}")
    return lines


def _send_intake_email(
    *,
    subject: str,
    body_lines: list[str],
    reply_to: str | None,
    attachments: Sequence[Mapping[str, Any]] | None = None,
) -> None:
    if not _EMAIL_ENABLED:
        return
    config = _load_smtp_config()

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.sender
    message["To"] = ", ".join(config.recipients)
    if reply_to:
        message["Reply-To"] = reply_to

    message.set_content("\n".join(body_lines))

    for attachment in attachments or ():
        data = attachment.get("data") if isinstance(attachment, Mapping) else None
        if not isinstance(data, (bytes, bytearray)):
            continue
        filename = ""
        if isinstance(attachment, Mapping):
            filename = str(attachment.get("filename") or "").strip()
        sanitized_filename = filename or "attachment"
        content_type = ""
        if isinstance(attachment, Mapping):
            content_type = str(attachment.get("content_type") or "").strip().lower()
        maintype, _, subtype = content_type.partition("/")
        maintype = maintype or "application"
        subtype = subtype or "octet-stream"
        message.add_attachment(
            bytes(data),
            maintype=maintype,
            subtype=subtype,
            filename=sanitized_filename,
        )

    logger.info(
        "dispatching_intake_email",
        extra={"subject": subject, "recipients": config.recipients},
    )
    _deliver_message(config, message)
    logger.info("intake_email_sent", extra={"subject": subject, "recipients": config.recipients})


def _send_transactional_email(
    *,
    recipient: str,
    subject: str,
    body_lines: Sequence[str],
) -> None:
    if not _EMAIL_ENABLED:
        return
    config = _load_smtp_config()

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.sender
    message["To"] = recipient
    message.set_content("\n".join(body_lines))

    logger.info(
        "dispatching_transactional_email",
        extra={"recipient": recipient, "subject": subject},
    )
    _deliver_message(config, message)
    logger.info(
        "transactional_email_sent",
        extra={"recipient": recipient, "subject": subject},
    )


def send_daily_snapshot_alert(*, subject: str, body_lines: Sequence[str]) -> None:
    try:
        _send_intake_email(
            subject=subject,
            body_lines=list(body_lines),
            reply_to=None,
        )
    except Exception:  # pragma: no cover - alert best-effort
        logger.exception(
            "failed_to_send_daily_snapshot_alert",
            extra={"subject": subject},
        )


def send_demo_request_email(record: Mapping[str, Any], request_id: str, received_at: str) -> None:
    try:
        subject_name = record.get("name") or "demo lead"
        subject_email = record.get("email") or ""
        subject = f"[AICI] Demo request from {subject_name}"
        lines = [
            "Form: Demo request",
            f"Request ID: {request_id}",
            f"Received at: {received_at}",
            "",
            "Submitted data:",
        ]
        fields = _format_lines(
            [
                ("Full name", record.get("name")),
                ("Work email", subject_email),
                ("Company or fund", record.get("company")),
                ("Role", record.get("role")),
                ("Team size", record.get("team_size")),
                ("Related run ID", record.get("run_id")),
                ("Primary use case", record.get("use_case")),
                ("Additional notes", record.get("notes")),
                ("Newsletter opt-in", record.get("newsletter_opt_in")),
                ("Terms accepted", record.get("terms_accepted")),
            ]
        )
        body_lines = lines + fields
        reply_to = subject_email if subject_email else None
        _send_intake_email(subject=subject, body_lines=body_lines, reply_to=reply_to)
    except Exception:  # pragma: no cover - background task guard
        logger.exception(
            "failed_to_send_demo_request_email",
            extra={"request_id": request_id},
        )


def send_registration_request_email(
    record: Mapping[str, Any],
    request_id: str,
    received_at: str,
) -> None:
    try:
        registrant_name = record.get("full_name") or "platform lead"
        registrant_email = record.get("email") or ""
        subject = f"[AICI] Walkthrough registration from {registrant_name}"
        lines = [
            "Form: Walkthrough registration",
            f"Request ID: {request_id}",
            f"Received at: {received_at}",
            "",
            "Submitted data:",
        ]
        fields = _format_lines(
            [
                ("Full name", registrant_name),
                ("Work email", registrant_email),
                ("Company", record.get("company")),
                ("Role", record.get("role")),
                ("Team size", record.get("team_size")),
                ("Objectives", record.get("objectives")),
                ("Newsletter opt-in", record.get("newsletter_opt_in")),
                ("Terms accepted", record.get("terms_accepted")),
            ]
        )
        body_lines = lines + fields
        reply_to = registrant_email if registrant_email else None
        _send_intake_email(subject=subject, body_lines=body_lines, reply_to=reply_to)
    except Exception:  # pragma: no cover - background task guard
        logger.exception(
            "failed_to_send_registration_request_email",
            extra={"request_id": request_id},
        )


def send_api_request_email(record: Mapping[str, Any], request_id: str, received_at: str) -> None:
    try:
        contact_name = record.get("contact_name") or "API lead"
        contact_email = record.get("email") or ""
        subject = f"[AICI] API access request from {contact_name}"
        lines = [
            "Form: API access request",
            f"Request ID: {request_id}",
            f"Received at: {received_at}",
            "",
            "Submitted data:",
        ]
        fields = _format_lines(
            [
                ("Contact name", contact_name),
                ("Work email", contact_email),
                ("Company", record.get("company")),
                ("Primary use case", record.get("use_case")),
                ("Expected monthly requests", record.get("expected_monthly_requests")),
                ("Integration stage", record.get("integration_stage")),
                ("Timeline", record.get("timeline")),
                ("Subscribe to updates", record.get("subscribe_updates")),
                ("Terms accepted", record.get("terms_accepted")),
            ]
        )
        body_lines = lines + fields
        reply_to = contact_email if contact_email else None
        _send_intake_email(subject=subject, body_lines=body_lines, reply_to=reply_to)
    except Exception:  # pragma: no cover - background task guard
        logger.exception(
            "failed_to_send_api_request_email",
            extra={"request_id": request_id},
        )


def send_support_ticket_email(
    record: Mapping[str, Any],
    request_id: str,
    received_at: str,
    attachments: Sequence[Mapping[str, Any]] | None = None,
) -> None:
    try:
        contact_name = record.get("contact_name") or record.get("account_name") or "support user"
        contact_email = record.get("contact_email") or record.get("account_email") or ""
        subject = record.get("subject") or "Support request"
        ticket_subject = f"[AICI] Support ticket: {subject}"
        lines = [
            "Form: Support ticket",
            f"Request ID: {request_id}",
            f"Received at: {received_at}",
            "",
            "Submitted data:",
        ]
        fields = _format_lines(
            [
                ("Contact name", contact_name),
                ("Contact email", contact_email),
                ("Subject", subject),
                ("Description", record.get("description")),
                ("Account ID", record.get("account_id")),
                ("Account email", record.get("account_email")),
            ]
        )
        attachment_labels: list[str] = []
        for attachment in record.get("attachments") or []:
            if not isinstance(attachment, Mapping):
                continue
            name = str(attachment.get("filename") or "").strip() or "attachment"
            size_bytes = attachment.get("size_bytes")
            size_hint = ""
            if isinstance(size_bytes, (int, float)) and size_bytes >= 0:
                size_mb = round(float(size_bytes) / (1024 * 1024), 2)
                size_hint = f" ({size_mb} MB)"
            content_type = str(attachment.get("content_type") or "").strip()
            label = f"{name}{size_hint}"
            if content_type:
                label = f"{label} [{content_type}]"
            attachment_labels.append(label)
        if attachment_labels:
            fields.extend(_format_lines([("Attachments", attachment_labels)]))
        body_lines = lines + fields
        reply_to = contact_email if contact_email else None
        _send_intake_email(
            subject=ticket_subject,
            body_lines=body_lines,
            reply_to=reply_to,
            attachments=attachments,
        )
    except Exception:  # pragma: no cover - background task guard
        logger.exception(
            "failed_to_send_support_ticket_email",
            extra={"request_id": request_id},
        )


def send_api_usage_alert(
    *,
    account: Any,
    api_key: Any,
    threshold: float,
    usage_ratio: float,
    recipient: str | None = None,
    recipient_name: str | None = None,
    monthly_usage_tokens: int | None = None,
    monthly_quota_tokens: int | None = None,
) -> None:
    try:
        target_email = recipient or getattr(account, "email", None)
        if not target_email:
            return
        account_name = recipient_name or getattr(account, "full_name", "") or "there"
        percent_display = round(min(max(usage_ratio, 0.0), 1.0) * 100, 1)
        threshold_display = round(threshold * 100)
        key_label = getattr(api_key, "label", "API key")
        key_hint = f"{getattr(api_key, 'token_prefix', '****')}...{getattr(api_key, 'token_suffix', '****')}"
        plan_code = getattr(api_key, "plan_code", "current")
        usage_display = (
            f"{monthly_usage_tokens:,} tokens"
            if monthly_usage_tokens is not None
            else f"{percent_display}% of the quota"
        )
        quota_display = f"{monthly_quota_tokens:,} tokens" if monthly_quota_tokens is not None else "token quota"
        remaining_tokens = (
            max((monthly_quota_tokens or 0) - (monthly_usage_tokens or 0), 0)
            if monthly_usage_tokens is not None and monthly_quota_tokens is not None
            else None
        )
        lines = [
            f"Hi {account_name},",
            "",
            f"Your {key_label} ({key_hint}) has consumed {usage_display} on the {plan_code.upper()} plan ({quota_display} total).",
            f"This crossed the {threshold_display}% alert threshold.",
        ]
        if remaining_tokens is not None:
            lines.append(f"Remaining tokens before soft throttle: {remaining_tokens:,}.")
        lines.extend(
            [
                "",
                "Token pricing: pipeline triggers charge base + parameter add-ons; run reads cost 5 tokens each.",
                "Track tokens via response headers (X-API-Request-Tokens, X-API-Usage-Daily/Monthly).",
                "",
                "Next steps:",
                "- Rotate or disable unused keys from the console (Keys & Security) to prevent overages.",
                "- Upgrade the workspace plan if you expect more traffic.",
                "- Reach out to contact@aici.pro for custom limits.",
                "",
                "The counter resets on the 1st of each month.",
            ]
        )
        _send_transactional_email(
            recipient=target_email,
            subject=f"[AICI] API token usage reached {threshold_display}% of the quota",
            body_lines=lines,
        )
    except Exception:  # pragma: no cover - best-effort
        logger.exception(
            "failed_to_send_api_usage_alert",
            extra={
                "account": getattr(account, "id", None),
                "api_key": getattr(api_key, "id", None),
            },
        )


def send_api_key_rotated_email(
    *,
    account: Any,
    api_key: Any,
    rotated_by: str | None,
    actor_ip: str | None = None,
) -> None:
    try:
        recipient = getattr(account, "email", None)
        if not recipient:
            return
        account_name = getattr(account, "full_name", "") or "team"
        key_label = getattr(api_key, "label", "API key")
        key_hint = f"{getattr(api_key, 'token_prefix', '****')}…{getattr(api_key, 'token_suffix', '****')}"
        rotated_by_display = rotated_by or "system"
        lines = [
            f"Hi {account_name},",
            "",
            f"The key \"{key_label}\" ({key_hint}) was rotated.",
            f"Initiator: {rotated_by_display}",
        ]
        if actor_ip:
            lines.append(f"Origin IP: {actor_ip}")
        lines.extend(
            [
                "",
                "Next steps:",
                "- Update your integrations with the new secret immediately.",
                "- Revoke the key if this rotation was unexpected.",
                "",
                "You are receiving this email because security alerts are enabled for your workspace.",
            ]
        )
        _send_transactional_email(
            recipient=recipient,
            subject=f"[AICI] API key rotated ({key_label})",
            body_lines=lines,
        )
    except Exception:  # pragma: no cover - notification best-effort
        logger.exception(
            "failed_to_send_api_key_rotation_email",
            extra={
                "account": getattr(account, "id", None),
                "api_key": getattr(api_key, "id", None),
            },
        )


def send_signup_confirmation_email(
    *,
    recipient: str,
    full_name: str,
    confirmation_link: str,
    expires_at: datetime,
) -> None:
    try:
        lines = [
            f"Hi {full_name or 'there'},",
            "",
            "Thanks for registering with AI Crypto Index.",
            "Please confirm your email address to activate your workspace and API access:",
            confirmation_link,
            "",
            "After confirmation, pin your Overview tab for onboarding and usage:",
            _APP_OVERVIEW_URL,
            "",
            f"The link will expire on {expires_at.strftime('%Y-%m-%d %H:%M %Z')}.",
            "",
            "If you did not request access, simply ignore this email.",
        ]
        _send_transactional_email(
            recipient=recipient,
            subject="[AICI] Confirm your account",
            body_lines=lines,
        )
    except Exception:  # pragma: no cover - best-effort delivery
        logger.exception(
            "failed_to_send_signup_confirmation",
            extra={"recipient": recipient},
        )


def send_password_reset_email(
    *,
    recipient: str,
    full_name: str,
    reset_link: str,
    expires_at: datetime,
) -> None:
    try:
        lines = [
            f"Hi {full_name or 'there'},",
            "",
            "We received a request to reset your AI Crypto Index password.",
            "You can set a new password using the link below:",
            reset_link,
            "",
            f"The link is valid until {expires_at.strftime('%Y-%m-%d %H:%M %Z')}.",
            "",
            "If you didn't initiate this request, you can safely ignore the email.",
        ]
        _send_transactional_email(
            recipient=recipient,
            subject="[AICI] Reset your password",
            body_lines=lines,
        )
    except Exception:  # pragma: no cover - best-effort delivery
        logger.exception(
            "failed_to_send_password_reset_email",
            extra={"recipient": recipient},
        )


def _format_expiry_label(expires_at: datetime) -> str:
    if expires_at.tzinfo:
        return expires_at.strftime("%Y-%m-%d %H:%M %Z")
    return expires_at.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M UTC")


def send_crypto_activation_email(
    *,
    recipient: str,
    full_name: str,
    plan_name: str,
    expires_at: datetime,
    invoice_url: str | None = None,
) -> None:
    try:
        expiry_label = _format_expiry_label(expires_at)
        lines = [
            f"Hi {full_name or 'there'},",
            "",
            f"Your {plan_name} plan is active.",
            f"Access is confirmed until {expiry_label}.",
            "",
            "You can renew at any time using the latest crypto invoice:",
        ]
        if invoice_url:
            lines.append(invoice_url)
        else:
            lines.append(_APP_OVERVIEW_URL)
        lines.extend(
            [
                "",
                "If you did not authorize this payment, contact support@aici.pro.",
            ]
        )
        _send_transactional_email(
            recipient=recipient,
            subject=f"[AICI] {plan_name} activated",
            body_lines=lines,
        )
    except Exception:  # pragma: no cover - best-effort delivery
        logger.exception(
            "failed_to_send_crypto_activation_email",
            extra={"recipient": recipient},
        )


def send_crypto_resume_email(
    *,
    recipient: str,
    full_name: str,
    plan_name: str,
    expires_at: datetime,
    invoice_url: str | None = None,
) -> None:
    try:
        expiry_label = _format_expiry_label(expires_at)
        lines = [
            f"Hi {full_name or 'there'},",
            "",
            f"Renewal for your {plan_name} plan has been resumed.",
            f"Access stays active through {expiry_label}.",
            "",
            "Keep your plan running with the upcoming crypto invoice:",
        ]
        if invoice_url:
            lines.append(invoice_url)
        else:
            lines.append(_APP_OVERVIEW_URL)
        lines.extend(
            [
                "",
                "If you did not request this, pause renewal again from the billing page or contact support@aici.pro.",
            ]
        )
        _send_transactional_email(
            recipient=recipient,
            subject=f"[AICI] {plan_name} renewal resumed",
            body_lines=lines,
        )
    except Exception:  # pragma: no cover - best-effort delivery
        logger.exception(
            "failed_to_send_crypto_resume_email",
            extra={"recipient": recipient},
        )


def send_crypto_renewal_reminder_email(
    *,
    recipient: str,
    full_name: str,
    plan_name: str,
    expires_at: datetime,
    days_left: int,
    invoice_url: str | None,
) -> None:
    try:
        expiry_label = _format_expiry_label(expires_at)
        reminder_days = max(days_left, 0)
        lines = [
            f"Hi {full_name or 'there'},",
            "",
            f"Your {plan_name} plan expires on {expiry_label}.",
            f"That's in {reminder_days} day{'s' if reminder_days != 1 else ''}.",
            "",
            "Renew with the next crypto invoice:",
        ]
        if invoice_url:
            lines.append(invoice_url)
        else:
            lines.append(_APP_OVERVIEW_URL)
        lines.extend(
            [
                "",
                "If you've already paid, no action is required.",
                "Otherwise, complete the payment before the expiration date to avoid pauses.",
            ]
        )
        _send_transactional_email(
            recipient=recipient,
            subject=f"[AICI] {plan_name} renewal in {reminder_days} day{'s' if reminder_days != 1 else ''}",
            body_lines=lines,
        )
    except Exception:  # pragma: no cover - best-effort delivery
        logger.exception(
            "failed_to_send_crypto_renewal_email",
            extra={"recipient": recipient},
        )


def send_crypto_webhook_alert(
    *,
    invoice_id: str | None,
    status: str,
    error: str,
    payload: str | None,
) -> None:
    try:
        payload_preview = _truncate_payload(payload)
        lines = [
            "Crypto webhook failed or was rejected.",
            f"Invoice ID: {invoice_id or 'unknown'}",
            f"Status: {status}",
            f"Error: {error}",
        ]
        if payload_preview:
            lines.extend(
                [
                    "",
                    "Payload preview:",
                    payload_preview,
                ]
            )
        _send_intake_email(
            subject=f"[AICI] Crypto webhook error ({invoice_id or 'unknown'})",
            body_lines=lines,
            reply_to=None,
        )
    except Exception:  # pragma: no cover - alert best-effort
        logger.exception(
            "failed_to_send_crypto_webhook_alert",
            extra={"invoice_id": invoice_id, "error": error},
        )


def send_crypto_confirmation_delay_alert(
    *,
    invoice_id: str,
    account_email: str | None,
    plan_code: str,
    status: str,
    confirmations: int,
    confirmations_required: int | None,
    age_minutes: int,
    hosted_url: str | None,
) -> None:
    try:
        lines = [
            "Crypto payment stuck without required confirmations.",
            f"Invoice ID: {invoice_id}",
            f"Account: {account_email or 'unknown'}",
            f"Plan: {plan_code}",
            f"Status: {status}",
            f"Confirmations: {confirmations}/{confirmations_required or '?'}",
            f"Age: {age_minutes} minutes",
        ]
        if hosted_url:
            lines.append(f"Hosted URL: {hosted_url}")
        _send_intake_email(
            subject=f"[AICI] Crypto payment delay ({invoice_id})",
            body_lines=lines,
            reply_to=None,
        )
    except Exception:  # pragma: no cover - alert best-effort
        logger.exception(
            "failed_to_send_crypto_confirmation_alert",
            extra={"invoice_id": invoice_id, "status": status},
        )
