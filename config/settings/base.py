"""Base Django settings for fuel-route-optimizer project."""
import os
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-fuel-route-optimizer-development-key-replace-in-production-37cec3b1",
)

DEBUG = False

ALLOWED_HOSTS: list[str] = [
    host.strip()
    for host in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]").split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third party apps
    "rest_framework",
    "drf_spectacular",
    # Project apps
    "apps.common.apps.CommonConfig",
    "apps.stations.apps.StationsConfig",
    "apps.routing.apps.RoutingConfig",
    "apps.optimizer.apps.OptimizerConfig",
    "apps.api.apps.ApiConfig",
    "apps.map_viewer.apps.MapViewerConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Database configuration: SQLite default or PostgreSQL via DATABASE_URL
database_url = os.environ.get("DATABASE_URL", "").strip()
if database_url:
    parsed_db = urlparse(database_url)
    engine = "django.db.backends.postgresql" if parsed_db.scheme.startswith("postgres") else "django.db.backends.sqlite3"
    DATABASES = {
        "default": {
            "ENGINE": engine,
            "NAME": parsed_db.path.lstrip("/"),
            "USER": parsed_db.username or "",
            "PASSWORD": parsed_db.password or "",
            "HOST": parsed_db.hostname or "",
            "PORT": parsed_db.port or "",
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Cache Configuration: In-memory LRU or Redis via REDIS_URL
redis_url = os.environ.get("REDIS_URL", "").strip()
if redis_url:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": redis_url,
            "TIMEOUT": 3600,
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "fuel-route-optimizer-cache",
            "TIMEOUT": 3600,
        }
    }

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.FormParser",
        "rest_framework.parsers.MultiPartParser",
    ],
    "EXCEPTION_HANDLER": "apps.api.exceptions.custom_exception_handler",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Fuel Route Optimizer API",
    "DESCRIPTION": (
        "High-performance API for computing optimal driving routes and cost-effective fuel stops "
        "across the United States, utilizing OPIS retail diesel prices and a 500-mile range vehicle."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
}

# Routing & Geocoding Upstream Providers Configuration
OSRM_ROUTE_URL = os.environ.get(
    "OSRM_ROUTE_URL", "https://router.project-osrm.org/route/v1/driving"
)
VALHALLA_ROUTE_URL = os.environ.get(
    "VALHALLA_ROUTE_URL", "https://valhalla1.openstreetmap.de/route"
)
ORS_ROUTE_URL = os.environ.get(
    "ORS_ROUTE_URL", "https://api.openrouteservice.org/v2/directions/driving-car"
)
ORS_API_KEY = os.environ.get("ORS_API_KEY", "")

# Upstream socket timeouts (seconds)
EXTERNAL_CONNECT_TIMEOUT_SEC = float(os.environ.get("EXTERNAL_CONNECT_TIMEOUT_SEC", "2.0"))
EXTERNAL_READ_TIMEOUT_SEC = float(os.environ.get("EXTERNAL_READ_TIMEOUT_SEC", "4.0"))

# Feature Flags (Defaults)
FEATURE_FLAGS = {
    "CANARY_ROUTING": False,
    "EXPERIMENTAL_SOLVER": False,
    "ALLOW_EPHEMERAL_TEARDOWN": True,
    "ENABLE_SRE_TELEMETRY": True,
}

