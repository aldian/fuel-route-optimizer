"""Station listing, price filtering, and spatial proximity search API endpoint."""
from decimal import Decimal, InvalidOperation
from typing import Any
from django.db.models import Avg, Max, Min, Q
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.api.serializers.station import FuelStationSerializer
from apps.stations.models import FuelStation
from apps.stations.spatial import StationSpatialIndex


class FuelStationListView(APIView):
    """Lists and searches OPIS US fuel truckstops with optional geographic proximity and price filters."""

    authentication_classes: list[Any] = []
    permission_classes: list[Any] = []

    @extend_schema(
        summary="Search & Filter US Fuel Stations",
        description=(
            "Filter stations by state, city, name/address search, retail price range (min_price, max_price), "
            "or geographic proximity (lat, lng, radius_miles). Includes aggregate price_summary statistics."
        ),
        parameters=[
            OpenApiParameter("state", str, description="2-letter US state code e.g. TX"),
            OpenApiParameter("city", str, description="City name substring"),
            OpenApiParameter("search", str, description="Search truckstop name, address, or city"),
            OpenApiParameter("min_price", float, description="Minimum retail price per gallon (USD)"),
            OpenApiParameter("max_price", float, description="Maximum retail price per gallon (USD)"),
            OpenApiParameter("lat", float, description="Latitude for 3D cKDTree radius query"),
            OpenApiParameter("lng", float, description="Longitude for 3D cKDTree radius query"),
            OpenApiParameter("radius_miles", float, description="Radius in miles (default: 25.0)"),
            OpenApiParameter(
                "ordering",
                str,
                description="Ordering: retail_price, -retail_price, distance, opis_truckstop_id, or -opis_truckstop_id",
            ),
            OpenApiParameter("limit", int, description="Number of results (default: 25, max: 100)"),
            OpenApiParameter("offset", int, description="Offset for pagination"),
        ],
        responses={200: FuelStationSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        state = request.query_params.get("state")
        city = request.query_params.get("city")
        search_q = request.query_params.get("search")
        min_price_str = request.query_params.get("min_price")
        max_price_str = request.query_params.get("max_price")
        lat_str = request.query_params.get("lat")
        lng_str = request.query_params.get("lng")
        radius_str = request.query_params.get("radius_miles", "25.0")
        ordering = request.query_params.get("ordering", "retail_price")

        try:
            limit = min(100, max(1, int(request.query_params.get("limit", 25))))
            offset = max(0, int(request.query_params.get("offset", 0)))
        except ValueError:
            limit, offset = 25, 0

        min_price: Decimal | None = None
        max_price: Decimal | None = None
        try:
            if min_price_str not in (None, ""):
                min_price = Decimal(str(min_price_str))
            if max_price_str not in (None, ""):
                max_price = Decimal(str(max_price_str))
        except InvalidOperation:
            return Response(
                {"error": {"code": "INVALID_PRICE_FILTER", "message": "Invalid min_price or max_price parameter."}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Proximity Query via 3D ECEF cKDTree Spatial Index
        if lat_str and lng_str:
            try:
                lat = float(lat_str)
                lng = float(lng_str)
                radius_miles = float(radius_str)
            except ValueError:
                return Response(
                    {"error": {"code": "INVALID_COORDINATES", "message": "Invalid lat, lng or radius_miles."}},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            spatial_idx = StationSpatialIndex.get_instance()
            nearby = spatial_idx.query_radius(lat, lng, radius_miles=radius_miles)

            if state:
                st_norm = state.strip().upper()
                nearby = [(st, d) for st, d in nearby if st.state.upper() == st_norm]
            if city:
                c_norm = city.strip().lower()
                nearby = [(st, d) for st, d in nearby if c_norm in st.city.lower()]
            if search_q:
                q_norm = search_q.strip().lower()
                nearby = [
                    (st, d)
                    for st, d
                    in nearby
                    if q_norm in st.truckstop_name.lower() or q_norm in st.address.lower() or q_norm in st.city.lower()
                ]
            if min_price is not None:
                nearby = [(st, d) for st, d in nearby if st.retail_price >= min_price]
            if max_price is not None:
                nearby = [(st, d) for st, d in nearby if st.retail_price <= max_price]

            total_count = len(nearby)
            if total_count > 0:
                prices = [float(st.retail_price) for st, _ in nearby]
                price_summary = {
                    "min_retail_price_usd": f"{min(prices):.3f}",
                    "avg_retail_price_usd": f"{(sum(prices) / total_count):.3f}",
                    "max_retail_price_usd": f"{max(prices):.3f}",
                }
            else:
                price_summary = {
                    "min_retail_price_usd": None,
                    "avg_retail_price_usd": None,
                    "max_retail_price_usd": None,
                }

            # Sort
            if ordering == "-retail_price":
                nearby.sort(key=lambda item: -float(item[0].retail_price))
            elif ordering == "distance":
                nearby.sort(key=lambda item: item[1])
            elif ordering == "retail_price":
                nearby.sort(key=lambda item: float(item[0].retail_price))

            paginated = nearby[offset : offset + limit]
            results = [
                {
                    "opis_truckstop_id": st.opis_truckstop_id,
                    "truckstop_name": st.truckstop_name,
                    "address": st.address,
                    "city": st.city,
                    "state": st.state,
                    "country_code": "US",
                    "rack_id": st.rack_id,
                    "retail_price": str(st.retail_price),
                    "latitude": st.latitude,
                    "longitude": st.longitude,
                    "distance_miles": round(dist, 2),
                    "geocode_precision": st.geocode_precision,
                }
                for st, dist in paginated
            ]

            return Response(
                {
                    "count": total_count,
                    "limit": limit,
                    "offset": offset,
                    "price_summary": price_summary,
                    "results": results,
                },
                status=status.HTTP_200_OK,
            )

        # Standard Database Query
        qs = FuelStation.objects.filter(is_active_us=True)
        if state:
            qs = qs.filter(state__iexact=state.strip())
        if city:
            qs = qs.filter(city__icontains=city.strip())
        if search_q:
            q_clean = search_q.strip()
            qs = qs.filter(
                Q(truckstop_name__icontains=q_clean)
                | Q(address__icontains=q_clean)
                | Q(city__icontains=q_clean)
            )
        if min_price is not None:
            qs = qs.filter(retail_price__gte=min_price)
        if max_price is not None:
            qs = qs.filter(retail_price__lte=max_price)

        if ordering in ("retail_price", "-retail_price", "opis_truckstop_id", "-opis_truckstop_id"):
            qs = qs.order_by(ordering)

        agg = qs.aggregate(
            min_p=Min("retail_price"),
            avg_p=Avg("retail_price"),
            max_p=Max("retail_price"),
        )
        price_summary = {
            "min_retail_price_usd": f"{agg['min_p']:.3f}" if agg["min_p"] is not None else None,
            "avg_retail_price_usd": f"{agg['avg_p']:.3f}" if agg["avg_p"] is not None else None,
            "max_retail_price_usd": f"{agg['max_p']:.3f}" if agg["max_p"] is not None else None,
        }

        total_count = qs.count()
        paginated_qs = qs[offset : offset + limit]
        serializer = FuelStationSerializer(paginated_qs, many=True)

        return Response(
            {
                "count": total_count,
                "limit": limit,
                "offset": offset,
                "price_summary": price_summary,
                "results": serializer.data,
            },
            status=status.HTTP_200_OK,
        )
