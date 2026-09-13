"""Small hosting adaptations, independent of planning and user state."""
import os
import time


def apply_configured_timezone():
    """Apply an explicit server TZ after Streamlit has loaded root-level secrets.

    POSIX caches local timezone information; setting an environment variable
    after interpreter startup alone need not refresh datetime.now/date.today.
    Unconfigured local installs and Windows retain their existing OS clock.
    This is process configuration, never a per-user or per-widget preference.
    """
    if os.environ.get("TZ") and hasattr(time, "tzset"):
        time.tzset()
