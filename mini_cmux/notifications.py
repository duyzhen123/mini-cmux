"""Best-effort native macOS notifications."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from typing import Optional


class Notifier:
    def __init__(self, enabled: Optional[bool] = None) -> None:
        disabled = os.environ.get("MINI_CMUX_DISABLE_NOTIFICATIONS", "").lower()
        self.enabled = (
            enabled
            if enabled is not None
            else disabled not in {"1", "true", "yes"} and platform.system() == "Darwin"
        )

    def send(self, title: str, body: str, action: str) -> bool:
        if not self.enabled:
            return False
        osascript = shutil.which("osascript")
        if not osascript:
            return False
        message = "{}\n\nFocus: {}".format(body, action)
        script = (
            "var app = Application.currentApplication();"
            "app.includeStandardAdditions = true;"
            "app.displayNotification("
            + json.dumps(message)
            + ", {withTitle: "
            + json.dumps(title)
            + "});"
        )
        try:
            subprocess.Popen(
                [osascript, "-l", "JavaScript", "-e", script],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            return False
        return True

