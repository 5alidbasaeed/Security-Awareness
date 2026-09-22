# Builds Gophish from a pinned upstream tag, from source, rather than trusting an
# arbitrary third-party image — see CLAUDE.md invariant #9 and the plan doc's
# "Gophish Version & Security Management" section.
#
# OPEN DECISION (see CLAUDE.md / phishing-training-platform-plan.md "Overall
# Readiness Assessment", item 4): this pins upstream v0.12.1, which is the
# version affected by the unpatched HIGH-severity CVE GO-2026-4455 (admin API
# key exposed in rendered dashboard HTML). Network isolation of the admin/API
# port (see docker-compose.yml — gophish is never given a published host port,
# and nginx never proxies to its admin port) is the required mitigation that
# makes this an acceptable starting point. Revisit this pin before any real
# campaign: either a patched upstream release or a vetted fork, per the plan.
FROM golang:1.22-bookworm AS build

ARG GOPHISH_VERSION=v0.12.1
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone --depth 1 --branch ${GOPHISH_VERSION} https://github.com/gophish/gophish.git .
RUN go build -o /out/gophish .

FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates gettext-base \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/gophish
COPY --from=build /src/ .
COPY --from=build /out/gophish /opt/gophish/gophish
COPY config.json.example /opt/gophish/config.json.template
COPY entrypoint.sh /opt/gophish/entrypoint.sh
RUN chmod +x /opt/gophish/entrypoint.sh /opt/gophish/gophish

# Admin UI/API (3333) — internal network only, never published to the host.
# Phish listener (80/443, configured in config.json) — reachable via the
# public network, proxied by nginx.
EXPOSE 3333 80 443

ENTRYPOINT ["/opt/gophish/entrypoint.sh"]
