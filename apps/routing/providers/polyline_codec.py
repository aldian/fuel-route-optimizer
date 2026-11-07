"""Fast, zero-dependency polyline decoder and encoder supporting precision 5 and 6."""


def decode_polyline(polyline_str: str, precision: int = 6) -> list[tuple[float, float]]:
    """
    Decodes an encoded polyline string into a list of (latitude, longitude) tuples.
    Default precision is 6 (1e-6 degrees, standard for OSRM / Valhalla / Mapbox).
    """
    factor = 10.0 ** precision
    coordinates: list[tuple[float, float]] = []
    index = 0
    length = len(polyline_str)
    lat = 0
    lng = 0

    while index < length:
        # Decode Latitude
        shift = 0
        result = 0
        while True:
            byte = ord(polyline_str[index]) - 63
            index += 1
            result |= (byte & 0x1F) << shift
            shift += 5
            if byte < 0x20:
                break
        dlat = ~(result >> 1) if (result & 1) else (result >> 1)
        lat += dlat

        # Decode Longitude
        shift = 0
        result = 0
        while True:
            byte = ord(polyline_str[index]) - 63
            index += 1
            result |= (byte & 0x1F) << shift
            shift += 5
            if byte < 0x20:
                break
        dlng = ~(result >> 1) if (result & 1) else (result >> 1)
        lng += dlng

        coordinates.append((lat / factor, lng / factor))

    return coordinates


def encode_polyline(coordinates: list[tuple[float, float]], precision: int = 6) -> str:
    """
    Encodes a list of (latitude, longitude) tuples into a polyline string.
    Default precision is 6.
    """
    factor = 10.0 ** precision
    output: list[str] = []
    prev_lat = 0
    prev_lng = 0

    for lat, lng in coordinates:
        curr_lat = int(round(lat * factor))
        curr_lng = int(round(lng * factor))

        dlat = curr_lat - prev_lat
        dlng = curr_lng - prev_lng

        prev_lat = curr_lat
        prev_lng = curr_lng

        for val in (dlat, dlng):
            val = ~(val << 1) if val < 0 else (val << 1)
            while val >= 0x20:
                output.append(chr((0x20 | (val & 0x1F)) + 63))
                val >>= 5
            output.append(chr(val + 63))

    return "".join(output)
