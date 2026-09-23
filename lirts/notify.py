"""Desktop notifications, best effort and never fatal."""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess

from lirts.constants import NOTIFY_TIMEOUT

log = logging.getLogger(__name__)


def _escape_applescript(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def notify_desktop(title: str, message: str) -> bool:
    """Show a desktop notification; returns True if a backend accepted it."""
    system = platform.system()
    try:
        if system == "Darwin":
            tn = shutil.which("terminal-notifier")
            if tn:
                subprocess.run(
                    [tn, "-title", title, "-message", message],
                    check=False,
                    timeout=NOTIFY_TIMEOUT,
                    capture_output=True,
                )
                return True
            osa = shutil.which("osascript")
            if osa:
                script = f'display notification "{_escape_applescript(message)}" with title "{_escape_applescript(title)}"'
                subprocess.run(
                    [osa, "-e", script], check=False, timeout=NOTIFY_TIMEOUT, capture_output=True
                )
                return True
        else:
            ns = shutil.which("notify-send")
            if ns:
                subprocess.run(
                    [ns, title, message], check=False, timeout=NOTIFY_TIMEOUT, capture_output=True
                )
                return True
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("notification failed: %s", exc)
    return False
