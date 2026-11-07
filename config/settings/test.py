"""Test execution settings."""
from .base import *  # noqa: F403

DEBUG = False
TESTING = True

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "fuel-route-test-cache",
    }
}

# Fast test timeouts
EXTERNAL_CONNECT_TIMEOUT_SEC = 0.5
EXTERNAL_READ_TIMEOUT_SEC = 1.0
