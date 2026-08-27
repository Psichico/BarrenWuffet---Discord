import scripts.cadbury_bot.mailer as mailer_mod
from scripts.cadbury_bot.config import CadburyConfig
from scripts.cadbury_bot.mailer import EmailMailer


class _FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.started_tls = False
        self.login_args = None
        self.sent = []
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, user, password):
        self.login_args = (user, password)

    def send_message(self, msg):
        self.sent.append(msg)


def _cfg(**kw):
    base = dict(recipients=["a@b.com", "c@d.com"], smtp_host="smtp.example",
                smtp_port=587, sender_env_var="GA", password_env_var="GP",
                subject_prefix="Market Recap")
    base.update(kw)
    return CadburyConfig(**base)


def setup_function(_):
    _FakeSMTP.instances.clear()


def test_send_success(monkeypatch):
    monkeypatch.setattr(mailer_mod.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setenv("GA", "me@gmail.com")
    monkeypatch.setenv("GP", "app-pw")

    ok = EmailMailer(_cfg()).send("Market Recap — Wed, Aug 26 2026", "body text")

    assert ok is True
    smtp = _FakeSMTP.instances[0]
    assert smtp.started_tls and smtp.login_args == ("me@gmail.com", "app-pw")
    msg = smtp.sent[0]
    assert msg["Subject"] == "Market Recap — Wed, Aug 26 2026"
    assert msg["To"] == "a@b.com, c@d.com"
    assert msg["From"] == "me@gmail.com"
    assert msg.get_content().strip() == "body text"


def test_missing_password_returns_false(monkeypatch):
    monkeypatch.setattr(mailer_mod.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setenv("GA", "me@gmail.com")
    monkeypatch.delenv("GP", raising=False)

    assert EmailMailer(_cfg()).send("s", "b") is False
    assert _FakeSMTP.instances == []  # never even connected


def test_no_recipients_returns_false(monkeypatch):
    monkeypatch.setattr(mailer_mod.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setenv("GA", "me@gmail.com")
    monkeypatch.setenv("GP", "app-pw")

    assert EmailMailer(_cfg(recipients=[])).send("s", "b") is False


def test_smtp_exception_returns_false(monkeypatch):
    def boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(mailer_mod.smtplib, "SMTP", boom)
    monkeypatch.setenv("GA", "me@gmail.com")
    monkeypatch.setenv("GP", "app-pw")

    assert EmailMailer(_cfg()).send("s", "b") is False
