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
# postgres / mysql / redis / gophish / django / celery-* should show no
# host-side port mapping in the PORTS column — only nginx does (80).

# 2. Django is reachable and healthy, checked from inside the internal
#    network (nothing on that network is published to the host — see
#    docker-compose.yml header comment; a real deployment reaches Django via
#    VPN/SSH tunnel the same way).
docker compose exec django curl -s http://localhost:8000/healthz
# → {"healthy": true, "checks": {"database": "ok", "redis": "ok"}}

# 3. Gophish's phish listener is reachable through nginx (the public path).
curl -I http://localhost/
# → any HTTP response (404 is expected with no campaign/landing-page
#   configured yet) proves the public proxy path works.

# 4. Gophish's ADMIN port is NOT reachable from the host/public side.
curl -m 3 http://localhost:3333
# → connection fails/refuses (nothing is published on 3333)

# 5. ...but Django CAN reach it over the internal network, as it needs to for
#    the PhishingEngineClient adapter (Phase 1):
docker compose exec django curl -s -o /dev/null -w '%{http_code}\n' http://gophish:3333
# → a response code (redirect to /login is typical), proving internal
#   reachability without public exposure
```

All five checks above were run and passed against this exact scaffold. Tear down when done: `docker compose down`.

**Known fix baked in**: `mysql` runs with `--sql-mode=NO_ENGINE_SUBSTITUTION` — Gophish's oldest DB migrations insert zero-dates that MySQL 8's default strict mode rejects outright, so it won't boot against MySQL 8.4 without this. Another concrete data point for the upstream-vs-fork decision below.

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
