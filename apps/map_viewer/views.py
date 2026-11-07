"""Interactive Leaflet + OpenStreetMap Route & Fuel Stops Map View."""
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
        range_miles = float(request.GET.get("vehicle_range_miles", 500.0))
        mpg = float(request.GET.get("mpg", 10.0))
        init_fuel_param = request.GET.get("initial_fuel_gallons")
        init_fuel = float(init_fuel_param) if init_fuel_param is not None else (range_miles / mpg)
        corridor_miles = float(request.GET.get("corridor_miles", 5.0))

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
        # Downsample visual coordinates if large
        coords = route.coordinates
        if len(coords) > 1500:
            step = len(coords) // 1200 + 1
            coords = coords[::step]
            if coords[-1] != route.coordinates[-1]:
                coords.append(route.coordinates[-1])
        leaflet_coords = [[round(pt[0], 5), round(pt[1], 5)] for pt in coords]

        # Fuel stops data for markers & cards
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
                "gallons": f"{s.gallons_purchased:.2f}",
                "cost": f"{s.cost_usd:.2f}",
                "mile": f"{s.route_mile_marker:.1f}",
            }
            for s in plan.stops
        ]

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Fuel Route Optimizer | {origin.resolved_name} to {dest.resolved_name}</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin=""/>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
  <style>
    :root {{
      --primary: #0F4C81;
      --primary-dark: #0A3254;
      --accent: #E05638;
      --bg: #F8FAFC;
      --card-bg: #FFFFFF;
      --text: #000000;
      --text-muted: #1E293B;
      --border: #CBD5E1;
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
      padding: 12px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      z-index: 1000;
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 12px;
      color: #000000;
      font-weight: 700;
      font-size: 1.15rem;
    }}
    .brand svg {{
      width: 32px;
      height: 32px;
      fill: none;
      stroke: var(--primary);
      stroke-width: 2.2;
    }}
    .route-title {{
      font-size: 0.95rem;
      font-weight: 600;
      color: #000000;
    }}
    .layout {{
      display: flex;
      flex: 1;
      height: calc(100vh - 60px);
    }}
    #map {{
      flex: 1;
      height: 100%;
      background: #E2E8F0;
    }}
    .sidebar {{
      width: 400px;
      background: #FFFFFF;
      border-left: 2px solid var(--border);
      display: flex;
      flex-direction: column;
      overflow-y: auto;
      padding: 20px;
      gap: 16px;
    }}
    .summary-card {{
      background: #F1F5F9;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 16px;
    }}
    .summary-card h2 {{
      font-size: 1rem;
      font-weight: 700;
      color: #000000;
      margin-bottom: 12px;
      border-bottom: 1px solid var(--border);
      padding-bottom: 6px;
    }}
    .metric-row {{
      display: flex;
      justify-content: space-between;
      margin-bottom: 8px;
      font-size: 0.9rem;
      color: #000000;
    }}
    .metric-value {{
      font-weight: 700;
      color: #000000;
    }}
    .metric-highlight {{
      font-size: 1.15rem;
      color: var(--primary);
      font-weight: 800;
    }}
    .stops-section h3 {{
      font-size: 0.95rem;
      font-weight: 700;
      color: #000000;
      margin: 8px 0;
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
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
      border-left-color: var(--accent);
    }}
    .stop-card-header {{
      display: flex;
      justify-content: space-between;
      font-weight: 700;
      font-size: 0.85rem;
      color: #000000;
      margin-bottom: 4px;
    }}
    .stop-name {{
      font-size: 0.88rem;
      font-weight: 700;
      color: #000000;
      margin-bottom: 4px;
    }}
    .stop-address {{
      font-size: 0.8rem;
      color: #1E293B;
      margin-bottom: 6px;
    }}
    .stop-details {{
      display: flex;
      justify-content: space-between;
      font-size: 0.82rem;
      color: #000000;
      background: #F8FAFC;
      padding: 6px;
      border-radius: 4px;
    }}
    .empty-stops-notice {{
      background: #EFF6FF;
      border: 1px solid #BFDBFE;
      padding: 12px;
      border-radius: 6px;
      font-size: 0.88rem;
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
    @media (max-width: 768px) {{
      .layout {{ flex-direction: column; }}
      .sidebar {{ width: 100%; height: 40%; }}
      #map {{ height: 60%; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <svg viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="10"/>
        <path d="M12 2a10 10 0 0 1 10 10M12 12l5-5M12 12l-3 4"/>
      </svg>
      <span>Fuel Route Optimizer</span>
    </div>
    <div class="route-title">
      {origin.resolved_name} &rarr; {dest.resolved_name} ({route.distance_miles:.1f} miles)
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
          <span>Vehicle Fuel Efficiency</span>
          <span class="metric-value">{spec.mpg:.1f} MPG</span>
        </div>
        <div class="metric-row">
          <span>Fuel Consumed</span>
          <span class="metric-value">{plan.total_fuel_consumed_gallons:.1f} gallons</span>
        </div>
        <div class="metric-row">
          <span>Refueling Stops</span>
          <span class="metric-value">{len(plan.stops)} stops</span>
        </div>
        <div class="metric-row" style="margin-top: 10px; border-top: 1px dashed var(--border); padding-top: 8px;">
          <span>Total Fuel Cost</span>
          <span class="metric-value metric-highlight">${plan.total_money_spent_on_fuel_usd}</span>
        </div>
        <div class="metric-row">
          <span style="font-size: 0.8rem; color: #1E293B;">En-Route Pump Spend:</span>
          <span class="metric-value" style="font-size: 0.82rem;">${plan.en_route_pump_cost_usd}</span>
        </div>
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
    const bbox = {json.dumps(list(route.bbox))}; // [min_lon, min_lat, max_lon, max_lat]

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
      html: '<svg width="24" height="32" viewBox="0 0 24 32"><path d="M12 0C5.37 0 0 5.37 0 12c0 9 12 20 12 20s12-11 12-20c0-6.63-5.37-12-12-12z" fill="#10B981"/><circle cx="12" cy="12" r="5" fill="#FFFFFF"/></svg>',
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
        .bindPopup('<b>Start:</b> {origin.resolved_name}')
        .addTo(map);

      L.marker(routeCoords[routeCoords.length - 1], {{ icon: finishIcon }})
        .bindPopup('<b>Finish:</b> {dest.resolved_name}')
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

    // Render Stop Markers & Cards
    const stopMarkers = {{}};
    const stopsContainer = document.getElementById('stops-list');

    if (stopsData.length === 0) {{
      stopsContainer.innerHTML = '<div class="empty-stops-notice">No refueling stops required! Vehicle range (500 miles) exceeds trip distance with initial tank.</div>';
    }} else {{
      stopsData.forEach(s => {{
        // SVG Pin with Stop Number
        const stopIcon = L.divIcon({{
          className: 'custom-pin-stop',
          html: `<svg width="28" height="36" viewBox="0 0 28 36">
                   <path d="M14 0C6.27 0 0 6.27 0 14c0 10.5 14 22 14 22s14-11.5 14-22c0-7.73-6.27-14-14-14z" fill="#E05638"/>
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
          <b>Route Mile:</b> Mile ${{s.mile}}
        `;

        const marker = L.marker([s.lat, s.lon], {{ icon: stopIcon }})
          .bindPopup(popupContent)
          .addTo(map);

        stopMarkers[s.stop_number] = marker;

        // Create Sidebar Card
        const card = document.createElement('div');
        card.className = 'stop-card';
        card.innerHTML = `
          <div class="stop-card-header">
            <span>STOP #${{s.stop_number}}</span>
            <span style="color: var(--accent);">$${{s.price}} / gal</span>
          </div>
          <div class="stop-name">${{s.name}}</div>
          <div class="stop-address">${{s.address}}, ${{s.city}}, ${{s.state}}</div>
          <div class="stop-details">
            <span>Added: <b>${{s.gallons}} gal</b></span>
            <span>Cost: <b>$${{s.cost}}</b></span>
            <span>At: <b>Mile ${{s.mile}}</b></span>
          </div>
        `;
        card.onclick = () => {{
          map.setView([s.lat, s.lon], 11, {{ animate: true }});
          marker.openPopup();
        }};
        stopsContainer.appendChild(card);
      }});
    }}
  </script>
</body>
</html>"""
        return HttpResponse(html, content_type="text/html")
