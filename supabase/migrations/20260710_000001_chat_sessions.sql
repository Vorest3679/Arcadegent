-- Chat session persistence for multi-client ReAct runtime.
-- Stores one row per session; turns and working_memory are kept in jsonb
-- to preserve flexible runtime schemas while allowing indexed queries
-- on session_id, client_id, status, and updated_at.

create table if not exists chat_sessions (
  session_id text primary key,
  client_id text,
  intent text not null default 'search',
  active_subagent text not null default 'main_agent',
  status text not null default 'idle',
  last_error text,
  turn_index int not null default 0,
  previous_response_id text,
  working_memory jsonb not null default '{}'::jsonb,
  turns jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_chat_sessions_client_updated
  on chat_sessions (client_id, updated_at desc);

create index if not exists idx_chat_sessions_status
  on chat_sessions (status);

-- Trigger: keep updated_at fresh on every update.
create or replace function arcadegent_set_chat_session_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists trg_chat_sessions_set_updated_at on chat_sessions;
create trigger trg_chat_sessions_set_updated_at
  before update on chat_sessions
  for each row
  execute function arcadegent_set_chat_session_updated_at();

-- The backend talks to Supabase with the service_role key, so it does
-- not need row-level security enabled. Make sure anonymous users cannot
-- read or write this table directly.
revoke all on chat_sessions from anon, authenticated;
