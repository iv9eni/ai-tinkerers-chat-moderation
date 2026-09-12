"""Load tokens from Google Secret Manager into the environment at startup.

Set SECRETS_PROJECT to the GCP project id. Each name in SECRET_NAMES that is not already
set in the environment is read from Secret Manager (latest version). Without
SECRETS_PROJECT nothing happens and .env or plain environment variables are used, which is
what local development wants.

Values are never logged. Errors name the secret, not its value.
"""

from __future__ import annotations

import os

SECRET_NAMES = (
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
    "SLACK_USER_TOKEN",
    "OPENROUTER_API_KEY",
    "AUDIT_HMAC_KEY",
)


class SecretsError(RuntimeError):
    pass


def load(
    project: str | None = None, client=None, names: tuple[str, ...] = SECRET_NAMES
) -> list[str]:
    """Fill missing environment variables from Secret Manager. Returns the names loaded."""
    project = project or os.environ.get("SECRETS_PROJECT", "")
    if not project:
        return []
    if client is None:
        try:
            from google.cloud import secretmanager
        except ImportError as e:
            raise SecretsError(
                "SECRETS_PROJECT is set but google-cloud-secret-manager is not installed:"
                " uv sync --extra gcp"
            ) from e
        client = secretmanager.SecretManagerServiceClient()

    loaded, problems = [], []
    for name in names:
        if os.environ.get(name):
            continue  # an explicit environment variable wins, useful for local overrides
        path = f"projects/{project}/secrets/{name}/versions/latest"
        try:
            response = client.access_secret_version(request={"name": path})
        except Exception as e:  # noqa: BLE001 - NotFound, PermissionDenied, network
            problems.append(f"{name}: {type(e).__name__}")
            continue
        os.environ[name] = response.payload.data.decode("utf-8")
        loaded.append(name)
    if problems:
        raise SecretsError(
            f"could not read from Secret Manager in project {project}: " + "; ".join(problems)
        )
    return loaded
