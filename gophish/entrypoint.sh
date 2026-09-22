#!/bin/sh
# Renders config.json from the template using env vars supplied by docker-compose,
# since Gophish itself doesn't support env-var interpolation in its config file.
set -eu

envsubst < /opt/gophish/config.json.template > /opt/gophish/config.json

exec ./gophish
