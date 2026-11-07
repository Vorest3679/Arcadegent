-- Compact arcade search index for the history sidebar.
-- The list reads only summary columns (see 20261011), so history search cannot
-- inspect `turns` without loading megabytes per session. This column keeps the
-- deduplicated arcade names and pinyin found in each session's
-- `payload.map_artifacts`, which is all the client needs to match an arcade
-- name or a pinyin query alongside title/preview.
--
-- Apply this BEFORE deploying the backend version that reads/writes it: the
-- list and pin queries select the new column, and session upserts write it.

alter table chat_sessions
  add column if not exists artifact_shops jsonb not null default '[]'::jsonb;

-- Backfill without changing updated_at (the trigger would reorder history).
alter table chat_sessions disable trigger trg_chat_sessions_set_updated_at;

update chat_sessions s
set artifact_shops = coalesce(
  (
    select jsonb_agg(
      jsonb_build_object('name', picked.name, 'name_pinyin', picked.name_pinyin)
      order by picked.name
    )
    from (
      -- Deduplicate case-insensitively and prefer a row that carries pinyin,
      -- mirroring build_artifact_shops()'s merge rule.
      select distinct on (lower(btrim(names.name)))
        btrim(names.name) as name,
        nullif(btrim(names.name_pinyin), '') as name_pinyin
      from (
        select
          shop.shop ->> 'name' as name,
          shop.shop ->> 'name_pinyin' as name_pinyin
        from jsonb_array_elements(
          case when jsonb_typeof(s.turns) = 'array' then s.turns else '[]'::jsonb end
        ) as t(turn)
        cross join lateral jsonb_array_elements(
          case
            when jsonb_typeof(t.turn -> 'payload' -> 'map_artifacts' -> 'shops') = 'array'
              then t.turn -> 'payload' -> 'map_artifacts' -> 'shops'
            else '[]'::jsonb
          end
        ) as shop(shop)
        union all
        select
          t.turn -> 'payload' -> 'map_artifacts' -> 'destination' ->> 'name',
          t.turn -> 'payload' -> 'map_artifacts' -> 'destination' ->> 'name_pinyin'
        from jsonb_array_elements(
          case when jsonb_typeof(s.turns) = 'array' then s.turns else '[]'::jsonb end
        ) as t(turn)
        where jsonb_typeof(t.turn -> 'payload' -> 'map_artifacts' -> 'destination') = 'object'
      ) as names
      where coalesce(btrim(names.name), '') <> ''
      order by lower(btrim(names.name)), nullif(btrim(names.name_pinyin), '') desc nulls last
      limit 200
    ) as picked
  ),
  '[]'::jsonb
)
where s.artifact_shops = '[]'::jsonb;

alter table chat_sessions enable trigger trg_chat_sessions_set_updated_at;
