"""Local development settings."""
from .base import *  # noqa: F403

DEBUG = True

ALLOWED_HOSTS = ["*"]

# CORS / CSRF lax settings for local frontend & Postman testing
CSRF_TRUSTED_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]
