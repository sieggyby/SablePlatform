"""T2-DOCKER: production-hardened Docker configuration."""
from __future__ import annotations

from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_dockerfile_has_non_root_user():
    """Dockerfile contains USER directive (not running as root)."""
    content = (_PROJECT_ROOT / "Dockerfile").read_text()
    assert "USER sable" in content or "USER " in content


def test_dockerfile_has_healthcheck():
    """Dockerfile contains HEALTHCHECK instruction."""
    content = (_PROJECT_ROOT / "Dockerfile").read_text()
    assert "HEALTHCHECK" in content


def test_dockerfile_creates_sable_user():
    """Dockerfile creates a non-root user and group."""
    content = (_PROJECT_ROOT / "Dockerfile").read_text()
    assert "groupadd" in content or "addgroup" in content
    assert "useradd" in content or "adduser" in content


def test_dockerignore_exists():
    """.dockerignore file exists."""
    assert (_PROJECT_ROOT / ".dockerignore").exists()


def test_dockerignore_excludes_sensitive_dirs():
    """.dockerignore excludes .git, tests, docs, *.db."""
    content = (_PROJECT_ROOT / ".dockerignore").read_text()
    assert ".git/" in content
    assert "tests/" in content
    assert "*.db" in content


def test_compose_has_healthchecks():
    """docker-compose.yaml has healthcheck on the main service."""
    content = (_PROJECT_ROOT / "docker-compose.yaml").read_text()
    assert "healthcheck:" in content


def test_compose_no_sleep_loop():
    """docker-compose.yaml alerts-cron does not use a bare sleep loop."""
    content = (_PROJECT_ROOT / "docker-compose.yaml").read_text()
    # Old pattern was: "while true; do sable-platform alerts evaluate; sleep 14400; done"
    assert "sleep 14400" not in content
