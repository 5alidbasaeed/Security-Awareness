#!/bin/sh
# Renders config.json from the template using env vars supplied by docker-compose,
# since Gophish itself doesn't support env-var interpolation in its config file.
set -eu

# envsubst silently renders an unset variable as "", which would leave the admin
# listener's address to Gophish's defaults. Fail loudly instead.
: "${GOPHISH_ADMIN_LISTEN:?must be set (docker-compose.yml binds the admin API to the internal network only)}"

envsubst < /opt/gophish/config.json.template > /opt/gophish/config.json

exec ./gophish
