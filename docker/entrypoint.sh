#!/usr/bin/env bash
# Container entrypoint: waits for DB, synchronizes multi-replica migrations/seeding via advisory lock, warms cKDTree, and starts Gunicorn
set -e

echo "=== Synchronizing database migrations & initial dataset seeding ==="
python -c "
import time
import django
django.setup()
from django.db import connection
from django.core.management import call_command
from apps.stations.models import FuelStation
from apps.routing.models import USPlaceGazetteer

# 1. Wait up to 60s for database readiness (e.g. Kubernetes Postgres StatefulSet initialization)
for attempt in range(30):
    try:
        connection.ensure_connection()
        break
    except Exception as exc:
        if attempt == 29:
            raise
        print(f'Waiting for database connection ({attempt + 1}/30): {exc}')
        time.sleep(2)

# 2. Acquire PostgreSQL advisory lock if using Postgres so multiple Kubernetes replicas do not race during migrate/seed
locked = False
try:
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            print('Acquiring PostgreSQL startup advisory lock (7768837)...')
            cursor.execute('SELECT pg_advisory_lock(7768837);')
            locked = True

    print('Running database migrations...')
    call_command('migrate', interactive=False)

    if FuelStation.objects.filter(is_active_us=True).count() < 6000:
        print('Seeding US fuel stations (6,626 active stations)...')
        call_command('load_fuel_stations')
    else:
        print('US fuel stations already seeded.')

    if USPlaceGazetteer.objects.count() < 1000:
        print('Seeding US Gazetteer (65,920 places)...')
        call_command('load_us_gazetteer')
    else:
        print('US Gazetteer already seeded.')
finally:
    if locked:
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock(7768837);')
        except Exception:
            pass
"

echo "=== Warming 3D cKDTree spatial index ==="
python -c "
import django; django.setup()
from apps.stations.spatial import StationSpatialIndex
StationSpatialIndex.get_instance().ensure_initialized()
print(f'Spatial index warmed ({StationSpatialIndex.get_instance().station_count} stations).')
"

echo "=== Starting Gunicorn WSGI Server ==="
exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 4 \
    --threads 2 \
    --timeout 60 \
    --access-logfile - \
    --error-logfile - \
    --preload
