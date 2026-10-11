-- Read model for the chat session detail API.
-- A full chat_sessions row carries tool results, worker transcripts and
-- worker_runs (megabytes per session) that the detail page never shows. This
-- view returns the same row shape with only what the API projects
-- (backend/app/api/http/chat.py `_to_detail`):
--   * turns: tool results and worker text dropped; `payload` keeps `status`,
--     `map_artifacts` and whether the model call had tool calls.
--   * working_memory: without `worker_runs`.
-- The runtime keeps reading and writing the table itself.

create or replace view chat_session_details as
select
  s.session_id,
  s.client_id,
  s.intent,
  s.active_subagent,
  s.status,
  s.last_error,
  s.turn_index,
  s.previous_response_id,
  s.working_memory - 'worker_runs' as working_memory,
  coalesce(
    (
      select jsonb_agg(
        jsonb_build_object(
          'role', e.t -> 'role',
          'agent', e.t -> 'agent',
          'name', e.t -> 'name',
          'call_id', e.t -> 'call_id',
          'worker_run_id', e.t -> 'worker_run_id',
          'scope', e.t -> 'scope',
          'created_at', e.t -> 'created_at',
          'content', case
            when e.t ->> 'role' = 'user'
              or (e.t ->> 'role' = 'assistant' and coalesce(e.t ->> 'scope', 'conversation') = 'conversation')
            then e.t -> 'content'
            else '""'::jsonb
          end,
          'payload', jsonb_strip_nulls(jsonb_build_object(
            'status', e.t -> 'payload' -> 'status',
            'map_artifacts', e.t -> 'payload' -> 'map_artifacts',
            'model', case
              when jsonb_typeof(e.t -> 'payload' -> 'model') = 'object' then jsonb_build_object(
                'tool_calls',
                coalesce(e.t -> 'payload' -> 'model' -> 'tool_calls', 'null'::jsonb)
                  not in ('null'::jsonb, '[]'::jsonb, '{}'::jsonb, '""'::jsonb, 'false'::jsonb, '0'::jsonb)
              )
            end
          ))
        )
        order by e.i
      )
      from jsonb_array_elements(s.turns) with ordinality as e(t, i)
    ),
    '[]'::jsonb
  ) as turns,
  s.title,
  s.preview,
  s.visible_turn_count,
  s.created_at,
  s.updated_at
from chat_sessions s;

revoke all on chat_session_details from anon, authenticated;
