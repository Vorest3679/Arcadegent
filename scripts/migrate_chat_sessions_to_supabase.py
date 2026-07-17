"""One-off migration: import legacy local chat_sessions.json into Supabase.

Usage:
    python scripts/migrate_chat_sessions_to_supabase.py [--path data/runtime/chat_sessions.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.agent.runtime.session_state import state_from_dict
from app.core.config import Settings
from app.infra.db.online.config import SupabaseRepositoryConfig
from app.infra.db.online.session_repository import SupabaseSessionStateRepository


def _load_legacy_sessions(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        print(f"Session file not found: {path}")
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        sessions = raw.get("sessions")
        if isinstance(sessions, list):
            return sessions
    if isinstance(raw, list):
        return raw
    print("Unrecognized legacy session file shape.")
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate chat_sessions.json to Supabase")
    parser.add_argument(
        "--path",
        type=Path,
        default=Path("data/runtime/chat_sessions.json"),
        help="Path to legacy chat_sessions.json",
    )
    args = parser.parse_args()

    settings = Settings.from_env()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        print("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set", file=sys.stderr)
        return 1

    repo = SupabaseSessionStateRepository(
        SupabaseRepositoryConfig(
            url=settings.supabase_url,
            key=settings.supabase_service_role_key,
            timeout_seconds=settings.supabase_timeout_seconds,
        )
    )

    raw_sessions = _load_legacy_sessions(args.path)
    migrated = 0
    skipped = 0
    for idx, raw in enumerate(raw_sessions, start=1):
        state = state_from_dict(raw)
        if state is None:
            print(f"Skipping invalid session at index {idx}")
            skipped += 1
            continue
        repo.save(state)
        migrated += 1
        print(f"Migrated session {state.session_id} ({migrated}/{len(raw_sessions)})")

    print(f"Done: migrated={migrated}, skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
