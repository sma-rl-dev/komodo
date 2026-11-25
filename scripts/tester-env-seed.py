#!/usr/bin/env python3
"""Deterministic seed data for the Komodo tester-env deployment.

Data theme: "Meridian Pay", a payments platform team that manages its
infrastructure from Komodo. Only the socket-free domain is seeded: tags,
variables, procedures, actions, users and user groups, alerters, a
host-file ResourceSync and a repo registration. Nothing here clones a repo,
runs a Deno action or reaches the network.

Idempotent: every record is looked up by its natural name first and only
created when missing. Re-running the seed on the same volume reports the
already-present records as unchanged.

Usage (normally invoked by ./tester-env seed):
    TESTER_ENV_LOCAL_URL=... KOMODO_JWT=... CORE_CONTAINER=... \
        python3 scripts/tester-env-seed.py

Environment:
    TESTER_ENV_LOCAL_URL  base URL, default http://127.0.0.1:19120
    KOMODO_JWT            admin JWT (required)
    CORE_CONTAINER        core container name for the sync host file
    TESTER_ENV_SEED_PASSWORD  password for the seeded users
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request

BASE_URL = os.environ.get("TESTER_ENV_LOCAL_URL", "http://127.0.0.1:19120").rstrip("/")
JWT = os.environ.get("KOMODO_JWT", "")
CORE_CONTAINER = os.environ.get("CORE_CONTAINER", "tester-env-komodo-core")
PASSWORD = os.environ.get("TESTER_ENV_SEED_PASSWORD", "Tester-env-Password-123")

SYNC_NAME = "Infrastructure as Code"
SYNC_DIR = "/syncs/Infrastructure_as_Code"
SYNC_FILE = "variables.toml"
SYNC_VARIABLES = {"LOG_LEVEL": "info", "SNAPSHOT_BUCKET": "meridian-snapshots"}

TAGS = [
    ("production", "Green"),
    ("staging", "Amber"),
    ("internal", "Slate"),
]

VARIABLES = [
    ("ALERT_WEBHOOK_URL", "http://localhost:7000/alerts/platform",
     "Delivery target for platform alert notifications", False),
    ("BACKUP_RETENTION_DAYS", "30",
     "Days of database snapshots kept in cold storage", False),
    ("DEPLOY_TOKEN", "meridian-deploy-token-2026",
     "Token used by the release pipeline when deploying", True),
]

PROCEDURES = [
    {
        "name": "Nightly Database Snapshot",
        "config": {
            "stages": [
                {
                    "name": "Quiesce application",
                    "enabled": True,
                    "executions": [
                        {"execution": {"type": "Sleep", "params": {"duration_ms": 2000}},
                         "enabled": True}
                    ],
                },
                {
                    "name": "Copy snapshot to cold storage",
                    "enabled": True,
                    "executions": [
                        {"execution": {"type": "Sleep", "params": {"duration_ms": 5000}},
                         "enabled": True}
                    ],
                },
            ],
            "schedule_format": "Cron",
            "schedule": "0 0 2 * * ?",
            "schedule_enabled": True,
            "schedule_timezone": "UTC",
        },
    },
    {
        "name": "Rotate Application Logs",
        "config": {
            "stages": [
                {
                    "name": "Archive and rotate",
                    "enabled": True,
                    "executions": [
                        {"execution": {"type": "Sleep", "params": {"duration_ms": 3000}},
                         "enabled": True}
                    ],
                }
            ],
        },
    },
    {
        "name": "Quarterly Failover Drill",
        "config": {
            "stages": [
                {
                    "name": "Drain primary region",
                    "enabled": True,
                    "executions": [
                        {"execution": {"type": "Sleep", "params": {"duration_ms": 4000}},
                         "enabled": True}
                    ],
                },
                {
                    "name": "Promote standby region",
                    "enabled": True,
                    "executions": [
                        {"execution": {"type": "Sleep", "params": {"duration_ms": 4000}},
                         "enabled": True}
                    ],
                },
            ],
        },
    },
]

ACTIONS = [
    {
        "name": "Collect Server Inventory",
        "config": {
            "file_contents": (
                'const servers = await komodo.read("ListServers", {});\n'
                'console.log(`Inventory: ${servers.length} server(s) reporting`);\n'
                "for (const server of servers) {\n"
                '  console.log(`- ${server.name}: ${server.info?.state ?? "Unknown"}`);\n'
                "}\n"
            )
        },
    },
    {
        "name": "Check Backup Retention",
        "config": {
            "file_contents": (
                'const variables = await komodo.read("ListVariables", {});\n'
                'const retention = variables.find((v) => v.name === "BACKUP_RETENTION_DAYS");\n'
                'console.log(`Backup retention policy: ${retention?.value ?? "unset"} day(s)`);\n'
            )
        },
    },
    {
        "name": "Publish Release Notes",
        "config": {
            "file_contents": (
                'console.log("Release notes for meridian-pay-api published to the changelog");\n'
                'console.log("Affected services: checkout, ledger, notifications");\n'
            )
        },
    },
]

USERS = ["dana.whitfield", "marco.ruiz"]

USER_GROUPS = [
    ("Platform Operations", "dana.whitfield", [("Server", "Read")]),
    ("Release Managers", "marco.ruiz",
     [("Server", "Read"), ("Procedure", "Execute"), ("ResourceSync", "Read")]),
]

# One per-target permission, visible in the procedure's permissions panel.
TARGET_PERMISSIONS = [
    ("user_group", "Platform Operations", "Procedure", "Rotate Application Logs", "Execute"),
]

ALERTERS = [
    ("Platform Pager", True, "http://localhost:7000/alerts/platform"),
    ("Weekly Maintenance Digest", False, "http://localhost:7000/alerts/digest"),
]

# description, tags for created resources (applied with UpdateResourceMeta).
METADATA = {
    ("Procedure", "Nightly Database Snapshot"): (
        "Nightly snapshot of the payments database, retained in cold storage",
        ["production"]),
    ("Procedure", "Rotate Application Logs"): (
        "Archives and rotates application logs on the maintenance schedule",
        ["production"]),
    ("Procedure", "Quarterly Failover Drill"): (
        "Rehearses the regional failover runbook with the on-call team",
        ["internal"]),
    ("Action", "Collect Server Inventory"): (
        "Writes the current server inventory to the action update log",
        ["internal"]),
    ("Action", "Check Backup Retention"): (
        "Confirms the configured backup retention policy before the nightly run",
        ["production"]),
    ("Action", "Publish Release Notes"): (
        "Records the release summary for the changelog",
        ["staging"]),
    ("ResourceSync", SYNC_NAME): (
        "Declarative variable declarations managed from the operations checkout",
        ["production"]),
    ("Repo", "platform-infrastructure"): (
        "Shared infrastructure definitions for the platform team",
        ["production"]),
}

REPO_NAME = "platform-infrastructure"
REPO_URL = "meridianpay/platform-infrastructure"


def call(kind: str, method: str, params: dict):
    request = urllib.request.Request(
        f"{BASE_URL}/{kind}/{method}",
        data=json.dumps(params).encode(),
        headers={"content-type": "application/json", "authorization": JWT},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        raise SystemExit(f"{kind}/{method} failed: HTTP {error.code} {detail}")
    return json.loads(body) if body else None


def read(method: str):
    return call("read", method, {})


def write(method: str, params: dict):
    return call("write", method, params)


def id_of(item: dict) -> str:
    value = item.get("id") or item.get("_id")
    if isinstance(value, dict):
        value = value.get("$oid")
    return str(value)


def ensure(list_method: str, create_method: str, name: str,
           config: dict | None = None) -> tuple[dict, bool]:
    for item in read(list_method):
        if item.get("name") == name:
            return item, False
    params: dict = {"name": name}
    if config is not None:
        params["config"] = config
    return write(create_method, params), True


def sync_file_contents() -> str:
    blocks = [
        f'[[variable]]\nname = "{name}"\nvalue = "{value}"\n'
        for name, value in SYNC_VARIABLES.items()
    ]
    return "\n".join(blocks)


def write_sync_file() -> None:
    script = f'mkdir -p "{SYNC_DIR}" && cat > "{SYNC_DIR}/{SYNC_FILE}"'
    result = subprocess.run(
        ["docker", "exec", "-i", CORE_CONTAINER, "sh", "-c", script],
        input=sync_file_contents().encode(),
        capture_output=True,
    )
    if result.returncode != 0:
        raise SystemExit(
            "Failed to write the sync host file in "
            f"{CORE_CONTAINER}: {result.stderr.decode('utf-8', 'replace')}"
        )


def main() -> None:
    if not JWT:
        raise SystemExit("KOMODO_JWT is required")
    created: list[str] = []
    unchanged: list[str] = []

    def track(label: str, is_new: bool) -> None:
        (created if is_new else unchanged).append(label)

    for name, color in TAGS:
        tags = read("ListTags")
        if any(tag.get("name") == name for tag in tags):
            track(f"tag {name}", False)
        else:
            write("CreateTag", {"name": name, "color": color})
            track(f"tag {name}", True)

    for name, value, description, is_secret in VARIABLES:
        variables = read("ListVariables")
        if any(variable.get("name") == name for variable in variables):
            track(f"variable {name}", False)
            continue
        write("CreateVariable", {
            "name": name,
            "value": value,
            "description": description,
            "is_secret": is_secret,
        })
        track(f"variable {name}", True)

    for procedure in PROCEDURES:
        _, is_new = ensure("ListProcedures", "CreateProcedure",
                           procedure["name"], procedure["config"])
        track(f"procedure {procedure['name']}", is_new)

    for action in ACTIONS:
        _, is_new = ensure("ListActions", "CreateAction",
                           action["name"], action["config"])
        track(f"action {action['name']}", is_new)

    users = {user.get("username"): user for user in read("ListUsers")}
    for username in USERS:
        if username in users:
            track(f"user {username}", False)
            continue
        write("CreateLocalUser", {"username": username, "password": PASSWORD})
        track(f"user {username}", True)

    group_ids: dict[str, str] = {}
    for group_name, member, permissions in USER_GROUPS:
        group, is_new = ensure("ListUserGroups", "CreateUserGroup", group_name)
        group_ids[group_name] = id_of(group)
        track(f"user group {group_name}", is_new)
        write("AddUserToUserGroup", {"user_group": group_name, "user": member})
        for resource_type, level in permissions:
            write("UpdatePermissionOnResourceType", {
                "user_target": {"type": "UserGroup", "id": group_ids[group_name]},
                "resource_type": resource_type,
                "permission": {"level": level, "specific": []},
            })

    for _, group_name, resource_type, resource_name, level in TARGET_PERMISSIONS:
        target_id = next(
            id_of(item) for item in read(f"List{resource_type}s")
            if item.get("name") == resource_name
        )
        write("UpdatePermissionOnTarget", {
            "user_target": {"type": "UserGroup", "id": group_ids[group_name]},
            "resource_target": {"type": resource_type, "id": target_id},
            "permission": {"level": level, "specific": []},
        })

    for name, enabled, url in ALERTERS:
        _, is_new = ensure("ListAlerters", "CreateAlerter", name, {
            "enabled": enabled,
            "endpoint": {"type": "Custom", "params": {"url": url}},
        })
        track(f"alerter {name}", is_new)

    write_sync_file()
    _, is_new = ensure("ListResourceSyncs", "CreateResourceSync", SYNC_NAME, {
        "files_on_host": True,
        "resource_path": [SYNC_FILE],
        "include_variables": True,
    })
    track(f"resource sync {SYNC_NAME}", is_new)

    servers = read("ListServers")
    local = next((server for server in servers if server.get("name") == "Local"), None)
    if local is None:
        raise SystemExit("Server 'Local' is not visible; deploy is not ready")
    _, is_new = ensure("ListRepos", "CreateRepo", REPO_NAME, {
        "server_id": id_of(local),
        "repo": REPO_URL,
        "branch": "main",
        "git_provider": "github.com",
        "git_https": True,
    })
    track(f"repo {REPO_NAME}", is_new)

    list_methods = {
        "Procedure": "ListProcedures",
        "Action": "ListActions",
        "ResourceSync": "ListResourceSyncs",
        "Repo": "ListRepos",
    }
    for (resource_type, resource_name), (description, tags) in METADATA.items():
        target_id = next(
            id_of(item) for item in read(list_methods[resource_type])
            if item.get("name") == resource_name
        )
        write("UpdateResourceMeta", {
            "target": {"type": resource_type, "id": target_id},
            "description": description,
            "tags": tags,
            "template": None,
        })

    # One real procedure run creates execution history. The procedure only
    # sleeps, so this stays local and deterministic. Runs only once per volume;
    # wait for completion so verify sees the final state.
    rotate = next(
        item for item in read("ListProcedures")
        if item.get("name") == "Rotate Application Logs"
    )
    if not (rotate.get("info") or {}).get("last_run_at"):
        call("execute", "RunProcedure", {"procedure": "Rotate Application Logs"})
        deadline = time.monotonic() + 60
        while True:
            info = next(
                item for item in read("ListProcedures")
                if item.get("name") == "Rotate Application Logs"
            )["info"]
            if info.get("last_run_at") and info.get("state") != "Running":
                break
            if time.monotonic() > deadline:
                raise SystemExit("Rotate Application Logs did not finish within 60s")
            time.sleep(1)
        track("procedure run Rotate Application Logs", True)
    else:
        track("procedure run Rotate Application Logs", False)

    print(f"created: {len(created)}")
    for label in created:
        print(f"  + {label}")
    print(f"unchanged: {len(unchanged)}")
    for label in unchanged:
        print(f"  = {label}")


if __name__ == "__main__":
    main()
