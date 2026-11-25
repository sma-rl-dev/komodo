# Komodo deployment (tester-env)

Komodo (moghtech/komodo) is a build/deploy control plane: a Rust Core server that
serves the React UI and talks to MongoDB, a Rust Periphery agent per managed host,
and the `km` CLI. This environment runs **Core + Periphery from editable source,
socket-free**: there is no `/var/run/docker.sock` mount, no `/proc` mount, no
privileged container and no dind.

## Why socket-free

Periphery's Docker API can prune images/volumes/networks/containers and run
`docker system prune -a -f --volumes` (and Core exposes that through the UI). On
this shared host that would destroy the protected `alevern/chromium-browser` and
`chromium-ls*` verification infrastructure. A privileged dind sidecar is rejected
by corpus precedent.

Socket-free is a designed state, not a broken one: Periphery logs
`Failed to connect to docker api. Docker monitoring won't work and will return
empty results.`, keeps retrying, and Core still marks the server `Ok` from system
stats. Resource CRUD, users/groups/permissions, procedures, actions, resource
syncs, alerts, settings and the server stats surface all work. Docker-backed
surfaces (containers, stacks, deployments, builds, container terminals, /stats
aggregates) are dead and out of scope for scenarios.

## Quick start

```bash
./tester-env deploy    # build image from source, start mongo+core+periphery, wait for admin login
./tester-env seed      # idempotent deterministic business data (deploy first)
./tester-env verify    # UI shell, admin login, Local server, then the full seeded state
./tester-env status    # compose/container state, URL, credentials
./tester-env logs      # follow all logs (or: logs core|periphery|mongo)
./tester-env stop      # stop containers, keep volumes
./tester-env reset     # compose down -v: containers + all RUN_ID-scoped volumes
```

Reset plus seed: `./tester-env reset && ./tester-env deploy && ./tester-env seed`.
`seed` (implemented in `scripts/tester-env-seed.py`) is create-if-absent and safe
to re-run: a second run reports all 21 records unchanged. `verify` runs
`scripts/tester-env-verify.py`, which asserts the seeded names, counts, flags and
relationships through the HTTP API. All commands accept `--run-id <id>` and
`--port <port>` (that is how `scripts/rl-env` invokes them) and honour
`IMAGE_TAG`, `RUN_ID`, `PORT` and `TESTER_ENV_BROWSER_ORIGIN`.

## Seeded data

Theme: **Meridian Pay**, a payments platform team managing its infrastructure.

| Surface | Seeded records |
| --- | --- |
| Tags (Settings > Tags) | `production` (Green), `staging` (Amber), `internal` (Slate) |
| Variables (Settings > Variables) | `ALERT_WEBHOOK_URL`, `BACKUP_RETENTION_DAYS`=30, `DEPLOY_TOKEN` (secret, masked) |
| Procedures | `Nightly Database Snapshot` (2 Sleep stages 2s+5s, cron `0 0 2 * * ?` UTC, next run visible), `Rotate Application Logs` (1 Sleep stage, state Ok with one completed run), `Quarterly Failover Drill` (2 Sleep stages, unscheduled) |
| Actions | `Collect Server Inventory`, `Check Backup Retention`, `Publish Release Notes` (never run: Deno would fetch npm deps) |
| Users | `dana.whitfield`, `marco.ruiz` (both enabled, not admin, password as the admin) |
| User groups | `Platform Operations` (Server Read, per-target Execute on Rotate Application Logs), `Release Managers` (Server Read, Procedure Execute, ResourceSync Read) |
| Alerters (Settings > Alerters) | `Platform Pager` (enabled, custom `http://localhost:7000/alerts/platform`), `Weekly Maintenance Digest` (disabled) |
| Resource syncs | `Infrastructure as Code` (files on host, `variables.toml`), pending Create diffs for `LOG_LEVEL` and `SNAPSHOT_BUCKET` |
| Repos | `platform-infrastructure` (`meridianpay/platform-infrastructure`, branch `main`, registered on Local, never cloned) |

The sync file lives in the `syncs` volume at
`/syncs/Infrastructure_as_Code/variables.toml`; `seed` writes it with
`docker exec` on the core container, so re-seeding after a reset recreates it.
The seed never clones, never runs an action and never reaches the network.
Failed/disabled states in the baseline (`Weekly Maintenance Digest` disabled, the
repo never pulled) are ordinary product states, not mutants.

## Tested ref

- Baseline: `v2.3.4` = `3e2c675d43ffd80f9d4eaab3da3887cc7dab5a05` (latest stable
  release, published 2026-10-22; `main` is the v2.3.3 commit one step behind).
- Deployment tooling lives in our fork `https://github.com/sma-rl-dev/komodo`,
  branch `tester-env-baseline`, one tooling commit on top of that baseline; no
  upstream source file is modified. Scenario worktrees are cloned from the fork
  and check out the baseline commit with this tooling overlaid.

## Runtime shape

| Service | Image | Port | Notes |
| --- | --- | --- | --- |
| core | `tester-env-komodo:dev` (local build) | host `PORT` -> 9120 | serves API + built UI from `/app/ui` |
| periphery | same image, `command: periphery` | internal 8120/9120 | pairs with Core over `ws://core:9120` |
| mongo | `mongo:8.0` | internal 27017 | fixed root creds, `--wiredTigerCacheSizeGB 0.25` |

Volumes (all scoped by the compose project `tester-env-komodo$RUN_ID`): mongo-data,
mongo-config, keys (core/periphery mutual-TLS keys), syncs, repo-cache, backups,
repos, stacks, builds.

Deterministic credentials: `admin` / `Tester-env-Password-123`
(`KOMODO_ADMIN_USERNAME` / `KOMODO_ADMIN_PASSWORD` to override). Created on the
first boot by `KOMODO_INIT_ADMIN_USERNAME/PASSWORD` + `KOMODO_LOCAL_AUTH=true`.
JWT and webhook secrets and the Mongo root password are fixed literals so a reset
reproduces the same stack. `KOMODO_DISABLE_INIT_RESOURCES=true` suppresses the
default tag and the 01:00/03:00/06:00 UTC scheduled procedures (Rotate Server
Keys would otherwise break the pairing mid-run).

## Build notes

One inline Dockerfile (in `docker-compose.tester-env.yml`, because
`scripts/rl-env` copies only `tester-env` + companion compose files into a
worktree):

- `node:22.12-alpine` builds `client/core/ts` (`yarn build && yarn link`) then
  `ui` (`yarn link komodo_client && yarn build`).
- `rust:1.98.0-trixie` builds `komodo_core`, `komodo_periphery` and `komodo_cli`
  in one shared workspace build with BuildKit cache mounts for
  `/usr/local/cargo/registry`, `/usr/local/cargo/git` and `/builder/target`;
  binaries are copied to `/out` and stripped so they leave the cache mount.
- `debian:trixie-slim` runtime: git/curl/ca-certificates/iproute2, `/app/ui`,
  binaries, Deno from `denoland/deno:bin` with `jsr:@std/{yaml,toml}` preloaded
  (`DENO_DIR=/action-cache/deno`). Starship and the Docker CLI packages are
  dropped.

Measured on this host: cold build 437 s for the cargo step (~9.5 min end to end
with the UI stage and the first container start); a source-only Rust change
rebuilt and redeployed in 158 s; a no-change deploy in 4.6 s (full layer cache);
cached build cache footprint ≈ 2.5 GB target + 0.45 GB registry. Image 480 MB.

## Verified evidence (2026-11-25, this host)

- `./tester-env deploy` cold: cargo step 436.9 s, first end-to-end start 701.5 s,
  image `tester-env-komodo:dev` f02bb66e33a6 (480 MB). No-change deploy 4.6 s;
  one-line Rust source edit rebuilt and redeployed in 158.5 s.
- Determinism: core logs `Creating init admin user...` then
  `Skipping initial resource creation`; reset -> deploy recreates the same admin
  and the same `Local` periphery pairing (periphery logs
  `Logged in to Komodo Core ... as Server Local`).
- `./tester-env verify` passes after deploy (UI shell, admin login, `/user`,
  `Local` Ok with system stats, no default init procedures, socket-free
  periphery log line) and after seed (every seeded name/count/flag/relationship
  plus a non-admin login).
- Seed determinism: reset -> deploy -> seed -> verify run twice, both cycles
  created the 21 records and verified, and a state fingerprint over
  names/colors/values/flags/stage shapes/schedules/relationships (ids and
  timestamps excluded) was identical (`sha256 0939d9f96f59b307`). Re-running
  `seed` on a seeded stack creates 0 and reports 21 unchanged.
- Browser proof (browser-checker 2026-11-25 at
  `http://host.docker.internal:19120/`): login as `admin`; Dashboard/Servers
  `Local` OK; Procedures show `Nightly Database Snapshot` (2 named stages, next
  run), `Rotate Application Logs` (state Ok with a completed run) and
  `Quarterly Failover Drill`; 3 actions; repo `platform-infrastructure`; resource
  sync `Infrastructure as Code` pending with Create Variable diffs
  `LOG_LEVEL`/`SNAPSHOT_BUCKET`; 3 tags; 3 variables with `DEPLOY_TOKEN` masked
  and `BACKUP_RETENTION_DAYS`=30; 2 alerters (Enabled/Disabled); 3 users; 2 user
  groups; the browser-smoke procedure is absent. All PASS.
- `./tester-env stop` removes the three containers and the compose network and
  keeps the RUN_ID-scoped volumes (mongo data, keys, syncs, repo cache,
  periphery repos/stacks/builds), so a following `deploy` resumes the same
  state. `./tester-env reset` additionally drops those volumes.

## Caveats

- **SPA deep links 404 on hard refresh.** Core serves `/` and static assets only;
  `/login`, `/servers`, `/settings` return 404 on a direct GET (React
  BrowserRouter, no server-side fallback). Scenarios must start at `/` and
  navigate by clicking; never navigate the browser to a path URL.
- `/version` reports `0.0.0`: the build skips upstream's `cargo set-version`.
- Periphery resolves the host public IP with a 1 s OpenDNS lookup
  (`myip.opendns.com`); it is best-effort and silent when offline.
- Periphery terminals are enabled and give a shell **inside the periphery
  container** (no host mounts beyond its named volumes). Scenarios should not
  need them; they are not a host escape, but they are also not part of the
  oracle surface.
- Resource ids are ObjectIds/ULIDs; assert names, counts and order, never ids or
  timestamps. Monitoring is 15 s, resource polling 1 h, stats/alerts kept 14 days.
- Scenario-relevant async oracles (procedures, actions, resource syncs) are
  timing-based: poll until the final state.
- Reset requires `reset` then `deploy`; both are in `./tester-env`. Reset keeps
  the image (content-addressed tags are shared); use
  `scripts/rl-env gc --prune-images` to reclaim images.
