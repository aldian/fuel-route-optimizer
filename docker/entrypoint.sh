#!/usr/bin/env bash
# Container entrypoint: runs migrations, seeds database if empty, and starts Gunicorn
set -e

echo "=== Running database migrations ==="
python manage.py migrate --noinput

echo "=== Checking fuel station data ==="
python -c "
import django; django.setup()
from apps.stations.models import FuelStation
if FuelStation.objects.filter(is_active_us=True).count() < 6000:
    from django.core.management import call_command
    print('Seeding US fuel stations...')
    call_command('load_fuel_stations')
"

echo "=== Checking US gazetteer data ==="
python -c "
import django; django.setup()
from apps.routing.models import USPlaceGazetteer
if USPlaceGazetteer.objects.count() < 1000:
    from django.core.management import call_command
    print('Seeding US Gazetteer...')
    call_command('load_us_gazetteer')
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
