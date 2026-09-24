#!/usr/bin/env bash
# One-shot, before Attendee's app/worker/scheduler start (docker-compose.yml's
# attendee-setup): creates or updates Attendee's database tables, then registers
# the translator's API key. Safe to run on every start. Runs through
# attendee_run.sh, which loads the secrets Django needs.
set -euo pipefail
cd /attendee
python manage.py migrate --noinput
python manage.py shell -c "exec(open('/setup/attendee_provision.py').read())"
