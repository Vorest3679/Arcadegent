"""Arcade host services: the AMap clients and payload mapping built once from ``Settings``.

``container.py`` only connects layers; the arcade-specific host objects are
built here. These host clients read ``Settings.amap_*``; the arcade plugin's
route tool reads its own ``amap`` config from ``plugins.yaml``, whose
placeholders reference the same environment variables (plan 5, section 4.2).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings
from app.services.amap_reverse_geocoder import AMapReverseGeocoder, AMapReverseGeocoderConfig
from app.services.arcade_geo_resolver import ArcadeGeoResolver, ArcadeGeoResolverConfig
from app.services.arcade_payload_mapper import ArcadePayloadMapper


@dataclass(frozen=True)
class ArcadeServices:
    reverse_geocoder: AMapReverseGeocoder
    geo_resolver: ArcadeGeoResolver
    payload_mapper: ArcadePayloadMapper


def build_arcade_services(
    settings: Settings,
    *,
    payload_mapper: ArcadePayloadMapper | None = None,
) -> ArcadeServices:
    """Build the AMap clients and the payload mapper; ``payload_mapper`` replaces the default one."""
    reverse_geocoder = AMapReverseGeocoder(
        config=AMapReverseGeocoderConfig(
            api_key=settings.amap_api_key,
            base_url=settings.amap_base_url,
            timeout_seconds=settings.amap_timeout_seconds,
        )
    )
    geo_resolver = ArcadeGeoResolver(
        config=ArcadeGeoResolverConfig(
            api_key=settings.amap_api_key,
            base_url=settings.amap_base_url,
            cache_path=settings.arcade_geo_cache_path,
            request_timeout_seconds=settings.arcade_geo_request_timeout_seconds,
            sync_limit=settings.arcade_geo_sync_limit,
            max_workers=settings.arcade_geo_max_workers,
        )
    )
    return ArcadeServices(
        reverse_geocoder=reverse_geocoder,
        geo_resolver=geo_resolver,
        payload_mapper=payload_mapper or ArcadePayloadMapper(geo_resolver=geo_resolver),
    )


__all__ = ["ArcadeServices", "build_arcade_services"]
