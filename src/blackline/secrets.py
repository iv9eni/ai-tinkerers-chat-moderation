"""Load tokens from Google Secret Manager into the environment at startup.

Set SECRETS_PROJECT to the GCP project id. Each name in SECRET_NAMES that is not already
set in the environment is read from Secret Manager (latest version). Without
SECRETS_PROJECT nothing happens and .env or plain environment variables are used, which is
what local development wants.

Precedence, highest first: a variable exported in the shell, then Secret Manager, then the
.env file. The app loads secrets before .env so the file cannot shadow Secret Manager.

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


def _connect():
    try:
        from google.cloud import secretmanager
    except ImportError as e:
        raise SecretsError(
            "SECRETS_PROJECT is set but google-cloud-secret-manager is not installed:"
            " uv sync --extra gcp"
        ) from e
    try:
        return secretmanager.SecretManagerServiceClient()
    except Exception as e:  # noqa: BLE001 - usually google.auth DefaultCredentialsError
        raise SecretsError(
            f"cannot connect to Secret Manager ({type(e).__name__}). On a laptop, run once:"
            " gcloud auth application-default login. On GCP, give the service account the"
            " Secret Manager Secret Accessor role."
        ) from None


def load(
    project: str | None = None,
    client=None,
    names: tuple[str, ...] | None = None,
    connect=None,
) -> list[str]:
    """Fill missing environment variables from Secret Manager. Returns the names loaded.
    Connects only when at least one name is missing."""
    project = project or os.environ.get("SECRETS_PROJECT", "")
    if not project:
        return []
    missing = [n for n in (names or SECRET_NAMES) if not os.environ.get(n)]
    if not missing:
        return []
    client = client or (connect or _connect)()

    loaded, problems = [], []
    for name in missing:
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
