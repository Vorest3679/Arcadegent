"""Shared connection settings for Supabase-backed repositories."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SupabaseRepositoryConfig:
    """Connection settings for Supabase runtime reads and writes."""

    url: str
    key: str
    timeout_seconds: float = 8.0
