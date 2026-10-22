#!/usr/bin/env python3
"""Run ai_core against the existing localhost camera_ai PostgreSQL database."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
COMPOSE_FILE = ROOT / "docker-compose.yml"


class StartupError(Exception):
    pass


def read_dotenv() -> dict[str, str]:
    if not ENV_FILE.is_file():
        raise StartupError(".env is missing. Copy .env.example and set the database credentials.")
    values: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def container_database_url(values: dict[str, str]) -> str:
    username = values.get("DB_USERNAME", "")
    password = values.get("DB_PASSWORD", "")
    local_url = values.get("DATABASE_URL", "")
    if username != "postgres" or not password or not local_url:
        raise StartupError("Set DB_USERNAME=postgres, DB_PASSWORD, and DATABASE_URL in .env.")

    try:
        parsed = urlsplit(local_url)
        valid_target = (
            parsed.scheme == "postgresql"
            and parsed.hostname == "127.0.0.1"
            and parsed.port == 5432
            and parsed.path == "/camera_ai"
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError as exc:
        raise StartupError("DATABASE_URL is invalid.") from exc

    if not valid_target:
        raise StartupError("DATABASE_URL must target postgresql://postgres:<password>@127.0.0.1:5432/camera_ai.")
    if unquote(parsed.username or "") != username or unquote(parsed.password or "") != password:
        raise StartupError("DATABASE_URL credentials do not match DB_USERNAME and DB_PASSWORD.")

    return f"postgresql://{quote(username, safe='')}:{quote(password, safe='')}@host.docker.internal:5432/camera_ai"


def docker_compose(env: dict[str, str], *args: str) -> None:
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), *args],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError as exc:
        raise StartupError("Docker CLI is unavailable.") from exc
    if result.returncode:
        # Compose errors can include interpolated credentials.
        raise StartupError("Docker Compose failed. Check Docker status and the ai_core logs.")


def main() -> None:
    values = read_dotenv()
    container_url = container_database_url(values)
    compose_env = dict(os.environ)
    compose_env["DATABASE_URL"] = container_url
    docker_compose(compose_env, "config", "--quiet")
    docker_compose(compose_env, "up", "-d", "--build", "ai_core")
    print("ai_core is running with camera_ai on the existing PostgreSQL at 127.0.0.1:5432.")


if __name__ == "__main__":
    try:
        main()
    except StartupError as exc:
        print(f"Startup failed: {exc}", file=sys.stderr)
        sys.exit(1)
