#!/usr/bin/env bash
# Wraps every command the bundled Attendee containers run (docker-compose.yml):
# loads the secrets deploy/setup_secrets.py generated, and makes the bot trust
# the translator's certificate - Attendee only streams audio to wss:// URLs.
set -euo pipefail
set -a
. /setup/attendee.env
set +a
# System roots plus our private CA - not the CA alone, so everything else the
# worker talks to over HTTPS keeps verifying exactly as before.
cat /etc/ssl/certs/ca-certificates.crt /setup/ca.pem > /tmp/interpreter-ca-bundle.pem
export SSL_CERT_FILE=/tmp/interpreter-ca-bundle.pem
exec "$@"
