"""Registers the translator's API key with the bundled Attendee (idempotent).

Run by attendee_setup.sh through `manage.py shell`, inside Attendee's own image.
Attendee stores only a SHA-256 of each key (bots/models.py ApiKey), so the key
setup_secrets.py generated is registered by its hash - the same thing Attendee's
"API Keys" page does when someone creates one by hand. No user account is
needed: API requests authenticate by key alone (bots/authentication.py).
"""

import hashlib
from pathlib import Path

from accounts.models import Organization
from bots.models import ApiKey, Project

key = Path("/setup/attendee_api_key").read_text(encoding="utf-8").strip()
key_hash = hashlib.sha256(key.encode()).hexdigest()

if ApiKey.objects.filter(key_hash=key_hash, disabled_at__isnull=True).exists():
    print("attendee-setup: API key already registered")
else:
    organization, _ = Organization.objects.get_or_create(name="Interpreter")
    project, _ = Project.objects.get_or_create(name="Interpreter", organization=organization)
    ApiKey.objects.create(project=project, name="Interpreter translator", key_hash=key_hash)
    print("attendee-setup: API key registered")
