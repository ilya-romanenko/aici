#!/usr/bin/env bash
set -euo pipefail

log() {
  printf '[AICI] %s\n' "$*"
}

bool_true() {
  case "${1,,}" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

readonly config_template="${AICI_CONFIG_TEMPLATE:-/app/config/pipeline.json}"
readonly rendered_config="${AI_CRYPTO_CONFIG:-/app/config/pipeline.runtime.json}"

render_config() {
  log "Rendering config from ${config_template} to ${rendered_config}."
  AICI_CONFIG_TEMPLATE="${config_template}" AI_CRYPTO_CONFIG="${rendered_config}" python - <<'PY'
import json
import os
import pathlib
import sys
from typing import Any


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def override(config: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    cursor: dict[str, Any] = config
    for key in path[:-1]:
        cursor = cursor.setdefault(key, {})
    cursor[path[-1]] = value


template_path = pathlib.Path(os.getenv("AICI_CONFIG_TEMPLATE", "/app/config/pipeline.json")).resolve()
output_path = pathlib.Path(os.getenv("AI_CRYPTO_CONFIG", "/app/config/pipeline.runtime.json")).resolve()

if not template_path.exists():
    sys.stderr.write(f"Config template not found at {template_path}\n")
    sys.exit(1)

config = json.loads(template_path.read_text(encoding="utf-8"))
auth_defaults = dict(config.get("auth") or {})

storage_str_overrides = (
    ("AICI_RUNS_ROOT", ("runs", "root")),
    ("AICI_DATA_ROOT", ("data", "root")),
)

for env_name, path in storage_str_overrides:
    value = os.getenv(env_name)
    if value:
        override(config, path, value)

auth_str_overrides = (
    ("AICI_AUTH_DATABASE_URL", ("auth", "database_url")),
    ("AICI_AUTH_JWT_SECRET", ("auth", "jwt_secret_key")),
    ("AICI_AUTH_JWT_ALG", ("auth", "jwt_algorithm")),
    ("AICI_AUTH_SESSION_COOKIE", ("auth", "session_cookie_name")),
    ("AICI_AUTH_SESSION_DOMAIN", ("auth", "session_cookie_domain")),
    ("AICI_AUTH_APP_URL", ("auth", "public_app_url")),
)

for env_name, path in auth_str_overrides:
    value = os.getenv(env_name)
    if value:
        override(config, path, value)

auth_int_overrides = (
    ("AICI_AUTH_ACCESS_TTL", ("auth", "access_token_ttl_seconds")),
    ("AICI_AUTH_REFRESH_TTL", ("auth", "refresh_token_ttl_seconds")),
    ("AICI_AUTH_EMAIL_TOKEN_TTL", ("auth", "email_token_ttl_seconds")),
    ("AICI_AUTH_RESET_TOKEN_TTL", ("auth", "password_reset_ttl_seconds")),
)

for env_name, path in auth_int_overrides:
    if os.getenv(env_name) is not None:
        default = int(auth_defaults.get(path[-1], 0) or 0)
        override(config, path, env_int(env_name, default))

auth_bool_overrides = (
    ("AICI_AUTH_SESSION_SECURE", ("auth", "session_cookie_secure")),
    ("AICI_AUTH_DEBUG_TOKENS", ("auth", "expose_tokens_in_responses")),
    ("AICI_AUTH_ECHO_SQL", ("auth", "echo_sql")),
)

for env_name, path in auth_bool_overrides:
    if os.getenv(env_name) is not None:
        default = bool(auth_defaults.get(path[-1], False))
        override(config, path, env_bool(env_name, default))

billing_overrides = (
    ("AICI_BILLING_TRIAL_DAYS", ("billing", "trial_days")),
    ("AICI_BILLING_ENTERPRISE_TERMS_DAYS", ("billing", "enterprise_invoice_terms_days")),
)

for env_name, path in billing_overrides:
    if os.getenv(env_name) is not None:
        override(config, path, env_int(env_name, int((config.get("billing") or {}).get(path[-1], 0) or 0)))

billing = config.setdefault("billing", {})
stripe = billing.setdefault("stripe", {})

stripe_overrides = (
    ("AICI_STRIPE_SECRET_KEY", "secret_key"),
    ("AICI_STRIPE_PUBLISHABLE_KEY", "publishable_key"),
    ("AICI_STRIPE_WEBHOOK_SECRET", "webhook_secret"),
)

for env_name, key in stripe_overrides:
    value = os.getenv(env_name)
    if value:
        stripe[key] = value

plans = billing.get("plans")
if isinstance(plans, dict):
    for plan in plans.values():
        if not isinstance(plan, dict):
            continue
        env_name = plan.get("price_id_env")
        if env_name:
            value = os.getenv(env_name)
            if value:
                plan["price_id"] = value

api_keys = config.setdefault("api_keys", {})
encryption_secret = os.getenv("AICI_API_KEY_SECRET")
if encryption_secret:
    api_keys["encryption_secret"] = encryption_secret

rotation_webhooks = os.getenv("AICI_API_KEY_ROTATION_WEBHOOKS")
usage_webhooks = os.getenv("AICI_API_KEY_USAGE_WEBHOOKS")

if rotation_webhooks:
    api_keys.setdefault("webhooks", {})
    api_keys["webhooks"]["rotation"] = [url.strip() for url in rotation_webhooks.split(",") if url.strip()]

if usage_webhooks:
    api_keys.setdefault("webhooks", {})
    api_keys["webhooks"]["usage_alerts"] = [url.strip() for url in usage_webhooks.split(",") if url.strip()]

output_path.parent.mkdir(parents=True, exist_ok=True)
output_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
print(f"Wrote merged config to {output_path}")
PY
}

readonly host="${AICI_HOST:-0.0.0.0}"
readonly port="${AICI_PORT:-8000}"

bootstrap_mode="${AICI_RUN_PIPELINE_ON_START:-auto}"
bootstrap_fail_policy="${AICI_FAIL_ON_BOOTSTRAP_ERROR:-0}"

render_config

export AI_CRYPTO_CONFIG="${rendered_config}"

ensure_storage_contract() {
  python - <<'PY'
import json
import os
from pathlib import Path


def resolve_root(config_path: Path, configured: str, fallback: str) -> Path:
    candidate = Path(configured or fallback)
    if candidate.is_absolute():
        return candidate
    config_dir = config_path.resolve().parent
    base_dir = config_dir.parent if config_dir.name.lower() == "config" else config_dir
    return (base_dir / candidate).resolve()


config_path = Path(os.getenv("AI_CRYPTO_CONFIG", "/app/config/pipeline.runtime.json")).resolve()
raw = json.loads(config_path.read_text(encoding="utf-8"))
runs_root = resolve_root(config_path, str((raw.get("runs") or {}).get("root", "runs")), "runs")
data_root = resolve_root(config_path, str((raw.get("data") or {}).get("root", "data")), "data")
performance_series_root = runs_root / "_performance" / "series"

for label, path in (
    ("runs_root", runs_root),
    ("data_root", data_root),
    ("performance_series_root", performance_series_root),
):
    path.mkdir(parents=True, exist_ok=True)
    print(f"{label}={path}")
PY
}

log "Ensuring persistent storage roots exist."
while IFS= read -r line; do
  [[ -n "${line}" ]] && log "Storage: ${line}"
done < <(ensure_storage_contract)

ensure_bootstrap_needed() {
  python - <<'PY'
from ai_crypto_index.shared import run_store
from ai_crypto_index.shared.settings import get_settings

settings = get_settings()
latest = run_store.find_latest_run(settings)
print("missing" if latest is None else "present")
PY
}

should_bootstrap() {
  local status="$1"
  case "${bootstrap_mode,,}" in
    never|0|false|off) return 1 ;;
    always|1|true|on) return 0 ;;
    auto)
      [[ "$status" == "missing" ]] && return 0 || return 1
      ;;
    *)
      log "Unknown AICI_RUN_PIPELINE_ON_START value '${bootstrap_mode}', falling back to 'auto'"
      [[ "$status" == "missing" ]] && return 0 || return 1
      ;;
  esac
}

status="$(ensure_bootstrap_needed)"
if should_bootstrap "$status"; then
  log "Bootstrapping pipeline run (status=${status})."
  if python -m ai_crypto_index.pipelines.main; then
    log "Pipeline bootstrap completed."
  else
    log "Pipeline bootstrap failed."
    if bool_true "$bootstrap_fail_policy"; then
      log "Failing container startup due to bootstrap error."
      exit 1
    fi
  fi
else
  log "Skipping pipeline bootstrap (status=${status}, mode=${bootstrap_mode})."
fi

forwarded_allow_ips="${AICI_FORWARDED_ALLOW_IPS:-*}"

uvicorn_cmd=(
  uvicorn
  ai_crypto_index.api.app:app
  --host "${host}"
  --port "${port}"
  --proxy-headers
  --forwarded-allow-ips "${forwarded_allow_ips}"
)

if [[ -n "${AICI_UVICORN_WORKERS:-}" ]]; then
  uvicorn_cmd+=(--workers "${AICI_UVICORN_WORKERS}")
fi

if [[ -n "${AICI_UVICORN_RELOAD:-}" && "${AICI_UVICORN_RELOAD}" != "0" ]]; then
  uvicorn_cmd+=(--reload)
fi

log "Starting API server on ${host}:${port}."
exec "${uvicorn_cmd[@]}"
