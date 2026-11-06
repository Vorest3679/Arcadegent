-- Pinned chat sessions (per-session UI state).
-- Pins are kept outside `chat_sessions` on purpose: every UPDATE on that table
-- bumps `updated_at` through trg_chat_sessions_set_updated_at, and pinning a
-- chat is not a conversation change. Keeping them apart means pin/unpin never
-- reorders the history list. Deleting a session removes its pin via cascade.
--
-- Apply this BEFORE deploying the backend version that reads/writes pins: the
-- list query and the pin endpoint both hit this table directly.

create table if not exists chat_session_pins (
  session_id text primary key references chat_sessions(session_id) on delete cascade,
  pinned_at timestamptz not null default now()
);

create index if not exists idx_chat_session_pins_pinned_at
  on chat_session_pins (pinned_at desc);

-- The backend uses the service_role key; anonymous users must not read or write.
revoke all on chat_session_pins from anon, authenticated;
