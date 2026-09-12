"""Container HEALTHCHECK: exit 0 when /healthz answers 200."""

import os
import sys
import urllib.request

try:
    with urllib.request.urlopen(
        f"http://127.0.0.1:{os.environ.get('PORT', '8080')}/healthz", timeout=2
    ):
        sys.exit(0)
except Exception:  # noqa: BLE001
    sys.exit(1)
