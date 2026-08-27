"""Send the recap over Gmail SMTP (stdlib only).

Same contract as a ``Sink``: never raise — log and return ``False``. Credentials
are read from the environment at send time (like ``DiscordSink`` reads its
webhook URL), so importing this module needs no secrets.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

from scripts.cadbury_bot.config import CadburyConfig

logger = logging.getLogger(__name__)


class EmailMailer:
    def __init__(self, cfg: CadburyConfig):
        self.cfg = cfg

    def send(self, subject: str, body: str) -> bool:
        cfg = self.cfg
        sender = os.environ.get(cfg.sender_env_var, "").strip()
        password = os.environ.get(cfg.password_env_var, "").strip()
        recipients = [r for r in cfg.recipients if r]

        if not sender or not password:
            logger.warning("cadbury_bot: %s / %s not set — cannot send recap",
                           cfg.sender_env_var, cfg.password_env_var)
            return False
        if not recipients:
            logger.warning("cadbury_bot: no recipients configured — not sending")
            return False

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = ", ".join(recipients)
        msg.set_content(body)

        try:
            with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as smtp:
                smtp.starttls()
                smtp.login(sender, password)
                smtp.send_message(msg)
        except Exception as e:  # smtplib / socket / auth — never propagate
            logger.error("cadbury_bot: SMTP send failed: %s", e)
            return False

        logger.info("cadbury_bot: recap emailed to %s", ", ".join(recipients))
        return True
