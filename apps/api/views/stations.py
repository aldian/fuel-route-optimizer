"""Station listing and spatial proximity search API endpoint."""
from decimal import Decimal
from typing import Any
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema, OpenApiParameter
from apps.api.serializers.station import FuelStationSerializer
from apps.stations.models import FuelStation
from apps.stations.spatial import StationSpatialIndex


class FuelStationListView(APIView):
    """Lists and searches OPIS US fuel truckstops with optional geographic proximity filter."""

    authentication_classes: list[Any] = []
    permission_classes: list[Any] = []

    @extend_schema(
        summary="Search Fuel Stations",
        description="Filter stations by state, city, or geographic proximity (lat, lng, radius_miles).",
        parameters=[
            OpenApiParameter("state", str, description="2-letter US state code e.g. TX"),
            OpenApiParameter("city", str, description="City name"),
            OpenApiParameter("lat", float, description="Latitude for radius query"),
            OpenApiParameter("lng", float, description="Longitude for radius query"),
            OpenApiParameter("radius_miles", float, description="Radius in miles (default: 25.0)"),
            OpenApiParameter("ordering", str, description="Ordering: retail_price or -retail_price"),
            OpenApiParameter("limit", int, description="Number of results (default: 25, max: 100)"),
            OpenApiParameter("offset", int, description="Offset for pagination"),
        ],
        responses={200: FuelStationSerializer(many=True)},
    )
    def get(self, request: Request) -> Response:
        state = request.query_params.get("state")
        city = request.query_params.get("city")
        lat_str = request.query_params.get("lat")
        lng_str = request.query_params.get("lng")
        radius_str = request.query_params.get("radius_miles", "25.0")
        ordering = request.query_params.get("ordering", "retail_price")
        limit = min(100, max(1, int(request.query_params.get("limit", 25))))
        offset = max(0, int(request.query_params.get("offset", 0)))

        # Proximity Query via Spatial Index
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
            total_count = len(nearby)

            # Sort
            if ordering == "-retail_price":
                nearby.sort(key=lambda item: -float(item[0].retail_price))
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
                {"count": total_count, "limit": limit, "offset": offset, "results": results},
                status=status.HTTP_200_OK,
            )

        # Standard Database Query
        qs = FuelStation.objects.filter(is_active_us=True)
        if state:
            qs = qs.filter(state__iexact=state.strip())
        if city:
            qs = qs.filter(city__icontains=city.strip())

        if ordering in ("retail_price", "-retail_price", "opis_truckstop_id", "-opis_truckstop_id"):
            qs = qs.order_by(ordering)

        total_count = qs.count()
        paginated_qs = qs[offset : offset + limit]
        serializer = FuelStationSerializer(paginated_qs, many=True)

        return Response(
            {"count": total_count, "limit": limit, "offset": offset, "results": serializer.data},
            status=status.HTTP_200_OK,
        )
