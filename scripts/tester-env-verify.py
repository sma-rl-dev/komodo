#!/usr/bin/env python3
"""Verify the deterministic Komodo seed through the HTTP API.

Asserts the seeded names, counts, flags and relationships only. Ids and
timestamps are server-generated, so they are never asserted.

Usage (normally invoked by ./tester-env verify):
    TESTER_ENV_LOCAL_URL=... KOMODO_JWT=... python3 scripts/tester-env-verify.py
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

BASE_URL = os.environ.get("TESTER_ENV_LOCAL_URL", "http://127.0.0.1:19120").rstrip("/")
JWT = os.environ.get("KOMODO_JWT", "")
PASSWORD = os.environ.get("TESTER_ENV_SEED_PASSWORD", "Tester-env-Password-123")

SYNC_NAME = "Infrastructure as Code"
SYNC_VARIABLES = {"LOG_LEVEL", "SNAPSHOT_BUCKET"}
REPO_NAME = "platform-infrastructure"
REPO_URL = "meridianpay/platform-infrastructure"

EXPECTED_TAGS = {"production": "Green", "staging": "Amber", "internal": "Slate"}
EXPECTED_VARIABLES = {
    "ALERT_WEBHOOK_URL": False,
    "BACKUP_RETENTION_DAYS": False,
    "DEPLOY_TOKEN": True,
}
EXPECTED_PROCEDURES = {
    "Nightly Database Snapshot": {"stages": 2, "scheduled": True},
    "Rotate Application Logs": {"stages": 1, "scheduled": False, "run": True},
    "Quarterly Failover Drill": {"stages": 2, "scheduled": False},
}
EXPECTED_ACTIONS = {"Collect Server Inventory", "Check Backup Retention", "Publish Release Notes"}
EXPECTED_USERS = {"admin", "dana.whitfield", "marco.ruiz"}
EXPECTED_GROUPS = {
    "Platform Operations": ("dana.whitfield", {"Server": "Read"}),
    "Release Managers": ("marco.ruiz", {"Server": "Read", "Procedure": "Execute",
                                        "ResourceSync": "Read"}),
}
EXPECTED_ALERTERS = {"Platform Pager": True, "Weekly Maintenance Digest": False}
DEFAULT_INIT_PROCEDURES = {"Backup Core Database", "Global Auto Update", "Rotate Server Keys"}
EXPECTED_TAGS_ON = {
    ("Procedure", "Nightly Database Snapshot"): {"production"},
    ("Action", "Collect Server Inventory"): {"internal"},
    ("ResourceSync", SYNC_NAME): {"production"},
    ("Repo", REPO_NAME): {"production"},
}


def call(kind: str, method: str, params: dict, token: str = ""):
    request = urllib.request.Request(
        f"{BASE_URL}/{kind}/{method}",
        data=json.dumps(params).encode(),
        headers={
            "content-type": "application/json",
            "authorization": token or JWT,
        },
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


def id_of(item: dict) -> str:
    value = item.get("id") or item.get("_id")
    if isinstance(value, dict):
        value = value.get("$oid")
    return str(value)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"verify seed FAILED: {message}")


def permission_level(value) -> str:
    return value if isinstance(value, str) else (value or {}).get("level", "None")


def login(username: str, password: str) -> str:
    request = urllib.request.Request(
        f"{BASE_URL}/auth/login/LoginLocalUser",
        data=json.dumps({"username": username, "password": password}).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = json.loads(response.read())
    return (body.get("data") or {}).get("jwt") or body.get("jwt", "")


def main() -> None:
    if not JWT:
        raise SystemExit("KOMODO_JWT is required")

    tags = {tag["name"]: tag for tag in read("ListTags")}
    check(set(tags) == set(EXPECTED_TAGS),
          f"tags are {sorted(tags)}, expected {sorted(EXPECTED_TAGS)}")
    for name, color in EXPECTED_TAGS.items():
        check(tags[name].get("color") == color, f"tag {name} color is not {color}")

    variables = {variable["name"]: variable for variable in read("ListVariables")}
    check(set(variables) == set(EXPECTED_VARIABLES),
          f"variables are {sorted(variables)}, expected {sorted(EXPECTED_VARIABLES)}")
    for name, is_secret in EXPECTED_VARIABLES.items():
        check(bool(variables[name].get("is_secret")) == is_secret,
              f"variable {name} is_secret mismatch")
        check(bool(variables[name].get("description")), f"variable {name} has no description")
    check(variables["BACKUP_RETENTION_DAYS"]["value"] == "30",
          "BACKUP_RETENTION_DAYS value is not 30")
    check(variables["ALERT_WEBHOOK_URL"]["value"] == "http://localhost:7000/alerts/platform",
          "ALERT_WEBHOOK_URL value mismatch")

    procedures = {procedure["name"]: procedure for procedure in read("ListProcedures")}
    check("browser-smoke-procedure" not in procedures,
          "leftover browser-smoke-procedure is present")
    check(not (set(procedures) & DEFAULT_INIT_PROCEDURES),
          "default init procedures were created despite KOMODO_DISABLE_INIT_RESOURCES")
    check(set(procedures) == set(EXPECTED_PROCEDURES),
          f"procedures are {sorted(procedures)}, expected {sorted(EXPECTED_PROCEDURES)}")
    for name, expected in EXPECTED_PROCEDURES.items():
        info = procedures[name].get("info") or {}
        check(info.get("stages") == expected["stages"],
              f"procedure {name} has {info.get('stages')} stage(s)")
        check(bool(info.get("next_scheduled_run")) == expected["scheduled"],
              f"procedure {name} schedule mismatch")
        if expected.get("run"):
            check(info.get("state") == "Ok" and bool(info.get("last_run_at")),
                  f"procedure {name} has no successful run history")
        check(bool(procedures[name].get("tags")), f"procedure {name} has no tag")

    actions = {action["name"]: action for action in read("ListFullActions")}
    check(set(actions) == EXPECTED_ACTIONS,
          f"actions are {sorted(actions)}, expected {sorted(EXPECTED_ACTIONS)}")
    for name in EXPECTED_ACTIONS:
        config = actions[name].get("config") or {}
        check(bool(config.get("file_contents")), f"action {name} has no script")
        check(config.get("run_at_startup") is False, f"action {name} runs at startup")

    full_procedures = {procedure["name"]: procedure
                       for procedure in read("ListFullProcedures")}
    snapshot_stages = (full_procedures["Nightly Database Snapshot"].get("config") or {})["stages"]
    snapshot_sleeps = [
        execution["execution"]["params"].get("duration_ms")
        for stage in snapshot_stages for execution in stage.get("executions") or []
        if execution["execution"].get("type") == "Sleep"
    ]
    check(snapshot_sleeps == [2000, 5000],
          f"Nightly Database Snapshot sleep stages are {snapshot_sleeps}, expected [2000, 5000]")
    rotate_schedule = (full_procedures["Nightly Database Snapshot"].get("config") or {})
    check(rotate_schedule.get("schedule") == "0 0 2 * * ?",
          "Nightly Database Snapshot cron schedule mismatch")
    check(rotate_schedule.get("schedule_timezone") == "UTC",
          "Nightly Database Snapshot schedule timezone is not UTC")

    users = {user["username"]: user for user in read("ListUsers")}
    check(set(users) == EXPECTED_USERS,
          f"users are {sorted(users)}, expected {sorted(EXPECTED_USERS)}")
    for username in EXPECTED_USERS - {"admin"}:
        check(users[username].get("enabled") is True, f"user {username} is not enabled")
        check(users[username].get("admin") is False, f"user {username} should not be admin")

    groups = {group["name"]: group for group in read("ListUserGroups")}
    check(set(groups) == set(EXPECTED_GROUPS),
          f"user groups are {sorted(groups)}, expected {sorted(EXPECTED_GROUPS)}")
    for name, (member, permissions) in EXPECTED_GROUPS.items():
        group = groups[name]
        check(id_of(users[member]) in (group.get("users") or []),
              f"user {member} is not in group {name}")
        levels = {key: permission_level(value) for key, value in (group.get("all") or {}).items()}
        for resource_type, level in permissions.items():
            check(levels.get(resource_type) == level,
                  f"group {name} {resource_type} permission is {levels.get(resource_type)}")

    # The per-target grant on the procedure is the deeper permission surface.
    rotate_id = id_of(procedures["Rotate Application Logs"])
    platforms_id = id_of(groups["Platform Operations"])
    group_permissions = call("read", "ListUserTargetPermissions", {
        "user_target": {"type": "UserGroup", "id": platforms_id},
    })
    target_grant = [
        permission for permission in group_permissions
        if (permission.get("resource_target") or {}).get("id") == rotate_id
    ]
    check(len(target_grant) == 1,
          "Platform Operations has no per-target permission on Rotate Application Logs")
    check(permission_level(target_grant[0].get("level")) == "Execute",
          "per-target permission on Rotate Application Logs is not Execute")

    alerters = {alerter["name"]: alerter for alerter in read("ListAlerters")}
    check(set(alerters) == set(EXPECTED_ALERTERS),
          f"alerters are {sorted(alerters)}, expected {sorted(EXPECTED_ALERTERS)}")
    for name, enabled in EXPECTED_ALERTERS.items():
        info = alerters[name].get("info") or {}
        check(bool(info.get("enabled")) == enabled, f"alerter {name} enabled mismatch")

    syncs = {sync["name"]: sync for sync in read("ListResourceSyncs")}
    check(set(syncs) == {SYNC_NAME}, f"resource syncs are {sorted(syncs)}")
    sync_info = call("read", "GetResourceSync", {"sync": SYNC_NAME}).get("info") or {}
    check(not sync_info.get("pending_error"),
          f"sync pending error: {sync_info.get('pending_error')}")
    pending = {update["data"]["name"] for update in sync_info.get("variable_updates") or []
               if update.get("type") == "Create"}
    check(pending == SYNC_VARIABLES,
          f"sync pending variable creates are {sorted(pending)}, expected {sorted(SYNC_VARIABLES)}")

    repos = {repo["name"]: repo for repo in read("ListRepos")}
    check(set(repos) == {REPO_NAME}, f"repos are {sorted(repos)}")
    check((repos[REPO_NAME].get("info") or {}).get("repo") == REPO_URL,
          "repo URL mismatch")

    list_methods = {
        "Procedure": "ListFullProcedures",
        "Action": "ListFullActions",
        "ResourceSync": "ListFullResourceSyncs",
        "Repo": "ListFullRepos",
    }
    tag_names = {id_of(tag): name for name, tag in tags.items()}
    for (resource_type, resource_name), expected_tags in EXPECTED_TAGS_ON.items():
        item = next(item for item in read(list_methods[resource_type])
                    if item.get("name") == resource_name)
        check(bool(item.get("description")),
              f"{resource_type} {resource_name} has no description")
        attached = {tag_names.get(tag) or tag for tag in item.get("tags") or []}
        check(attached == expected_tags,
              f"{resource_type} {resource_name} tags are {sorted(attached)}")

    # A seeded non-admin can authenticate and read servers through their group.
    user_jwt = login("dana.whitfield", PASSWORD)
    check(bool(user_jwt), "dana.whitfield could not log in")
    request = urllib.request.Request(f"{BASE_URL}/user",
                                     headers={"authorization": user_jwt})
    with urllib.request.urlopen(request, timeout=30) as response:
        me = json.loads(response.read())
    check(me.get("username") == "dana.whitfield", "seeded user session is wrong")
    servers = call("read", "ListServers", {}, token=user_jwt)
    check(any(server.get("name") == "Local" for server in servers),
          "seeded user cannot read the Local server")

    print("verify seed OK: "
          f"{len(tags)} tags, {len(variables)} variables, {len(procedures)} procedures, "
          f"{len(actions)} actions, {len(users)} users, {len(groups)} user groups, "
          f"{len(alerters)} alerters, {len(syncs)} resource sync, {len(repos)} repo")


if __name__ == "__main__":
    main()
