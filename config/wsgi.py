"""WSGI config for fuel-route-optimizer project."""
import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
application = get_wsgi_application()

try:
    from apps.stations.spatial import StationSpatialIndex
    StationSpatialIndex.get_instance().ensure_initialized()
except Exception:
    pass
