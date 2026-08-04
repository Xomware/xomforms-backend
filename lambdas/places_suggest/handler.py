"""
GET /places/suggest -- Address autocomplete for in-person events (authed).

Proxies Amazon Location rather than letting the browser call it directly:
signing an AWS request client-side would mean shipping credentials, and an
unauthenticated proxy would be a free geocoding API for anyone who finds it.

Uses SearchPlaceIndexForText (not ...ForSuggestions) because it returns the
formatted label AND coordinates in a single call. Suggestions would return
labels only, forcing a second GetPlace round trip per selection.
"""

import os

import boto3

from lambdas.common.logger import get_logger
from lambdas.common.errors import handle_errors, ValidationError
from lambdas.common.utility_helpers import (
    success_response,
    get_caller_email,
    get_query_params,
)

log = get_logger(__file__)

HANDLER = "places_suggest"

PLACE_INDEX_NAME = os.environ.get("PLACE_INDEX_NAME", "xomforms-places")
MAX_RESULTS = 5
MIN_QUERY_LENGTH = 3

location = boto3.client("location", region_name="us-east-1")


def _label_parts(place: dict) -> dict:
    """
    Split the result into a short name and the rest of the address.

    A venue usually has both ("Fenway Park" + "4 Jersey St, Boston, MA"), and
    showing the label whole makes every suggestion look the same at a glance.
    """
    label = place.get("Label") or ""
    name = place.get("Label", "").split(",")[0].strip()
    # AWS returns a Municipality/Region breakdown; prefer the explicit fields
    # when present so the secondary line reads as an address, not a fragment.
    parts = [
        place.get("AddressNumber"),
        place.get("Street"),
        place.get("Municipality"),
        place.get("Region"),
        place.get("PostalCode"),
    ]
    street = " ".join(p for p in [place.get("AddressNumber"), place.get("Street")] if p)
    tail = ", ".join(p for p in [street, place.get("Municipality"), place.get("Region")] if p)
    return {
        "label": label,
        "name": name,
        "secondary": tail or label,
        "hasParts": any(parts),
    }


@handle_errors(HANDLER)
def handler(event, context):
    # Authed: this is a paid upstream API, not something to leave open.
    get_caller_email(event)

    params = get_query_params(event)
    query = (params.get("q") or "").strip()

    # Below three characters every query matches half the planet and the call
    # is wasted spend, so short-circuit rather than round-tripping.
    if len(query) < MIN_QUERY_LENGTH:
        return success_response({"suggestions": []})

    kwargs = {
        "IndexName": PLACE_INDEX_NAME,
        "Text": query,
        "MaxResults": MAX_RESULTS,
    }
    # Bias toward the user when the client knows roughly where they are --
    # "Main St" is otherwise a global search.
    bias_lon = params.get("lon")
    bias_lat = params.get("lat")
    if bias_lat and bias_lon:
        try:
            kwargs["BiasPosition"] = [float(bias_lon), float(bias_lat)]
        except ValueError:
            raise ValidationError(
                message="lat/lon must be numbers", function="handler", field="lat"
            )

    res = location.search_place_index_for_text(**kwargs)

    suggestions = []
    for item in res.get("Results", []):
        place = item.get("Place") or {}
        point = (place.get("Geometry") or {}).get("Point") or []
        parts = _label_parts(place)
        suggestions.append(
            {
                "label": parts["label"],
                "name": parts["name"],
                "secondary": parts["secondary"],
                # [lon, lat] from AWS; exposed as named fields so the client
                # can't get the order wrong.
                "lon": point[0] if len(point) == 2 else None,
                "lat": point[1] if len(point) == 2 else None,
            }
        )

    log.info(f"Place suggestions for '{query[:40]}': {len(suggestions)}")
    return success_response({"suggestions": suggestions})
