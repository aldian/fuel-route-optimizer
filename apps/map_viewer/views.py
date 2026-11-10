"""Interactive Leaflet + OpenStreetMap Route & Fuel Stops Map View."""
import html as html_lib
import json
from typing import Any
from django.http import HttpRequest, HttpResponse
from django.views import View
from apps.optimizer.domain import VehicleSpec
from apps.optimizer.service import RouteOptimizationService
from apps.routing.call_budget import ExternalCallBudget


class RouteMapView(View):
    """Renders a self-contained interactive route map with high-contrast WCAG styling."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.service = RouteOptimizationService()

    def get(self, request: HttpRequest) -> HttpResponse:
        start_q = request.GET.get("start", "New York, NY")
        finish_q = request.GET.get("finish", "Los Angeles, CA")
        try:
            range_miles = max(50.0, min(2000.0, float(request.GET.get("vehicle_range_miles", 500.0))))
        except (TypeError, ValueError):
            range_miles = 500.0
        try:
            mpg = max(1.0, min(100.0, float(request.GET.get("mpg", 10.0))))
        except (TypeError, ValueError):
            mpg = 10.0

        tank_capacity = range_miles / mpg
        init_fuel_param = request.GET.get("initial_fuel_gallons")
        try:
            init_fuel = float(init_fuel_param) if init_fuel_param not in (None, "") else tank_capacity
            init_fuel = max(0.0, min(tank_capacity, init_fuel))
        except (TypeError, ValueError):
            init_fuel = tank_capacity

        try:
            corridor_miles = max(1.0, min(50.0, float(request.GET.get("corridor_miles", 5.0))))
        except (TypeError, ValueError):
            corridor_miles = 5.0

        spec = VehicleSpec(
            max_range_miles=range_miles,
            mpg=mpg,
            initial_fuel_gallons=init_fuel,
            corridor_miles=corridor_miles,
        )

        budget = ExternalCallBudget(max_calls=3)
        result = self.service.optimize_route(
            start_input=start_q,
            finish_input=finish_q,
            vehicle_spec=spec,
            budget=budget,
        )

        plan = result.plan
        route = result.route
        origin = result.origin
        dest = result.destination

        # Prepare Map Coordinates for Leaflet (expects [lat, lon])
        coords = route.coordinates
        if len(coords) > 1500:
            step = len(coords) // 1200 + 1
            coords = coords[::step]
            if coords[-1] != route.coordinates[-1]:
                coords.append(route.coordinates[-1])
        leaflet_coords = [[round(pt[0], 5), round(pt[1], 5)] for pt in coords]

        # Fuel stops data for markers, cards, and interactive SVG price/tank chart
        stops_json = [
            {
                "stop_number": s.stop_number,
                "name": s.station.truckstop_name,
                "address": s.station.address,
                "city": s.station.city,
                "state": s.station.state,
                "lat": s.station.latitude,
                "lon": s.station.longitude,
                "price": f"{s.retail_price_per_gallon_usd:.3f}",
                "price_num": float(s.retail_price_per_gallon_usd),
                "gallons": f"{s.gallons_purchased:.2f}",
                "gallons_num": float(s.gallons_purchased),
                "cost": f"{s.cost_usd:.2f}",
                "mile": f"{s.route_mile_marker:.1f}",
                "mile_num": round(float(s.route_mile_marker), 1),
                "tank_before": f"{s.tank_gallons_before:.1f}",
                "tank_after": f"{s.tank_gallons_after:.1f}",
                "tank_after_num": round(float(s.tank_gallons_after), 1),
            }
            for s in plan.stops
        ]

        cheapest_json = None
        if plan.recommended_cheapest_station is not None:
            cs = plan.recommended_cheapest_station
            cheapest_json = {
                "name": cs.station.truckstop_name,
                "address": cs.station.address,
                "city": cs.station.city,
                "state": cs.station.state,
                "lat": cs.station.latitude,
                "lon": cs.station.longitude,
                "price": f"{cs.retail_price:.3f}",
                "mile": f"{cs.route_mile_marker:.1f}",
                "off_route": f"{cs.off_route_distance_miles:.2f}",
            }

        esc_start = html_lib.escape(origin.resolved_name)
        esc_dest = html_lib.escape(dest.resolved_name)
        esc_start_input = html_lib.escape(start_q)
        esc_finish_input = html_lib.escape(finish_q)

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="referrer" content="strict-origin-when-cross-origin">
  <title>Fuel Route Optimizer | {esc_start} to {esc_dest}</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin=""/>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
  <style>
    :root {{
      --primary: #0F4C81;
      --primary-dark: #0A3254;
      --accent: #C84326;
      --success: #047857;
      --bg: #F8FAFC;
      --card-bg: #FFFFFF;
      --text: #000000;
      --text-muted: #0F172A;
      --border: #94A3B8;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      display: flex;
      flex-direction: column;
      height: 100vh;
      overflow: hidden;
    }}
    header {{
      background: #FFFFFF;
      border-bottom: 2px solid var(--border);
      padding: 10px 20px;
      display: flex;
      flex-direction: column;
      gap: 8px;
      z-index: 1000;
    }}
    .header-top {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 12px;
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 10px;
      color: #000000;
      font-weight: 800;
      font-size: 1.1rem;
    }}
    .brand svg {{
      width: 30px;
      height: 30px;
      flex-shrink: 0;
    }}
    .route-title {{
      font-size: 0.92rem;
      font-weight: 700;
      color: #000000;
      background: #E2E8F0;
      padding: 4px 10px;
      border-radius: 6px;
    }}
    .planner-bar {{
      display: flex;
      flex-wrap: wrap;
      align-items: flex-end;
      gap: 10px;
      padding-top: 4px;
    }}
    .form-field {{
      display: flex;
      flex-direction: column;
      gap: 3px;
    }}
    .form-field label {{
      font-size: 0.75rem;
      font-weight: 700;
      color: #000000;
    }}
    .form-field input {{
      font: inherit;
      font-size: 0.88rem;
      color: #000000;
      background: #FFFFFF;
      border: 1.5px solid #475569;
      border-radius: 6px;
      padding: 6px 10px;
      min-height: 36px;
    }}
    .form-field input.location-input {{
      width: 185px;
    }}
    .form-field input.num-input {{
      width: 92px;
    }}
    .form-field input:focus-visible,
    .btn-primary:focus-visible,
    .corridor-chip:focus-visible {{
      outline: 3px solid var(--primary);
      outline-offset: 2px;
    }}
    .btn-primary {{
      font: inherit;
      font-size: 0.88rem;
      font-weight: 700;
      color: #FFFFFF;
      background: var(--primary);
      border: 1.5px solid var(--primary-dark);
      border-radius: 6px;
      padding: 6px 16px;
      min-height: 36px;
      cursor: pointer;
      transition: background 0.15s ease;
    }}
    .btn-primary:hover {{
      background: var(--primary-dark);
    }}
    .corridors-row {{
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 6px;
      padding-top: 2px;
    }}
    .corridors-label {{
      font-size: 0.76rem;
      font-weight: 700;
      color: #000000;
      margin-right: 4px;
    }}
    .corridor-chip {{
      font: inherit;
      font-size: 0.76rem;
      font-weight: 700;
      color: #000000;
      background: #F1F5F9;
      border: 1px solid #64748B;
      border-radius: 999px;
      padding: 3px 10px;
      cursor: pointer;
      transition: background 0.12s ease, border-color 0.12s ease;
    }}
    .corridor-chip:hover {{
      background: #E2E8F0;
      border-color: #000000;
    }}
    .layout {{
      display: flex;
      flex: 1;
      min-height: 0;
    }}
    #map {{
      flex: 1;
      height: 100%;
      background: #E2E8F0;
    }}
    .sidebar {{
      width: 430px;
      background: #FFFFFF;
      border-left: 2px solid var(--border);
      display: flex;
      flex-direction: column;
      overflow-y: auto;
      padding: 16px;
      gap: 14px;
    }}
    .summary-card {{
      background: #F1F5F9;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px;
    }}
    .summary-card h2 {{
      font-size: 0.96rem;
      font-weight: 800;
      color: #000000;
      margin-bottom: 10px;
      border-bottom: 1px solid var(--border);
      padding-bottom: 6px;
    }}
    .metric-row {{
      display: flex;
      justify-content: space-between;
      margin-bottom: 6px;
      font-size: 0.86rem;
      color: #000000;
    }}
    .metric-value {{
      font-weight: 700;
      color: #000000;
    }}
    .metric-highlight {{
      font-size: 1.12rem;
      color: var(--primary);
      font-weight: 800;
    }}
    .chart-card {{
      background: #FFFFFF;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px;
    }}
    .chart-card h3 {{
      font-size: 0.88rem;
      font-weight: 800;
      color: #000000;
      margin-bottom: 4px;
    }}
    .chart-subtitle {{
      font-size: 0.76rem;
      color: #0F172A;
      margin-bottom: 8px;
    }}
    .cheapest-card {{
      background: #ECFDF5;
      border: 1.5px solid #059669;
      border-radius: 8px;
      padding: 12px;
      cursor: pointer;
    }}
    .cheapest-card-title {{
      font-size: 0.78rem;
      font-weight: 800;
      color: #000000;
      text-transform: uppercase;
      letter-spacing: 0.03em;
      margin-bottom: 4px;
    }}
    .cheapest-card-body {{
      font-size: 0.85rem;
      color: #000000;
      font-weight: 600;
    }}
    .stops-section h3 {{
      font-size: 0.92rem;
      font-weight: 800;
      color: #000000;
      margin: 4px 0 8px 0;
    }}
    .stop-card {{
      background: #FFFFFF;
      border: 1px solid var(--border);
      border-left: 4px solid var(--primary);
      border-radius: 6px;
      padding: 12px;
      margin-bottom: 10px;
      cursor: pointer;
      transition: transform 0.1s ease, box-shadow 0.1s ease;
    }}
    .stop-card:hover {{
      transform: translateY(-2px);
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.12);
      border-left-color: var(--accent);
    }}
    .stop-card-header {{
      display: flex;
      justify-content: space-between;
      font-weight: 800;
      font-size: 0.84rem;
      color: #000000;
      margin-bottom: 4px;
    }}
    .stop-name {{
      font-size: 0.88rem;
      font-weight: 700;
      color: #000000;
      margin-bottom: 3px;
    }}
    .stop-address {{
      font-size: 0.79rem;
      color: #0F172A;
      margin-bottom: 6px;
    }}
    .stop-details {{
      display: flex;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 4px;
      font-size: 0.8rem;
      color: #000000;
      background: #F8FAFC;
      padding: 6px;
      border-radius: 4px;
    }}
    .empty-stops-notice {{
      background: #EFF6FF;
      border: 1px solid #93C5FD;
      padding: 12px;
      border-radius: 6px;
      font-size: 0.86rem;
      color: #000000;
      line-height: 1.4;
    }}
    .leaflet-popup-content {{
      font-family: inherit;
      color: #000000;
      font-size: 0.88rem;
      line-height: 1.4;
      padding: 4px;
    }}
    .leaflet-popup-content b {{
      color: #000000;
      font-size: 0.92rem;
    }}
    @media (max-width: 900px) {{
      .layout {{ flex-direction: column; }}
      .sidebar {{ width: 100%; height: 45%; }}
      #map {{ height: 55%; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="header-top">
      <div class="brand">
        <svg viewBox="0 0 32 32" aria-hidden="true">
          <circle cx="16" cy="16" r="14" fill="#0F4C81"/>
          <path d="M16 7L22 23H18.8L17.5 19.2H14.5L13.2 23H10L16 7ZM15.2 16.5H16.8L16 13.8L15.2 16.5Z" fill="#FFFFFF"/>
        </svg>
        <span>Fuel Route Optimizer</span>
      </div>
      <div class="route-title">
        {esc_start} &rarr; {esc_dest} ({route.distance_miles:.1f} miles)
      </div>
    </div>

    <search aria-label="Route and vehicle configuration">
      <form id="route-planner-form" class="planner-bar" action="/api/v1/route/map/" method="GET">
        <div class="form-field">
          <label for="start-input">Start Location</label>
          <input id="start-input" class="location-input" type="text" name="start" value="{esc_start_input}" required autocorrect="off" spellcheck="false">
        </div>
        <div class="form-field">
          <label for="finish-input">Destination</label>
          <input id="finish-input" class="location-input" type="text" name="finish" value="{esc_finish_input}" required autocorrect="off" spellcheck="false">
        </div>
        <div class="form-field">
          <label for="range-input">Max Range (mi)</label>
          <input id="range-input" class="num-input" type="number" name="vehicle_range_miles" value="{spec.max_range_miles:.0f}" min="100" max="1500" step="25" required>
        </div>
        <div class="form-field">
          <label for="mpg-input">Efficiency (MPG)</label>
          <input id="mpg-input" class="num-input" type="number" name="mpg" value="{spec.mpg:.1f}" min="2" max="50" step="0.5" required>
        </div>
        <div class="form-field">
          <label for="fuel-input">Start Fuel (gal)</label>
          <input id="fuel-input" class="num-input" type="number" name="initial_fuel_gallons" value="{spec.initial_fuel_gallons:.1f}" min="1" max="200" step="1" required>
        </div>
        <button id="optimize-btn" type="submit" class="btn-primary">Optimize Route</button>
      </form>
    </search>

    <div class="corridors-row" role="group" aria-label="Popular US Freight Corridors">
      <span class="corridors-label">Popular US Freight Corridors:</span>
      <button type="button" class="corridor-chip" data-start="New York, NY" data-finish="Los Angeles, CA">I-80 NY &rarr; LA</button>
      <button type="button" class="corridor-chip" data-start="Jacksonville, FL" data-finish="Los Angeles, CA">I-10 Jacksonville &rarr; LA</button>
      <button type="button" class="corridor-chip" data-start="Boston, MA" data-finish="Seattle, WA">I-90 Boston &rarr; Seattle</button>
      <button type="button" class="corridor-chip" data-start="Laredo, TX" data-finish="Duluth, MN">I-35 Laredo &rarr; Duluth</button>
      <button type="button" class="corridor-chip" data-start="Chicago, IL" data-finish="Indianapolis, IN">Short Haul: Chicago &rarr; Indy</button>
    </div>
  </header>

  <div class="layout">
    <div id="map"></div>
    <div class="sidebar">
      <div class="summary-card">
        <h2>Trip Overview</h2>
        <div class="metric-row">
          <span>Total Distance</span>
          <span class="metric-value">{route.distance_miles:.1f} miles</span>
        </div>
        <div class="metric-row">
          <span>Estimated Driving Time</span>
          <span class="metric-value">{int(route.duration_seconds // 3600)}h {int((route.duration_seconds % 3600) // 60)}m</span>
        </div>
        <div class="metric-row">
          <span>Vehicle Profile</span>
          <span class="metric-value">{spec.max_range_miles:.0f} mi range &bull; {spec.mpg:.1f} MPG ({spec.tank_capacity_gallons:.0f} gal tank)</span>
        </div>
        <div class="metric-row">
          <span>Fuel Consumed</span>
          <span class="metric-value">{plan.total_fuel_consumed_gallons:.1f} gallons</span>
        </div>
        <div class="metric-row">
          <span>Refueling Stops</span>
          <span class="metric-value">{len(plan.stops)} stops</span>
        </div>
        <div class="metric-row" style="margin-top: 8px; border-top: 1px dashed var(--border); padding-top: 8px;">
          <span>Total Fuel Cost</span>
          <span class="metric-value metric-highlight">${plan.total_money_spent_on_fuel_usd}</span>
        </div>
        <div class="metric-row">
          <span style="font-size: 0.8rem; color: #0F172A;">En-Route Pump Spend:</span>
          <span class="metric-value" style="font-size: 0.82rem;">${plan.en_route_pump_cost_usd}</span>
        </div>
      </div>

      <div id="cheapest-station-container"></div>

      <div id="price-chart-card" class="chart-card" style="display: none;">
        <h3>Stop-by-Stop Fuel Price &amp; Tank Profile</h3>
        <div class="chart-subtitle">Click any bar to focus that refueling stop on the map ($/gal &amp; tank level after fill)</div>
        <div id="price-chart-svg-wrap"></div>
      </div>

      <div class="stops-section">
        <h3>Recommended Refueling Stops ({len(plan.stops)})</h3>
        <div id="stops-list"></div>
      </div>
    </div>
  </div>

  <script>
    const routeCoords = {json.dumps(leaflet_coords)};
    const stopsData = {json.dumps(stops_json)};
    const cheapestStation = {json.dumps(cheapest_json)};
    const bbox = {json.dumps(list(route.bbox))};

    // Wire 1-click corridor preset buttons
    document.querySelectorAll('.corridor-chip').forEach((btn) => {{
      btn.addEventListener('click', () => {{
        const startInput = document.getElementById('start-input');
        const finishInput = document.getElementById('finish-input');
        const form = document.getElementById('route-planner-form');
        startInput.value = btn.getAttribute('data-start') || '';
        finishInput.value = btn.getAttribute('data-finish') || '';
        form.submit();
      }});
    }});

    // Auto-adjust default initial fuel when range or MPG changes if user hasn't custom-edited it
    const rangeInput = document.getElementById('range-input');
    const mpgInput = document.getElementById('mpg-input');
    const fuelInput = document.getElementById('fuel-input');
    const syncTankCapacity = () => {{
      const r = parseFloat(rangeInput.value);
      const m = parseFloat(mpgInput.value);
      if (r > 0 && m > 0) {{
        const cap = Math.round((r / m) * 10) / 10;
        fuelInput.max = String(cap);
        if (parseFloat(fuelInput.value) > cap) {{
          fuelInput.value = String(cap);
        }}
      }}
    }};
    rangeInput.addEventListener('change', syncTankCapacity);
    mpgInput.addEventListener('change', syncTankCapacity);

    // Initialize Leaflet Map
    const map = L.map('map');
    L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }}).addTo(map);

    // Draw route polyline
    const polyline = L.polyline(routeCoords, {{
      color: '#0F4C81',
      weight: 5,
      opacity: 0.85,
      lineJoin: 'round'
    }}).addTo(map);

    // Start Pin Icon
    const startIcon = L.divIcon({{
      className: 'custom-pin-start',
      html: '<svg width="24" height="32" viewBox="0 0 24 32"><path d="M12 0C5.37 0 0 5.37 0 12c0 9 12 20 12 20s12-11 12-20c0-6.63-5.37-12-12-12z" fill="#047857"/><circle cx="12" cy="12" r="5" fill="#FFFFFF"/></svg>',
      iconSize: [24, 32],
      iconAnchor: [12, 32],
      popupAnchor: [0, -30]
    }});

    // Finish Pin Icon
    const finishIcon = L.divIcon({{
      className: 'custom-pin-finish',
      html: '<svg width="24" height="32" viewBox="0 0 24 32"><path d="M12 0C5.37 0 0 5.37 0 12c0 9 12 20 12 20s12-11 12-20c0-6.63-5.37-12-12-12z" fill="#000000"/><circle cx="12" cy="12" r="5" fill="#FFFFFF"/></svg>',
      iconSize: [24, 32],
      iconAnchor: [12, 32],
      popupAnchor: [0, -30]
    }});

    // Add Start and Finish markers
    if (routeCoords.length > 0) {{
      L.marker(routeCoords[0], {{ icon: startIcon }})
        .bindPopup('<b>Start:</b> {esc_start}')
        .addTo(map);

      L.marker(routeCoords[routeCoords.length - 1], {{ icon: finishIcon }})
        .bindPopup('<b>Finish:</b> {esc_dest}')
        .addTo(map);
    }}

    // Fit map bounds
    if (bbox && bbox.length === 4) {{
      map.fitBounds([
        [bbox[1], bbox[0]],
        [bbox[3], bbox[2]]
      ], {{ padding: [40, 40] }});
    }} else {{
      map.fitBounds(polyline.getBounds(), {{ padding: [40, 40] }});
    }}

    // Render Cheapest Corridor Station highlight card if present
    const cheapestContainer = document.getElementById('cheapest-station-container');
    if (cheapestStation) {{
      const cCard = document.createElement('div');
      cCard.className = 'cheapest-card';
      cCard.innerHTML = `
        <div class="cheapest-card-title">Lowest-Price Corridor Station ($${{cheapestStation.price}}/gal)</div>
        <div class="cheapest-card-body">
          ${{cheapestStation.name}} &mdash; ${{cheapestStation.city}}, ${{cheapestStation.state}} (Mile ${{cheapestStation.mile}}, ${{cheapestStation.off_route}} mi off route)
        </div>
      `;
      cCard.addEventListener('click', () => {{
        map.setView([cheapestStation.lat, cheapestStation.lon], 11, {{ animate: true }});
      }});
      cheapestContainer.appendChild(cCard);
    }}

    // Render Stop Markers, Cards & Interactive Price/Tank Chart
    const stopMarkers = {{}};
    const stopsContainer = document.getElementById('stops-list');

    if (stopsData.length === 0) {{
      stopsContainer.innerHTML = '<div class="empty-stops-notice">No refueling stops required! Vehicle range ({spec.max_range_miles:.0f} miles) covers this trip on the starting tank.</div>';
    }} else {{
      stopsData.forEach(s => {{
        const stopIcon = L.divIcon({{
          className: 'custom-pin-stop',
          html: `<svg width="28" height="36" viewBox="0 0 28 36">
                   <path d="M14 0C6.27 0 0 6.27 0 14c0 10.5 14 22 14 22s14-11.5 14-22c0-7.73-6.27-14-14-14z" fill="#C84326"/>
                   <circle cx="14" cy="14" r="10" fill="#FFFFFF"/>
                   <text x="14" y="18" font-size="12" font-weight="bold" fill="#000000" text-anchor="middle">${{s.stop_number}}</text>
                 </svg>`,
          iconSize: [28, 36],
          iconAnchor: [14, 36],
          popupAnchor: [0, -34]
        }});

        const popupContent = `
          <b>Stop #${{s.stop_number}}: ${{s.name}}</b><br>
          ${{s.address}}, ${{s.city}}, ${{s.state}}<br>
          <b>Price:</b> $${{s.price}} / gal<br>
          <b>Fuel Added:</b> ${{s.gallons}} gal ($${{s.cost}})<br>
          <b>Tank Level:</b> ${{s.tank_before}} gal &rarr; ${{s.tank_after}} gal<br>
          <b>Route Mile:</b> Mile ${{s.mile}}
        `;

        const marker = L.marker([s.lat, s.lon], {{ icon: stopIcon }})
          .bindPopup(popupContent)
          .addTo(map);

        stopMarkers[s.stop_number] = marker;

        const card = document.createElement('div');
        card.className = 'stop-card';
        card.innerHTML = `
          <div class="stop-card-header">
            <span>STOP #${{s.stop_number}} (${{s.state}})</span>
            <span style="color: var(--accent);">$${{s.price}} / gal</span>
          </div>
          <div class="stop-name">${{s.name}}</div>
          <div class="stop-address">${{s.address}}, ${{s.city}}, ${{s.state}}</div>
          <div class="stop-details">
            <span>Added: <b>+${{s.gallons}} gal</b> ($${{s.cost}})</span>
            <span>Tank: <b>${{s.tank_before}}&rarr;${{s.tank_after}} gal</b></span>
            <span>At: <b>Mile ${{s.mile}}</b></span>
          </div>
        `;
        card.addEventListener('click', () => {{
          map.setView([s.lat, s.lon], 11, {{ animate: true }});
          marker.openPopup();
        }});
        stopsContainer.appendChild(card);
      }});

      // Render SVG Stop-by-Stop Fuel Price & Tank Level Chart
      const chartCard = document.getElementById('price-chart-card');
      const chartWrap = document.getElementById('price-chart-svg-wrap');
      chartCard.style.display = 'block';

      const prices = stopsData.map(s => s.price_num);
      const minP = Math.min(...prices);
      const maxP = Math.max(...prices);
      const svgW = 365;
      const svgH = 145;
      const padL = 32;
      const padR = 10;
      const padT = 20;
      const padB = 34;
      const plotW = svgW - padL - padR;
      const plotH = svgH - padT - padB;
      const n = stopsData.length;
      const slotW = plotW / n;
      const barW = Math.max(10, Math.min(26, slotW * 0.65));

      let barsSvg = '';
      stopsData.forEach((s, idx) => {{
        const ratio = (maxP > minP) ? ((s.price_num - minP) / (maxP - minP)) : 0.5;
        const barH = Math.round(28 + ratio * (plotH - 30));
        const x = Math.round(padL + idx * slotW + (slotW - barW) / 2);
        const y = padT + plotH - barH;
        const isLowest = Math.abs(s.price_num - minP) < 0.0011;
        const fill = isLowest ? '#047857' : '#0F4C81';
        barsSvg += `
          <g class="chart-bar-group" data-stop="${{s.stop_number}}" style="cursor: pointer;">
            <title>Stop #${{s.stop_number}} (${{s.city}}, ${{s.state}}) - $${{s.price}}/gal | Mile ${{s.mile}} | Tank after fill: ${{s.tank_after}} gal</title>
            <rect x="${{x}}" y="${{y}}" width="${{barW}}" height="${{barH}}" rx="3" fill="${{fill}}" />
            <text x="${{x + barW / 2}}" y="${{y - 4}}" font-size="9" font-weight="700" fill="#000000" text-anchor="middle">$${{s.price_num.toFixed(2)}}</text>
            <text x="${{x + barW / 2}}" y="${{padT + plotH + 13}}" font-size="9" font-weight="700" fill="#000000" text-anchor="middle">#${{s.stop_number}}</text>
            <text x="${{x + barW / 2}}" y="${{padT + plotH + 24}}" font-size="8" fill="#0F172A" text-anchor="middle">${{s.state}}</text>
          </g>
        `;
      }});

      chartWrap.innerHTML = `
        <svg viewBox="0 0 ${{svgW}} ${{svgH}}" width="100%" height="${{svgH}}" role="img" aria-label="Fuel price per gallon by stop number">
          <line x1="${{padL}}" y1="${{padT + plotH}}" x2="${{svgW - padR}}" y2="${{padT + plotH}}" stroke="#475569" stroke-width="1.5"/>
          <text x="2" y="${{padT + 10}}" font-size="9" font-weight="700" fill="#000000">$${{maxP.toFixed(2)}}</text>
          <text x="2" y="${{padT + plotH}}" font-size="9" font-weight="700" fill="#000000">$${{minP.toFixed(2)}}</text>
          ${{barsSvg}}
        </svg>
      `;

      chartWrap.querySelectorAll('.chart-bar-group').forEach((g) => {{
        g.addEventListener('click', () => {{
          const num = parseInt(g.getAttribute('data-stop'), 10);
          const stopObj = stopsData.find(item => item.stop_number === num);
          const m = stopMarkers[num];
          if (stopObj && m) {{
            map.setView([stopObj.lat, stopObj.lon], 11, {{ animate: true }});
            m.openPopup();
          }}
        }});
      }});
    }}
  </script>
</body>
</html>"""
        response = HttpResponse(html, content_type="text/html")
        response["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response
