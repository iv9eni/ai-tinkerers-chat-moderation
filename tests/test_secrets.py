import pytest

from blackline import secrets


class Payload:
    def __init__(self, data):
        self.data = data


class Resp:
    def __init__(self, value):
        self.payload = Payload(value.encode())


class FakeSecretManager:
    def __init__(self, store, deny=()):
        self.store, self.deny, self.asked = store, set(deny), []

    def access_secret_version(self, request):
        name = request["name"].split("/")[3]
        self.asked.append(request["name"])
        if name in self.deny:
            raise PermissionError("denied")
        if name not in self.store:
            raise LookupError("not found")
        return Resp(self.store[name])


NAMES = ("SLACK_BOT_TOKEN", "AUDIT_HMAC_KEY")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for n in NAMES:
        monkeypatch.delenv(n, raising=False)
    monkeypatch.delenv("SECRETS_PROJECT", raising=False)


def test_no_project_means_no_calls():
    fake = FakeSecretManager({})
    assert secrets.load(client=fake, names=NAMES) == []
    assert fake.asked == []


def test_fills_missing_values_from_the_latest_version(monkeypatch):
    fake = FakeSecretManager({"SLACK_BOT_TOKEN": "xoxb-secret", "AUDIT_HMAC_KEY": "k"})
    assert secrets.load("backline-508417", fake, NAMES) == list(NAMES)
    import os

    assert os.environ["SLACK_BOT_TOKEN"] == "xoxb-secret"
    assert fake.asked[0] == "projects/backline-508417/secrets/SLACK_BOT_TOKEN/versions/latest"


def test_existing_environment_variable_wins(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-local")
    fake = FakeSecretManager({"SLACK_BOT_TOKEN": "xoxb-remote", "AUDIT_HMAC_KEY": "k"})
    assert secrets.load("p", fake, NAMES) == ["AUDIT_HMAC_KEY"]
    import os

    assert os.environ["SLACK_BOT_TOKEN"] == "xoxb-local"


def test_errors_name_the_secret_but_never_the_value():
    fake = FakeSecretManager({"SLACK_BOT_TOKEN": "xoxb-very-secret"}, deny={"AUDIT_HMAC_KEY"})
    with pytest.raises(secrets.SecretsError) as e:
        secrets.load("p", fake, NAMES)
    assert "AUDIT_HMAC_KEY: PermissionError" in str(e.value)
    assert "xoxb-very-secret" not in str(e.value)


def test_does_not_connect_when_nothing_is_missing(monkeypatch):
    for n in NAMES:
        monkeypatch.setenv(n, "set")

    def boom():
        raise AssertionError("should not connect")

    assert secrets.load("p", names=NAMES, connect=boom) == []


def test_connect_turns_credential_errors_into_a_clear_message(monkeypatch):
    sm = pytest.importorskip("google.cloud.secretmanager")

    def no_credentials(*a, **k):
        raise RuntimeError("Your default credentials were not found")

    monkeypatch.setattr(sm, "SecretManagerServiceClient", no_credentials)
    with pytest.raises(secrets.SecretsError, match="application-default login"):
        secrets.load("p", names=NAMES)


def test_secret_manager_beats_env_file_but_not_the_shell(monkeypatch, tmp_path):
    import os

    from dotenv import dotenv_values

    from adapters.slack_app import load_config

    for n in (*NAMES, "SECRETS_PROJECT"):
        monkeypatch.setenv(n, "x")  # makes monkeypatch restore the original state afterwards
        monkeypatch.delenv(n)
    env = tmp_path / ".env"
    env.write_text("SLACK_BOT_TOKEN=from-file\nAUDIT_HMAC_KEY=from-file\nSECRETS_PROJECT=p\n")
    fake = FakeSecretManager({"SLACK_BOT_TOKEN": "from-sm", "AUDIT_HMAC_KEY": "from-sm"})
    monkeypatch.setattr(secrets, "SECRET_NAMES", NAMES)
    monkeypatch.setenv("AUDIT_HMAC_KEY", "from-shell")

    load_config(str(env), dotenv_values(env), client=fake)
    assert os.environ["SLACK_BOT_TOKEN"] == "from-sm"  # Secret Manager beats the file
    assert os.environ["AUDIT_HMAC_KEY"] == "from-shell"  # the shell beats both
