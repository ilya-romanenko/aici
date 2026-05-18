import os


_TRUE_VALUES = {"1", "true", "yes", "on"}


# Safety guard: disable outbound emails in pytest unless explicitly opted in.
allow_real_email = os.getenv("AICI_ALLOW_REAL_EMAIL_IN_TESTS", "").strip().lower() in _TRUE_VALUES
if not allow_real_email:
    os.environ["AICI_EMAIL_ENABLED"] = "0"
