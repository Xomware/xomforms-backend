"""
Tests for lambdas/places_suggest -- address autocomplete proxy.

The route exists so the browser never signs an AWS request, and so an
unauthenticated caller can't turn a paid geocoding API into a free one.
"""

import json
from unittest.mock import patch

RESULTS = {
    "Results": [
        {
            "Place": {
                "Label": "Fenway Park, 4 Jersey St, Boston, MA 02215",
                "AddressNumber": "4",
                "Street": "Jersey St",
                "Municipality": "Boston",
                "Region": "Massachusetts",
                "Geometry": {"Point": [-71.0972, 42.3467]},
            }
        }
    ]
}


class TestPlacesSuggest:
    @patch("lambdas.places_suggest.handler.location")
    def test_returns_suggestions_with_coordinates(self, mock_loc, mock_context, authorized_event):
        from lambdas.places_suggest.handler import handler

        mock_loc.search_place_index_for_text.return_value = RESULTS
        event = authorized_event(
            httpMethod="GET", path="/places/suggest", queryStringParameters={"q": "fenway"}
        )

        body = json.loads(handler(event, mock_context)["body"])
        s = body["suggestions"][0]

        assert s["name"] == "Fenway Park"
        assert "Boston" in s["secondary"]
        # AWS returns [lon, lat]; exposing named fields is what stops the
        # client from swapping them.
        assert s["lat"] == 42.3467
        assert s["lon"] == -71.0972

    @patch("lambdas.places_suggest.handler.location")
    def test_short_queries_never_reach_the_paid_api(
        self, mock_loc, mock_context, authorized_event
    ):
        from lambdas.places_suggest.handler import handler

        event = authorized_event(
            httpMethod="GET", path="/places/suggest", queryStringParameters={"q": "fe"}
        )

        body = json.loads(handler(event, mock_context)["body"])
        assert body["suggestions"] == []
        mock_loc.search_place_index_for_text.assert_not_called()

    @patch("lambdas.places_suggest.handler.location")
    def test_biases_results_when_the_client_knows_where_it_is(
        self, mock_loc, mock_context, authorized_event
    ):
        from lambdas.places_suggest.handler import handler

        mock_loc.search_place_index_for_text.return_value = {"Results": []}
        event = authorized_event(
            httpMethod="GET",
            path="/places/suggest",
            queryStringParameters={"q": "main st", "lat": "42.36", "lon": "-71.05"},
        )

        handler(event, mock_context)
        kwargs = mock_loc.search_place_index_for_text.call_args[1]
        # BiasPosition is [lon, lat] -- the opposite order to the query params.
        assert kwargs["BiasPosition"] == [-71.05, 42.36]

    @patch("lambdas.places_suggest.handler.location")
    def test_rejects_a_non_numeric_bias(self, mock_loc, mock_context, authorized_event):
        from lambdas.places_suggest.handler import handler

        event = authorized_event(
            httpMethod="GET",
            path="/places/suggest",
            queryStringParameters={"q": "main st", "lat": "north", "lon": "-71.05"},
        )
        assert handler(event, mock_context)["statusCode"] == 400

    @patch("lambdas.places_suggest.handler.location")
    def test_requires_authentication(self, mock_loc, mock_context, public_event):
        """Otherwise it's a free geocoding API for whoever finds it."""
        from lambdas.places_suggest.handler import handler

        event = public_event(
            httpMethod="GET", path="/places/suggest", queryStringParameters={"q": "fenway"}
        )

        assert handler(event, mock_context)["statusCode"] == 401
        mock_loc.search_place_index_for_text.assert_not_called()

    @patch("lambdas.places_suggest.handler.location")
    def test_survives_a_result_with_no_geometry(
        self, mock_loc, mock_context, authorized_event
    ):
        from lambdas.places_suggest.handler import handler

        mock_loc.search_place_index_for_text.return_value = {
            "Results": [{"Place": {"Label": "Somewhere"}}]
        }
        event = authorized_event(
            httpMethod="GET", path="/places/suggest", queryStringParameters={"q": "somewhere"}
        )

        body = json.loads(handler(event, mock_context)["body"])
        assert body["suggestions"][0]["lat"] is None
