"""Deploys Recall's cloud to the Supabase project in .env. Safe to re-run: every step is idempotent.

1. SQL migrations in supabase/migrations (each applied once)
2. The understand job: pg_cron calls the function every minute with the secret key (kept in Vault)
3. Function secrets: the LLM keys
4. Edge Functions: ingest, understand, search, mcp
5. Auth: the OAuth 2.1 server for MCP clients, with the consent page on Recall's localhost:8766

Needs SUPABASE_ACCESS_TOKEN (a personal access token) in .env. Prints no secret values.
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.supabase.com/v1/projects"
FUNCTIONS = ["ingest", "understand", "search", "mcp"]
CONSENT_ORIGIN = "http://localhost:8766"


def load_env() -> dict:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.startswith("#") and value.strip():
            env[key.strip()] = value.strip()
    return env


def api(env: dict, method: str, path: str, body=None):
    req = urllib.request.Request(
        f"{API}/{env['REF']}{path}", method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {env['SUPABASE_ACCESS_TOKEN']}", "Content-Type": "application/json",
                 "User-Agent": "recall-deploy"})
    try:
        with urllib.request.urlopen(req, timeout=120) as res:
            data = res.read()
            return json.loads(data) if data else None
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path} failed: {e.code} {e.read().decode(errors='replace')[:500]}")


def sql(env: dict, query: str):
    return api(env, "POST", "/database/query", {"query": query})


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def migrate(env: dict) -> None:
    sql(env, "create schema if not exists recall_private; "
             "create table if not exists recall_private.migrations (name text primary key, applied timestamptz default now())")
    applied = {row["name"] for row in sql(env, "select name from recall_private.migrations")}
    for path in sorted((ROOT / "supabase" / "migrations").glob("*.sql")):
        if path.name in applied:
            continue
        sql(env, path.read_text(encoding="utf-8") +
            f"\n;insert into recall_private.migrations (name) values ({literal(path.name)});")
        print(f"  applied {path.name}")
    print("migrations up to date")


def schedule_understanding(env: dict) -> None:
    secrets = {"recall_project_url": env["SUPABASE_URL"], "recall_secret_key": env["SUPABASE_SERVICE_ROLE_KEY"]}
    for name, value in secrets.items():
        sql(env, f"select vault.update_secret(id, {literal(value)}) from vault.secrets where name = {literal(name)}; "
                 f"select vault.create_secret({literal(value)}, {literal(name)}) "
                 f"where not exists (select 1 from vault.secrets where name = {literal(name)});")
    jobs = {"recall-understand": ("* * * * *", "{}"), "recall-digests": ("7 * * * *", '{"task": "digests"}')}
    for name, (schedule, body) in jobs.items():
        sql(env, f"""select cron.schedule('{name}', '{schedule}', $job$
            select net.http_post(
                url := (select decrypted_secret from vault.decrypted_secrets where name = 'recall_project_url')
                       || '/functions/v1/understand',
                headers := jsonb_build_object(
                    'apikey', (select decrypted_secret from vault.decrypted_secrets where name = 'recall_secret_key'),
                    'Content-Type', 'application/json'),
                body := '{body}'::jsonb,
                timeout_milliseconds := 90000)
        $job$)""")
    print("understand job every minute, digests hourly")


def set_secrets(env: dict) -> None:
    names = [n for n in ("GROQ_API_KEY", "CEREBRAS_API_KEY") if n in env]
    api(env, "POST", "/secrets", [{"name": n, "value": env[n]} for n in names])
    print(f"function secrets set: {', '.join(names)}")


def deploy_functions(env: dict) -> None:
    run_env = os.environ | {"SUPABASE_ACCESS_TOKEN": env["SUPABASE_ACCESS_TOKEN"]}
    for name in FUNCTIONS:
        subprocess.run(["npx", "-y", "supabase@2.118.0", "functions", "deploy", name, "--project-ref", env["REF"],
                        "--use-api"], cwd=ROOT, env=run_env, check=True, shell=os.name == "nt")
    print(f"functions deployed: {', '.join(FUNCTIONS)}")


def configure_auth(env: dict) -> None:
    api(env, "PATCH", "/config/auth", {
        "site_url": CONSENT_ORIGIN,
        "oauth_server_enabled": True,
        "oauth_server_authorization_path": "/oauth/consent",
        "oauth_server_allow_dynamic_registration": True,  # MCP clients register themselves; you approve each one
    })
    print(f"OAuth server on; consent page at {CONSENT_ORIGIN}/oauth/consent")


def main(steps: list[str]) -> None:
    env = load_env()
    if "SUPABASE_ACCESS_TOKEN" not in env:
        sys.exit("Add SUPABASE_ACCESS_TOKEN to .env (supabase.com/dashboard/account/tokens)")
    env["REF"] = env["SUPABASE_URL"].split("//")[1].split(".")[0]
    all_steps = {"migrate": migrate, "schedule": schedule_understanding, "secrets": set_secrets,
                 "functions": deploy_functions, "auth": configure_auth}
    for name in steps or all_steps:
        all_steps[name](env)


if __name__ == "__main__":
    main(sys.argv[1:])
