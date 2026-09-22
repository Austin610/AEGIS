"""Read-only GitHub setup diagnostics; never return configuration values."""

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from aegis.access import NewKey
from aegis.github_auth import GitHubLogin


def check_github_setup() -> dict[str, object]:
    required = ("CLIENT_ID", "CLIENT_SECRET", "CALLBACK", "ROLES_FILE")
    missing = [
        "AEGIS_GITHUB_" + key
        for key in required
        if not os.environ.get("AEGIS_GITHUB_" + key, "").strip()
    ]
    issues: list[str] = []
    members = 0
    if missing:
        issues.append("Configure all four GitHub environment settings before starting AEGIS.")
    else:
        try:
            provider = GitHubLogin()
            callback = urlsplit(provider.callback)
            if callback.port is None or not 1024 <= callback.port <= 65535:
                raise ValueError("Invalid callback port")
        except ValueError:
            issues.append(
                "Callback must match the loopback AEGIS callback, including its server port."
            )
        try:
            path = Path(os.environ["AEGIS_GITHUB_ROLES_FILE"])
            if not path.is_absolute():
                raise ValueError("Roles path must be absolute")
            with path.open("rb") as handle:
                raw = handle.read(1_048_577)
            if len(raw) > 1_048_576:
                raise ValueError("Roles file exceeds limit")
            mapping = json.loads(raw)
            if not isinstance(mapping, dict) or not mapping:
                raise ValueError("At least one member required")
            for identity, entry in mapping.items():
                if not identity.isascii() or not identity.isdecimal() or int(identity) <= 0:
                    raise ValueError("Numeric GitHub account ID required")
                if str(int(identity)) != identity or not isinstance(entry, dict):
                    raise ValueError("Invalid member")
                NewKey.model_validate({"label": "GitHub member", **entry})
            members = len(mapping)
        except (OSError, ValueError, TypeError, RecursionError):
            issues.append(
                "Roles file must be an absolute readable JSON path, at most 1 MiB, "
                "with numeric account IDs and valid roles/workspace scopes."
            )
    return {
        "status": "ready_for_live_login" if not issues else "configuration_required",
        "missing_settings": missing,
        "valid_members": members,
        "issues": issues,
        "live_login_verified": False,
        "notice": (
            "Checks local configuration only; does not contact GitHub "
            "or verify workspace existence."
        ),
    }
