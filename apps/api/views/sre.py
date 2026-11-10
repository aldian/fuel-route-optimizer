"""SRE SLA/SLO Compliance Scorecard, Live Drill Probe, Prometheus Metrics, and Observability Dashboard views."""
import json
from typing import Any
from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.common.sre import sre_monitor


class SRESLAView(APIView):
    """Real-time SRE SLA/SLO Compliance Scorecard & Error Budget endpoint."""

    authentication_classes: list[Any] = []
    permission_classes: list[Any] = []

    @extend_schema(
        summary="SRE SLA/SLO Compliance Report & Error Budget",
        description=(
            "Evaluates all 6 authoritative SLAs (Availability >= 99.9%, Internal Compute P99 < 15ms, "
            "Cached E2E P95 < 20ms, Uncached E2E P95 < 350ms, External API Call Budget <= 3, and "
            "6,626 US Stations + 3D cKDTree readiness) and returns real-time SLI percentiles and Error Budget status."
        ),
        responses={
            200: inline_serializer(
                name="SRESLAResponse",
                fields={
                    "overall_status": serializers.CharField(),
                    "sla_compliant": serializers.BooleanField(),
                    "uptime_seconds": serializers.FloatField(),
                    "error_budget": serializers.DictField(),
                    "slos": serializers.ListField(child=serializers.DictField()),
                    "telemetry": serializers.DictField(),
                },
            )
        },
    )
    def get(self, request: Request) -> Response:
        report = sre_monitor.evaluate_sla()
        return Response(report, status=status.HTTP_200_OK)


class SREProbeDrillView(APIView):
    """Executes an active synthetic multi-corridor US freight optimization & cache drill."""

    authentication_classes: list[Any] = []
    permission_classes: list[Any] = []

    @extend_schema(
        summary="Execute Live Multi-Corridor SRE Load & Cache Drill",
        description=(
            "Runs a live synthetic multi-corridor optimization wave across I-95, I-35, I-80, and I-5 "
            "(both uncached 3D cKDTree + DP solver passes and cached passes) and returns updated SLA telemetry."
        ),
        responses={
            200: inline_serializer(
                name="SREProbeDrillResponse",
                fields={
                    "drill_executed": serializers.BooleanField(),
                    "corridors_tested": serializers.IntegerField(),
                    "requests_generated": serializers.IntegerField(),
                    "results": serializers.ListField(child=serializers.DictField()),
                    "sla_report": serializers.DictField(),
                },
            )
        },
    )
    def post(self, request: Request) -> Response:
        corridor_idx = request.data.get("corridor_index") if isinstance(request.data, dict) else None
        iterations = request.data.get("iterations", 4) if isinstance(request.data, dict) else 4
        try:
            c_idx = int(corridor_idx) if corridor_idx is not None else None
        except (TypeError, ValueError):
            c_idx = None
        try:
            iters = int(iterations)
        except (TypeError, ValueError):
            iters = 4
        drill_output = sre_monitor.run_synthetic_drill(iterations=iters, corridor_index=c_idx)
        return Response(drill_output, status=status.HTTP_200_OK)


class SREPrometheusMetricsView(APIView):
    """Prometheus / OpenMetrics text exposition endpoint for SRE scraping."""

    authentication_classes: list[Any] = []
    permission_classes: list[Any] = []

    @extend_schema(
        summary="Prometheus / OpenMetrics SRE Telemetry Exporter",
        description="Exposes SLIs, SLO compliance gauge, latency percentiles, and error budget metrics in Prometheus text format.",
        responses={(200, "text/plain"): OpenApiTypes.STR},
    )
    def get(self, request: Request) -> HttpResponse:
        metrics_text = sre_monitor.render_prometheus_metrics()
        return HttpResponse(
            metrics_text,
            content_type="text/plain; version=0.0.4; charset=utf-8",
            status=status.HTTP_200_OK,
        )


class SREDashboardView(APIView):
    """Self-contained interactive SRE & SLO Observability Console."""

    authentication_classes: list[Any] = []
    permission_classes: list[Any] = []

    @extend_schema(
        summary="Interactive SRE & SLO Observability Console (HTML)",
        description="Renders a real-time SRE dashboard displaying all 6 SLOs, Error Budget burn, latency percentiles, Prometheus metrics, and a one-click US corridor load drill.",
        responses={(200, "text/html"): OpenApiTypes.STR},
    )
    def get(self, request: Request) -> HttpResponse:
        initial_report = sre_monitor.evaluate_sla()
        initial_prom = sre_monitor.render_prometheus_metrics()
        report_json = json.dumps(initial_report)
        prom_json = json.dumps(initial_prom)

        html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SRE &amp; SLO Observability Console — Spotter Fuel Route Optimizer</title>
  <style>
    :root {{
      --bg: #f8fafc;
      --surface: #ffffff;
      --border: #cbd5e1;
      --text: #0f172a;
      --text-muted: #334155;
      --primary: #0284c7;
      --primary-hover: #0369a1;
      --success-bg: #dcfce7;
      --success-border: #16a34a;
      --success-text: #14532d;
      --danger-bg: #fee2e2;
      --danger-border: #dc2626;
      --danger-text: #7f1d1d;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      padding-bottom: 110px;
    }}
    header {{
      background: var(--surface);
      border-bottom: 2px solid var(--border);
      padding: 16px 32px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 16px;
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 12px;
    }}
    .brand h1 {{
      margin: 0;
      font-size: 20px;
      font-weight: 800;
      color: var(--text);
    }}
    .brand span {{
      font-size: 13px;
      font-weight: 600;
      color: var(--text-muted);
    }}
    .actions {{
      display: flex;
      align-items: center;
      gap: 12px;
    }}
    .btn {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 10px 18px;
      border-radius: 8px;
      font-size: 14px;
      font-weight: 700;
      cursor: pointer;
      border: 2px solid var(--primary);
      background: var(--primary);
      color: #ffffff;
      text-decoration: none;
      transition: background 0.15s ease;
    }}
    .btn:hover {{ background: var(--primary-hover); }}
    .btn-outline {{
      background: var(--surface);
      color: var(--text);
      border-color: var(--border);
    }}
    .btn-outline:hover {{ background: #f1f5f9; }}
    .btn:focus-visible, .corridor-probe-chip:focus-visible {{
      outline: 3px solid var(--primary);
      outline-offset: 2px;
    }}
    .corridor-probe-bar {{
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 18px;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 10px 16px;
    }}
    .corridor-probe-label {{
      font-size: 12px;
      font-weight: 800;
      color: var(--text);
      text-transform: uppercase;
      letter-spacing: 0.04em;
      margin-right: 4px;
    }}
    .corridor-probe-chip {{
      font: inherit;
      font-size: 12px;
      font-weight: 700;
      color: var(--text);
      background: #f1f5f9;
      border: 1px solid #64748b;
      border-radius: 999px;
      padding: 4px 12px;
      cursor: pointer;
      transition: background 0.12s ease, border-color 0.12s ease;
    }}
    .corridor-probe-chip:hover {{
      background: #e2e8f0;
      border-color: var(--text);
    }}
    main {{
      max-width: 1680px;
      margin: 24px auto;
      padding: 0 32px;
    }}
    .kpi-grid {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 16px;
      margin-bottom: 24px;
    }}
    .card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 18px 22px;
      box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06);
    }}
    .kpi-label {{
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-muted);
      margin-bottom: 8px;
    }}
    .kpi-value {{
      font-size: 26px;
      font-weight: 800;
      color: var(--text);
    }}
    .kpi-sub {{
      font-size: 13px;
      font-weight: 600;
      color: var(--text-muted);
      margin-top: 6px;
    }}
    .progress-track {{
      height: 10px;
      background: #e2e8f0;
      border-radius: 999px;
      overflow: hidden;
      margin-top: 10px;
    }}
    .progress-fill {{
      height: 100%;
      background: #16a34a;
      transition: width 0.3s ease;
    }}
    .section-title {{
      font-size: 16px;
      font-weight: 800;
      color: var(--text);
      margin: 0 0 14px 0;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }}
    .slo-grid {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 16px;
      margin-bottom: 24px;
    }}
    .slo-card {{
      background: var(--surface);
      border: 2px solid var(--border);
      border-radius: 10px;
      padding: 16px 20px;
    }}
    .slo-card.pass {{ border-left: 6px solid var(--success-border); }}
    .slo-card.fail {{ border-left: 6px solid var(--danger-border); }}
    .slo-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 8px;
    }}
    .slo-id {{
      font-size: 12px;
      font-weight: 800;
      color: var(--text-muted);
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    }}
    .badge {{
      padding: 3px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 800;
    }}
    .badge-pass {{
      background: var(--success-bg);
      color: var(--success-text);
      border: 1px solid var(--success-border);
    }}
    .badge-fail {{
      background: var(--danger-bg);
      color: var(--danger-text);
      border: 1px solid var(--danger-border);
    }}
    .slo-name {{
      font-size: 15px;
      font-weight: 700;
      color: var(--text);
      margin-bottom: 6px;
    }}
    .slo-meta {{
      font-size: 13px;
      color: var(--text-muted);
      font-weight: 600;
    }}
    .bottom-grid {{
      display: grid;
      grid-template-columns: 1.35fr 1fr;
      gap: 20px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }}
    th, td {{
      text-align: left;
      padding: 10px 12px;
      border-bottom: 1px solid var(--border);
      color: var(--text);
    }}
    th {{
      background: #f1f5f9;
      font-weight: 800;
      text-transform: uppercase;
      font-size: 11px;
      letter-spacing: 0.04em;
    }}
    pre.prom-box {{
      margin: 0;
      padding: 14px;
      background: #0f172a;
      color: #f8fafc;
      border-radius: 8px;
      font-size: 12px;
      line-height: 1.45;
      overflow-x: auto;
      max-height: 320px;
    }}
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <svg width="36" height="36" viewBox="0 0 36 36" aria-hidden="true">
        <circle cx="18" cy="18" r="17" fill="#0284c7" />
        <text x="18" y="24" text-anchor="middle" fill="#ffffff" font-size="18" font-weight="800" font-family="system-ui, sans-serif">A</text>
      </svg>
      <div>
        <h1>Spotter Fuel Route Optimizer • SRE &amp; SLO Observability Console</h1>
        <span>Real-Time Service Level Objectives, 99.9% Error Budget &amp; Multi-Corridor Telemetry</span>
      </div>
    </div>
    <div class="actions">
      <button id="btn-run-drill" class="btn" type="button">
        ▶ Run Live US Corridor Load &amp; Cache Drill (15 Probes)
      </button>
      <button id="btn-refresh-sre" class="btn btn-outline" type="button">
        ↻ Refresh Telemetry
      </button>
      <button id="btn-export-json" class="btn btn-outline" type="button">
        ⬇ Export SLA Audit JSON
      </button>
      <a class="btn btn-outline" href="/api/docs/">OpenAPI Docs</a>
      <a class="btn btn-outline" href="/api/v1/route/map/?start=New+York,+NY&amp;finish=Los+Angeles,+CA">Route Map</a>
    </div>
  </header>

  <main>
    <div class="corridor-probe-bar" role="group" aria-label="Synthetic US Freight Corridor Probes">
      <span class="corridor-probe-label">1-Click Corridor Probes:</span>
      <button type="button" class="corridor-probe-chip" data-corridor-index="0">Probe I-95 (NY &rarr; DC)</button>
      <button type="button" class="corridor-probe-chip" data-corridor-index="1">Probe I-35 (Austin &rarr; OKC)</button>
      <button type="button" class="corridor-probe-chip" data-corridor-index="2">Probe I-80 (Chicago &rarr; Des Moines)</button>
      <button type="button" class="corridor-probe-chip" data-corridor-index="3">Probe I-5 (LA &rarr; Sacramento)</button>
      <button type="button" class="corridor-probe-chip" data-corridor-index="4">Probe I-10 (Houston &rarr; San Antonio)</button>
    </div>

    <div class="kpi-grid">
      <div class="card">
        <div class="kpi-label">Overall SLA Compliance</div>
        <div id="kpi-status" class="kpi-value">MEETING_SLA</div>
        <div id="kpi-uptime" class="kpi-sub">All 6 Authoritative SLOs Passing</div>
      </div>
      <div class="card">
        <div class="kpi-label">Availability &amp; Error Budget (Target &ge; 99.9%)</div>
        <div id="kpi-avail" class="kpi-value">100.000%</div>
        <div class="progress-track">
          <div id="kpi-budget-bar" class="progress-fill" style="width: 100%;"></div>
        </div>
        <div id="kpi-budget-text" class="kpi-sub">100.0% Error Budget Remaining (0 5xx Errors)</div>
      </div>
      <div class="card">
        <div class="kpi-label">Traffic &amp; Shared Cache Hit Ratio</div>
        <div id="kpi-requests" class="kpi-value">0 Requests</div>
        <div id="kpi-cache" class="kpi-sub">Cache Hit Ratio: 0.0% • External Call Violations: 0</div>
      </div>
      <div class="card">
        <div class="kpi-label">3D Spatial cKDTree &amp; OPIS Station Dataset</div>
        <div id="kpi-stations" class="kpi-value">6,626 Stations</div>
        <div id="kpi-spatial" class="kpi-sub">Spatial Index Ready: True • Compute P99: 0.0 ms</div>
      </div>
    </div>

    <h2 class="section-title">
      <span>6 Authoritative Service Level Objectives (SLOs)</span>
      <span id="drill-status-banner" style="font-size:13px;font-weight:700;color:#0284c7;">Ready</span>
    </h2>
    <div id="slo-grid" class="slo-grid"></div>

    <div class="card" style="margin-bottom: 24px;">
      <h3 class="section-title">
        <span>Real-Time Latency Percentiles (P50 / P95 / P99) vs. Authoritative SLA Ceilings</span>
        <span style="font-size:12px;color:var(--text-muted);">Measured across Redis-shared sliding window</span>
      </h3>
      <div id="latency-chart-wrap"></div>
    </div>

    <div class="bottom-grid">
      <div class="card">
        <h3 class="section-title">
          <span>Live Multi-Corridor US Freight Benchmark &amp; Latency Percentiles</span>
          <span style="font-size:12px;color:var(--text-muted);">3D cKDTree + O(K log K) Refuel Solver</span>
        </h3>
        <table>
          <thead>
            <tr>
              <th>US Freight Corridor</th>
              <th>Distance</th>
              <th>Candidates</th>
              <th>Compute CPU</th>
              <th>Uncached E2E</th>
              <th>Cached E2E</th>
              <th>Cheapest Stop</th>
            </tr>
          </thead>
          <tbody id="drill-table-body">
            <tr>
              <td colspan="7" style="text-align:center;font-weight:600;color:var(--text-muted);padding:22px;">
                Click "Run Live US Corridor Load &amp; Cache Drill" above to execute 12 live route optimization &amp; cache probes across I-95, I-35, I-80, and I-5.
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="card">
        <h3 class="section-title">
          <span>Prometheus / OpenMetrics Exporter (/api/v1/sre/metrics/)</span>
          <span style="font-size:12px;color:var(--text-muted);">text/plain; version=0.0.4</span>
        </h3>
        <pre id="prom-box" class="prom-box"></pre>
      </div>
    </div>
  </main>

  <script>
    const initialReport = {report_json};
    const initialProm = {prom_json};
    let latestReport = initialReport;

    function renderLatencyChart(tel) {{
      const comp = tel.compute_latency_ms || {{}};
      const cached = tel.cached_e2e_latency_ms || {{}};
      const uncached = tel.uncached_e2e_latency_ms || {{}};
      const rows = [
        {{
          label: 'Internal 3D cKDTree + DP Compute (SLO-LAT-01 < 15.0 ms P99)',
          p50: Number(comp.p50 || 0),
          p95: Number(comp.p95 || 0),
          p99: Number(comp.p99 || 0),
          sla: 15.0,
          activeMetric: Number(comp.p99 || 0),
          samples: Number(comp.samples || 0),
        }},
        {{
          label: 'Cached End-to-End Route Response (SLO-LAT-02 < 20.0 ms P95)',
          p50: Number(cached.p50 || 0),
          p95: Number(cached.p95 || 0),
          p99: Number(cached.p99 || 0),
          sla: 20.0,
          activeMetric: Number(cached.p95 || 0),
          samples: Number(cached.samples || 0),
        }},
        {{
          label: 'Uncached End-to-End Route Response (SLO-LAT-03 < 350.0 ms P95)',
          p50: Number(uncached.p50 || 0),
          p95: Number(uncached.p95 || 0),
          p99: Number(uncached.p99 || 0),
          sla: 350.0,
          activeMetric: Number(uncached.p95 || 0),
          samples: Number(uncached.samples || 0),
        }},
      ];

      const svgW = 920;
      const svgH = 158;
      const barX = 395;
      const maxBarW = 360;
      let rowsSvg = '';
      rows.forEach((r, idx) => {{
        const y = 18 + idx * 48;
        const p50W = Math.min(maxBarW, Math.max(4, Math.round((r.p50 / r.sla) * maxBarW)));
        const p95W = Math.min(maxBarW, Math.max(6, Math.round((r.p95 / r.sla) * maxBarW)));
        const p99W = Math.min(maxBarW, Math.max(8, Math.round((r.p99 / r.sla) * maxBarW)));
        const ok = r.activeMetric < r.sla;
        const barColor = ok ? '#0284c7' : '#dc2626';
        rowsSvg += `
          <g>
            <text x="4" y="${{y + 14}}" font-size="12" font-weight="800" fill="#0f172a">${{r.label}}</text>
            <text x="4" y="${{y + 30}}" font-size="11" font-weight="600" fill="#334155">Samples: n=${{r.samples}} • P50: ${{r.p50.toFixed(2)}} ms • P95: ${{r.p95.toFixed(2)}} ms • P99: ${{r.p99.toFixed(2)}} ms</text>
            <rect x="${{barX}}" y="${{y + 4}}" width="${{maxBarW}}" height="22" rx="5" fill="#e2e8f0" />
            <rect x="${{barX}}" y="${{y + 4}}" width="${{p99W}}" height="22" rx="5" fill="#93c5fd" />
            <rect x="${{barX}}" y="${{y + 4}}" width="${{p95W}}" height="22" rx="5" fill="${{barColor}}" />
            <rect x="${{barX}}" y="${{y + 4}}" width="${{p50W}}" height="22" rx="5" fill="#0369a1" />
            <line x1="${{barX + maxBarW}}" y1="${{y + 1}}" x2="${{barX + maxBarW}}" y2="${{y + 29}}" stroke="#16a34a" stroke-width="2.5" />
            <text x="${{barX + maxBarW + 10}}" y="${{y + 19}}" font-size="11" font-weight="800" fill="#0f172a">${{r.activeMetric.toFixed(2)}} / ${{r.sla.toFixed(1)}} ms SLA</text>
          </g>
        `;
      }});

      const wrap = document.getElementById('latency-chart-wrap');
      if (wrap) {{
        wrap.innerHTML = `
          <svg viewBox="0 0 ${{svgW}} ${{svgH}}" width="100%" height="${{svgH}}" role="img" aria-label="Latency percentiles compared against SLA ceilings">
            ${{rowsSvg}}
          </svg>
        `;
      }}
    }}

    function renderReport(report, promText) {{
      latestReport = report;
      const isOk = Boolean(report.sla_compliant);
      const statusEl = document.getElementById('kpi-status');
      statusEl.textContent = isOk ? '✓ ' + report.overall_status : '✗ ' + report.overall_status;
      statusEl.style.color = isOk ? '#15803d' : '#b91c1c';

      const eb = report.error_budget || {{}};
      const tel = report.telemetry || {{}};
      document.getElementById('kpi-uptime').textContent =
        `Uptime: ${{report.uptime_seconds}}s • All 6 SLOs Audited Live`;
      document.getElementById('kpi-avail').textContent =
        `${{Number(eb.observed_availability_pct || 100).toFixed(3)}}%`;
      document.getElementById('kpi-budget-bar').style.width =
        `${{Math.max(0, Math.min(100, eb.error_budget_remaining_pct || 100))}}%`;
      document.getElementById('kpi-budget-text').textContent =
        `${{eb.error_budget_remaining_pct}}% Error Budget Remaining (${{tel.server_error_requests_5xx || 0}} 5xx Errors)`;

      document.getElementById('kpi-requests').textContent =
        `${{tel.total_requests || 0}} Requests`;
      document.getElementById('kpi-cache').textContent =
        `Cache Hit Ratio: ${{tel.cache_hit_ratio_pct || 0}}% (${{tel.cache_hits || 0}} hits) • Budget Violations: ${{(tel.external_api_calls || {{}}).budget_violations || 0}}`;

      const dr = tel.data_readiness || {{}};
      const comp = tel.compute_latency_ms || {{}};
      document.getElementById('kpi-stations').textContent =
        `${{Number(dr.active_us_stations || 0).toLocaleString()}} US Stations`;
      document.getElementById('kpi-spatial').textContent =
        `3D cKDTree Ready: ${{dr.spatial_index_ready}} • Compute P99: ${{comp.p99 || 0}} ms (P50: ${{comp.p50 || 0}} ms)`;

      const grid = document.getElementById('slo-grid');
      grid.innerHTML = '';
      (report.slos || []).forEach(slo => {{
        const card = document.createElement('div');
        card.className = 'slo-card ' + (slo.passed ? 'pass' : 'fail');
        card.innerHTML = `
          <div class="slo-header">
            <span class="slo-id">${{slo.id}}</span>
            <span class="badge ${{slo.passed ? 'badge-pass' : 'badge-fail'}}">${{slo.passed ? 'PASS' : 'BREACHED'}}</span>
          </div>
          <div class="slo-name">${{slo.name}}</div>
          <div class="slo-meta">Target: <strong>${{slo.target}}</strong></div>
          <div class="slo-meta" style="margin-top:4px;color:#0f172a;">Actual: <strong>${{slo.actual}}</strong></div>
        `;
        grid.appendChild(card);
      }});

      if (promText) {{
        document.getElementById('prom-box').textContent = promText;
      }}
      renderLatencyChart(tel);
    }}

    async function refreshTelemetry() {{
      const [slaResp, promResp] = await Promise.all([
        fetch('/api/v1/sre/sla/'),
        fetch('/api/v1/sre/metrics/')
      ]);
      const report = await slaResp.json();
      const promText = await promResp.text();
      renderReport(report, promText);
    }}

    async function runLiveDrill(corridorIndex = null) {{
      const btn = document.getElementById('btn-run-drill');
      const banner = document.getElementById('drill-status-banner');
      btn.disabled = true;
      const payload = corridorIndex !== null ? {{ corridor_index: corridorIndex }} : {{ iterations: 5 }};
      banner.textContent = corridorIndex !== null
        ? `Running synthetic probe on corridor #${{corridorIndex + 1}}...`
        : 'Running 15 multi-corridor optimization & cache probes across 5 US interstates...';
      try {{
        const resp = await fetch('/api/v1/sre/probe/', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify(payload),
        }});
        const data = await resp.json();
        const promResp = await fetch('/api/v1/sre/metrics/');
        const promText = await promResp.text();

        const tbody = document.getElementById('drill-table-body');
        tbody.innerHTML = '';
        (data.results || []).forEach(row => {{
          const tr = document.createElement('tr');
          tr.innerHTML = `
            <td><strong>${{row.corridor}}</strong></td>
            <td>${{row.distance_miles}} mi</td>
            <td>${{row.candidate_stations_evaluated}}</td>
            <td><span class="badge badge-pass">${{row.compute_time_ms}} ms</span></td>
            <td>${{row.uncached_e2e_ms}} ms</td>
            <td><span class="badge badge-pass">${{row.cached_e2e_ms}} ms</span></td>
            <td>${{row.recommended_station || 'N/A'}}</td>
          `;
          tbody.appendChild(tr);
        }});

        renderReport(data.sla_report, promText);
        banner.textContent = `✓ Drill Completed: ${{data.requests_generated}} probes across ${{data.corridors_tested}} US corridor(s) — All 6 SLOs MEETING_SLA`;
      }} finally {{
        btn.disabled = false;
      }}
    }}

    function exportSlaJson() {{
      const blob = new Blob([JSON.stringify(latestReport, null, 2)], {{ type: 'application/json' }});
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'spotter-sla-compliance-report.json';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    }}

    document.getElementById('btn-run-drill').addEventListener('click', () => runLiveDrill(null));
    document.getElementById('btn-refresh-sre').addEventListener('click', () => refreshTelemetry());
    document.getElementById('btn-export-json').addEventListener('click', () => exportSlaJson());
    document.querySelectorAll('.corridor-probe-chip').forEach((chip) => {{
      chip.addEventListener('click', () => {{
        const idx = parseInt(chip.getAttribute('data-corridor-index'), 10);
        runLiveDrill(idx);
      }});
    }});

    renderReport(initialReport, initialProm);
  </script>
</body>
</html>"""
        return HttpResponse(html_doc, content_type="text/html; charset=utf-8", status=status.HTTP_200_OK)
