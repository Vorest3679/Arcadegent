"""HTTP API layer: browser location reverse-geocoding for chat context."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_container
from app.core.container import AppContainer
from app.protocol.messages import ReverseGeocodeRequest, ReverseGeocodeResponse

router = APIRouter(prefix="/api/location", tags=["location"])


@router.post("/reverse-geocode", response_model=ReverseGeocodeResponse)
def reverse_geocode(
    request: ReverseGeocodeRequest,
    container: AppContainer = Depends(get_container),
) -> ReverseGeocodeResponse:
    return container.reverse_geocoder.reverse_geocode(request)
