"""Phase 16 tests — Environment & Credentials Management.

Tests env resolver, secret masking, Settings API endpoints,
CodexWrapper env injection, and config persistence.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_os.api.deps import orch_holder
from agent_os.config.env import (
    build_codex_env,
    mask_secret,
    resolve_all,
    resolve_secret,
)
from agent_os.config.schema import (
    AgentOSConfig,
    GitHubConfig,
    SecretsConfig,
)


# ---------- Fixtures --------------------------------------------------------


@pytest.fixture()
def tmp_config(tmp_path: Path) -> AgentOSConfig:
    db_path = str(tmp_path / "test.db")
    return AgentOSConfig(storage={"db_path": db_path})


@pytest.fixture()
def app_client(tmp_config: AgentOSConfig):
    """Create a TestClient with settings router included."""
    from agent_os.api.routes import bus, metrics, modules, pipeline, requirements, settings

    orch_holder.shutdown()
    orch = orch_holder.init(tmp_config)

    @asynccontextmanager
    async def noop_lifespan(app):
        yield

    app = FastAPI(lifespan=noop_lifespan)
    app.include_router(pipeline.router)
    app.include_router(modules.router)
    app.include_router(requirements.router)
    app.include_router(metrics.router)
    app.include_router(bus.router)
    app.include_router(settings.router)

    with TestClient(app) as client:
        yield client, orch

    orch_holder.shutdown()


@pytest.fixture()
def dotenv_file(tmp_path: Path) -> Path:
    """Create a .env file in tmp_path for testing."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        'OPENAI_API_KEY="sk-from-dotenv-12345"\n'
        'GITHUB_TOKEN="ghp_from_dotenv_67890"\n'
    )
    return tmp_path


# ---------- Test: Env Resolver Priority ------------------------------------


class TestEnvResolver:

    def test_config_value_wins_over_env_and_dotenv(self, dotenv_file: Path):
        """Config.yaml value has highest priority."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-from-env"}):
            result = resolve_secret(
                "openai_api_key",
                config_value="sk-from-config",
                project_root=str(dotenv_file),
            )
        assert result == "sk-from-config"

    def test_dotenv_wins_over_os_environ(self, dotenv_file: Path):
        """Dotenv file value wins over os.environ when config is empty."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-from-env"}):
            result = resolve_secret(
                "openai_api_key",
                config_value="",
                project_root=str(dotenv_file),
            )
        assert result == "sk-from-dotenv-12345"

    def test_os_environ_fallback(self, tmp_path: Path):
        """Falls back to os.environ when config and .env are empty."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-from-env"}):
            result = resolve_secret(
                "openai_api_key",
                config_value="",
                project_root=str(tmp_path),  # No .env file here
            )
        assert result == "sk-from-env"

    def test_empty_when_nowhere(self, tmp_path: Path):
        """Returns empty string when secret is not found anywhere."""
        with patch.dict(os.environ, {}, clear=True):
            # Make sure OPENAI_API_KEY is not in the env
            os.environ.pop("OPENAI_API_KEY", None)
            result = resolve_secret(
                "openai_api_key",
                config_value="",
                project_root=str(tmp_path),
            )
        assert result == ""

    def test_resolve_all(self, dotenv_file: Path):
        """resolve_all returns both keys."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            os.environ.pop("GITHUB_TOKEN", None)
            result = resolve_all(
                config_openai="",
                config_github="",
                project_root=str(dotenv_file),
            )
        assert result["openai_api_key"] == "sk-from-dotenv-12345"
        assert result["github_token"] == "ghp_from_dotenv_67890"

    def test_dotenv_ignores_comments_and_blanks(self, tmp_path: Path):
        """Dotenv parser skips comments and blank lines."""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "# This is a comment\n"
            "\n"
            "OPENAI_API_KEY=sk-test-key\n"
            "  \n"
        )
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            result = resolve_secret(
                "openai_api_key", config_value="", project_root=str(tmp_path),
            )
        assert result == "sk-test-key"


# ---------- Test: Secret Masking -------------------------------------------


class TestSecretMasking:

    def test_mask_normal_key(self):
        assert mask_secret("sk-proj-abcdefghijklmnop") == "sk-...mnop"

    def test_mask_short_key(self):
        assert mask_secret("short") == "***"

    def test_mask_empty(self):
        assert mask_secret("") == ""

    def test_mask_exactly_10_chars(self):
        result = mask_secret("1234567890")
        assert result == "123...7890"


# ---------- Test: build_codex_env ------------------------------------------


class TestBuildCodexEnv:

    def test_injects_resolved_key(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text('OPENAI_API_KEY=sk-from-dotenv\n')
        env = build_codex_env(config_openai="", project_root=str(tmp_path))
        assert env["OPENAI_API_KEY"] == "sk-from-dotenv"

    def test_config_value_overrides(self, tmp_path: Path):
        env = build_codex_env(config_openai="sk-from-config", project_root=str(tmp_path))
        assert env["OPENAI_API_KEY"] == "sk-from-config"

    def test_preserves_other_env_vars(self, tmp_path: Path):
        with patch.dict(os.environ, {"MY_CUSTOM_VAR": "hello"}):
            env = build_codex_env(config_openai="sk-x", project_root=str(tmp_path))
        assert env["MY_CUSTOM_VAR"] == "hello"


# ---------- Test: Config Models --------------------------------------------


class TestConfigModels:

    def test_github_config_defaults(self):
        cfg = GitHubConfig()
        assert cfg.owner == ""
        assert cfg.repo == ""
        assert cfg.auto_push is False
        assert cfg.auto_create_pr is False

    def test_secrets_config_defaults(self):
        cfg = SecretsConfig()
        assert cfg.openai_api_key == ""
        assert cfg.github_token == ""

    def test_agentosconfig_has_github_and_secrets(self):
        cfg = AgentOSConfig()
        assert hasattr(cfg, "github")
        assert hasattr(cfg, "secrets")
        assert isinstance(cfg.github, GitHubConfig)
        assert isinstance(cfg.secrets, SecretsConfig)

    def test_github_config_custom(self):
        cfg = GitHubConfig(owner="myorg", repo="myrepo", auto_push=True)
        assert cfg.owner == "myorg"
        assert cfg.repo == "myrepo"
        assert cfg.auto_push is True

    def test_config_from_yaml_dict(self):
        raw = {
            "github": {"owner": "acme", "repo": "proj", "auto_push": True},
            "secrets": {"openai_api_key": "sk-test", "github_token": "ghp_test"},
        }
        cfg = AgentOSConfig(**raw)
        assert cfg.github.owner == "acme"
        assert cfg.secrets.openai_api_key == "sk-test"


# ---------- Test: Settings API ---------------------------------------------


class TestSettingsAPI:

    def test_get_settings_returns_masked(self, app_client):
        client, orch = app_client
        orch.config.secrets.openai_api_key = "sk-proj-abcdefghijklmnop"
        orch.config.secrets.github_token = "ghp_abcdefghijklmnop"
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        # Secrets should be masked
        assert data["secrets"]["openai_api_key"].startswith("sk-")
        assert "..." in data["secrets"]["openai_api_key"]
        assert data["secrets"]["github_token"].startswith("ghp")
        assert "..." in data["secrets"]["github_token"]

    def test_get_settings_structure(self, app_client):
        client, _orch = app_client
        resp = client.get("/api/settings")
        data = resp.json()
        assert "secrets" in data
        assert "github" in data
        assert "project" in data
        assert "pipeline" in data

    def test_update_settings_project(self, app_client):
        client, orch = app_client
        resp = client.put(
            "/api/settings",
            json={
                "project": {"name": "TestProj", "root_path": "/tmp/test", "language": "go"},
            },
        )
        assert resp.status_code == 200
        assert orch.config.project.name == "TestProj"
        assert orch.config.project.language == "go"

    def test_update_settings_github(self, app_client):
        client, orch = app_client
        resp = client.put(
            "/api/settings",
            json={
                "github": {"owner": "myorg", "repo": "myrepo", "auto_push": True, "auto_create_pr": True},
            },
        )
        assert resp.status_code == 200
        assert orch.config.github.owner == "myorg"
        assert orch.config.github.auto_push is True

    def test_update_settings_pipeline(self, app_client):
        client, orch = app_client
        resp = client.put(
            "/api/settings",
            json={
                "pipeline": {"max_iterations_per_module": 10, "convergence_rule": "no_critical", "auto_approve_hitl": True},
            },
        )
        assert resp.status_code == 200
        assert orch.config.orchestrator.max_iterations_per_module == 10
        assert orch.config.orchestrator.auto_approve_hitl is True

    def test_update_secrets_skips_masked_values(self, app_client):
        client, orch = app_client
        orch.config.secrets.openai_api_key = "sk-original-key-here-abcd"
        resp = client.put(
            "/api/settings",
            json={
                "secrets": {"openai_api_key": "***", "github_token": "ghp_new_token_here12"},
            },
        )
        assert resp.status_code == 200
        # Masked value should NOT overwrite the real key
        assert orch.config.secrets.openai_api_key == "sk-original-key-here-abcd"
        # New token should be set
        assert orch.config.secrets.github_token == "ghp_new_token_here12"

    def test_update_secrets_sets_new_key(self, app_client):
        client, orch = app_client
        resp = client.put(
            "/api/settings",
            json={
                "secrets": {"openai_api_key": "sk-brand-new-key-12345", "github_token": ""},
            },
        )
        assert resp.status_code == 200
        assert orch.config.secrets.openai_api_key == "sk-brand-new-key-12345"


class TestTestGitHub:

    @patch("agent_os.api.routes.settings.resolve_secret", return_value="")
    @patch("agent_os.api.routes.settings.httpx")
    def test_no_token(self, mock_httpx, mock_resolve, app_client):
        client, _orch = app_client
        resp = client.post("/api/settings/test-github")
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert "No GitHub token" in data["message"]

    @patch("agent_os.api.routes.settings.httpx")
    def test_valid_token(self, mock_httpx, app_client):
        client, orch = app_client
        orch.config.secrets.github_token = "ghp_valid_token_12345"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"login": "testuser"}
        mock_httpx.get.return_value = mock_resp

        resp = client.post("/api/settings/test-github")
        data = resp.json()
        assert data["valid"] is True
        assert data["user"] == "testuser"

    @patch("agent_os.api.routes.settings.httpx")
    def test_invalid_token(self, mock_httpx, app_client):
        client, orch = app_client
        orch.config.secrets.github_token = "ghp_bad_token_12345678"

        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_httpx.get.return_value = mock_resp

        resp = client.post("/api/settings/test-github")
        data = resp.json()
        assert data["valid"] is False
        assert "401" in data["message"]


# ---------- Test: CodexWrapper env injection --------------------------------


class TestCodexWrapperEnv:

    def test_wrapper_stores_api_key(self):
        from agent_os.codex.wrapper import CodexWrapper
        w = CodexWrapper(
            timeout_seconds=60,
            max_retries=0,
            openai_api_key="sk-test-key-here",
            project_root="/tmp",
        )
        assert w._openai_api_key == "sk-test-key-here"
        assert w._project_root == "/tmp"

    def test_wrapper_defaults(self):
        from agent_os.codex.wrapper import CodexWrapper
        w = CodexWrapper()
        assert w._openai_api_key == ""
        assert w._project_root == "."


# ---------- Test: Config YAML write ----------------------------------------


class TestConfigPersistence:

    def test_write_config_yaml(self, tmp_path: Path, app_client):
        """_write_config_yaml writes the expected structure to the given path."""
        import yaml
        from agent_os.api.routes.settings import _write_config_yaml

        client, orch = app_client
        yaml_path = tmp_path / "config.yaml"

        # Pass an explicit path — no CWD gymnastics needed
        _write_config_yaml(orch.config, yaml_path)

        written = yaml.safe_load(yaml_path.read_text())
        assert written["github"]["owner"] == ""
        assert written["secrets"]["openai_api_key"] == ""
        assert written["secrets"]["github_token"] == ""
        assert "project" in written
        assert "orchestrator" in written

    def test_write_config_yaml_noop_without_path(self, tmp_path: Path, app_client):
        """_write_config_yaml does nothing when config_path is None (test/in-memory)."""
        from agent_os.api.routes.settings import _write_config_yaml

        _client, orch = app_client
        yaml_path = tmp_path / "should_not_exist.yaml"

        # Must not create the file
        _write_config_yaml(orch.config, None)
        assert not yaml_path.exists()
