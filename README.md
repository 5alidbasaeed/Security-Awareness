# Internal Phishing Simulation & Security Awareness Training Platform

Phase 0 (infra proof) scaffold. See [phishing-training-platform-plan.md](phishing-training-platform-plan.md) for the full architecture and [CLAUDE.md](CLAUDE.md) for the non-negotiable invariants this scaffold implements.

## Running the Phase 0 stack locally

```bash
cp .env.example .env
# edit .env — at minimum set real values for POSTGRES_PASSWORD, MYSQL_PASSWORD,
# MYSQL_ROOT_PASSWORD, DJANGO_SECRET_KEY

docker compose up -d --build
```

This brings up: Postgres (Django's DB), MySQL (Gophish's DB), Redis (Celery broker), Django (migrates on boot), Celery worker + beat, Gophish (builds from pinned source, see `gophish/Dockerfile`), and Nginx.

## Verifying the network isolation (Phase 0's actual exit criterion)

The whole point of Phase 0 is proving the topology, not just that containers boot:

```bash
# 1. Datastores publish no host ports.
docker compose ps
# postgres / mysql / redis should show no host-side port mapping in the PORTS column.

# 2. Django is reachable and healthy (local-dev-only: published on :8000 — see
#    docker-compose.yml header comment; a real deployment removes this and
#    reaches Django via VPN/SSH tunnel instead).
curl http://localhost:8000/healthz
# → {"healthy": true, "checks": {"database": "ok", "redis": "ok"}}

# 3. Gophish's phish listener is reachable through nginx (the public path).
curl -I http://localhost/

# 4. Gophish's ADMIN port is NOT reachable from the host/public side.
curl http://localhost:3333
# → should fail to connect (nothing is published on 3333)

# 5. ...but Django CAN reach it over the internal network, as it needs to for
#    the PhishingEngineClient adapter (Phase 1):
docker compose exec django curl -s -o /dev/null -w '%{http_code}\n' http://gophish:3333
# → 200 (or a redirect), proving internal reachability without public exposure
```

Tear down when done: `docker compose down`.

## What's NOT in this scaffold yet (see the plan doc's phases)

- **Sending-domain SPF/DKIM/DMARC + a real mail-security-gateway test.** Needs a real domain and real infra this repo can't provide — do this against the actual deployment host before Phase 0 is considered complete for real use.
- **VPN/SSH-tunnel access to the Django dashboard on a real host.** Also host infrastructure, not something Docker Compose alone can set up.
- **Gophish's actual admin setup**: on first boot Gophish generates an admin password (check its logs: `docker compose logs gophish`) and an API key. Copy the API key into `.env` (`GOPHISH_API_KEY`) once you have it — nothing consumes it yet in Phase 0.
- Django models, Gophish API integration, campaign/event/training logic, auth — all Phase 1+. The `apps/` directory currently holds empty app skeletons only.

## Open decisions carried from the plan doc

See `CLAUDE.md` → "Open decisions". Two are partially addressed by this scaffold:

- **Network topology**: implemented as described in the plan doc (`docker-compose.yml`).
- **Gophish version**: pinned to upstream `v0.12.1` (see comment header in `gophish/Dockerfile`) — this is the version affected by the unpatched GO-2026-4455 CVE; network isolation of the admin port is the mitigation, not a fix. Revisit upstream-vs-fork before any real campaign.

## Subagents & skills

`.claude/agents/` and `.claude/skills/` have project-scoped subagents and reference material for backend, frontend, database, architecture review, security review, debugging, and testing. See `CLAUDE.md` for the index.
