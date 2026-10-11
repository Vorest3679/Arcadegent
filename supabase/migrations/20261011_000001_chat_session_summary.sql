-- Summary columns for the chat history list.
-- The list endpoint used to read every session's full `turns` (megabytes) just to
-- show a title, a preview and a message count. These columns are filled by the
-- backend on every save (backend/app/services/chat_session_summary.py); this file
-- adds them and backfills existing rows with the same rules.
--
-- Apply this BEFORE deploying the backend version that writes them: its upsert
-- sends these columns and PostgREST rejects unknown ones.

alter table chat_sessions
  add column if not exists title text,
  add column if not exists preview text,
  add column if not exists visible_turn_count int;

-- Whitespace-collapsed single line, cut to max_len with "..." (as `single_line` in Python).
create or replace function arcadegent_compact_text(raw text, max_len int)
returns text
language sql
immutable
as $$
  select case
    when length(c) <= max_len then c
    else rtrim(left(c, greatest(1, max_len - 3))) || '...'
  end
  from (select btrim(regexp_replace(coalesce(raw, ''), '\s+', ' ', 'g')) as c) t
$$;

-- Backfill without touching updated_at (the trigger would reorder the list).
alter table chat_sessions disable trigger trg_chat_sessions_set_updated_at;

update chat_sessions s
set
  title = coalesce(
    (
      select arcadegent_compact_text(e.t ->> 'content', 32)
      from jsonb_array_elements(s.turns) with ordinality as e(t, i)
      where e.t ->> 'role' = 'user'
        and arcadegent_compact_text(e.t ->> 'content', 32) <> ''
      order by e.i
      limit 1
    ),
    'New chat'
  ),
  preview = (
    select arcadegent_compact_text(e.t ->> 'content', 72)
    from jsonb_array_elements(s.turns) with ordinality as e(t, i)
    where (
        e.t ->> 'role' = 'user'
        or (
          e.t ->> 'role' = 'assistant'
          and coalesce(e.t ->> 'scope', 'conversation') = 'conversation'
          and jsonb_typeof(e.t -> 'payload' -> 'model') is distinct from 'object'
        )
      )
      and arcadegent_compact_text(e.t ->> 'content', 72) <> ''
    order by e.i desc
    limit 1
  ),
  visible_turn_count = (
    select count(*)
    from jsonb_array_elements(s.turns) as e(t)
    where e.t ->> 'role' = 'user'
      or (
        e.t ->> 'role' = 'assistant'
        and coalesce(e.t ->> 'scope', 'conversation') = 'conversation'
        and jsonb_typeof(e.t -> 'payload' -> 'model') is distinct from 'object'
      )
  )
where s.title is null;

alter table chat_sessions enable trigger trg_chat_sessions_set_updated_at;
