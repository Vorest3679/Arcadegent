-- Runtime read RPCs for switching Arcadegent from JSONL to Supabase.

create extension if not exists postgis;

create or replace function arcadegent_valid_lng_lat(
  p_lng double precision,
  p_lat double precision
)
returns boolean
language sql
immutable
as $$
  select p_lng is not null
    and p_lat is not null
    and p_lng between -180 and 180
    and p_lat between -90 and 90;
$$;

create or replace function arcadegent_normalize_region_name(p_value text)
returns text
language plpgsql
immutable
as $$
declare
  v_text text := lower(regexp_replace(btrim(coalesce(p_value, '')), '[[:space:]]+', '', 'g'));
  v_suffix text;
  v_changed boolean;
  v_suffixes text[] := array[
    '特别行政区',
    '自治区',
    '自治州',
    '自治县',
    '地区',
    '省',
    '市',
    '区',
    '县',
    '州',
    '盟'
  ];
begin
  if v_text = '' then
    return '';
  end if;

  loop
    v_changed := false;
    foreach v_suffix in array v_suffixes loop
      if length(v_text) > length(v_suffix) and right(v_text, length(v_suffix)) = v_suffix then
        v_text := left(v_text, length(v_text) - length(v_suffix));
        v_changed := true;
        exit;
      end if;
    end loop;
    exit when not v_changed or v_text = '';
  end loop;

  return v_text;
end;
$$;

create or replace function arcadegent_normalize_title_name(p_value text)
returns text
language plpgsql
immutable
as $$
declare
  v_text text := lower(regexp_replace(btrim(coalesce(p_value, '')), '[[:space:]_\-./]+', '', 'g'));
begin
  if v_text = '' then
    return '';
  end if;
  if position('舞萌' in v_text) > 0 or v_text like 'maimai%' then
    return 'maimai';
  end if;
  if v_text like 'soundvoltex%' or v_text = 'sdvx' then
    return 'sdvx';
  end if;
  return v_text;
end;
$$;

create or replace function arcadegent_refresh_geo_wgs84()
returns trigger
language plpgsql
as $$
begin
  if arcadegent_valid_lng_lat(new.longitude_wgs84, new.latitude_wgs84) then
    new.geo_wgs84 := st_setsrid(
      st_makepoint(new.longitude_wgs84, new.latitude_wgs84),
      4326
    )::geography;
  else
    new.geo_wgs84 := null;
  end if;
  return new;
end;
$$;

drop trigger if exists trg_arcade_shops_refresh_geo_wgs84 on arcade_shops;
create trigger trg_arcade_shops_refresh_geo_wgs84
before insert or update of longitude_wgs84, latitude_wgs84
on arcade_shops
for each row
execute function arcadegent_refresh_geo_wgs84();

update arcade_shops
set geo_wgs84 = case
  when arcadegent_valid_lng_lat(longitude_wgs84, latitude_wgs84)
    then st_setsrid(st_makepoint(longitude_wgs84, latitude_wgs84), 4326)::geography
  else null
end;

create or replace function arcadegent_shop_runtime_json(
  p_shop arcade_shops,
  p_arcades jsonb,
  p_arcade_count integer,
  p_distance_m integer default null
)
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  select (
    to_jsonb(p_shop)
      - 'id'
      - 'geo_wgs84'
      - 'geo_source'
      - 'geo_precision'
      - 'ingest_batch_id'
      - 'created_at'
      - 'updated_at'
      - 'created_at_src'
      - 'updated_at_src'
  )
  || jsonb_build_object(
    'created_at', p_shop.created_at_src,
    'updated_at', p_shop.updated_at_src,
    'arcades', coalesce(p_arcades, '[]'::jsonb),
    'arcade_count', coalesce(p_arcade_count, 0)
  )
  || case
    when p_distance_m is null then '{}'::jsonb
    else jsonb_build_object('distance_m', p_distance_m)
  end;
$$;

create or replace function arcadegent_search_shops(
  p_keyword text default null,
  p_province_code text default null,
  p_city_code text default null,
  p_county_code text default null,
  p_province_name text default null,
  p_city_name text default null,
  p_county_name text default null,
  p_has_arcades boolean default null,
  p_page integer default 1,
  p_page_size integer default 20,
  p_sort_by text default 'default',
  p_sort_order text default 'desc',
  p_sort_title_name text default null,
  p_origin_lng double precision default null,
  p_origin_lat double precision default null,
  p_origin_coord_system text default 'wgs84'
)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_terms text[] := array[]::text[];
  v_page integer := greatest(coalesce(p_page, 1), 1);
  v_page_size integer := least(greatest(coalesce(p_page_size, 20), 1), 100);
  v_offset integer;
  v_sort_by text := lower(coalesce(nullif(btrim(p_sort_by), ''), 'default'));
  v_sort_order text := lower(coalesce(nullif(btrim(p_sort_order), ''), 'desc'));
  v_title_name_norm text := arcadegent_normalize_title_name(p_sort_title_name);
  v_origin_coord_system text := lower(coalesce(nullif(btrim(p_origin_coord_system), ''), 'wgs84'));
  v_has_origin boolean := arcadegent_valid_lng_lat(p_origin_lng, p_origin_lat);
  v_result jsonb;
begin
  if v_sort_by not in ('default', 'updated_at', 'source_id', 'arcade_count', 'title_quantity', 'distance') then
    v_sort_by := 'default';
  end if;
  if v_sort_order not in ('asc', 'desc') then
    v_sort_order := 'desc';
  end if;
  if v_origin_coord_system not in ('wgs84', 'gcj02') then
    v_origin_coord_system := 'wgs84';
  end if;
  v_offset := (v_page - 1) * v_page_size;

  if p_keyword is not null and btrim(p_keyword) <> '' then
    select coalesce(array_agg(distinct term), array[]::text[])
    into v_terms
    from regexp_split_to_table(lower(btrim(p_keyword)), E'[[:space:],.;!?|/\\\\，。；！？、]+') as term
    where btrim(term) <> '';
  end if;

  with title_groups as (
    select
      t.source,
      t.source_id,
      jsonb_agg(
        jsonb_build_object(
          'id', t.arcade_item_id,
          'title_id', t.title_id,
          'title_name', t.title_name,
          'quantity', t.quantity,
          'version', t.version,
          'coin', t.coin,
          'eacoin', t.eacoin,
          'comment', t.comment
        )
        order by t.id
      ) as arcades,
      count(*)::integer as arcade_count,
      coalesce(string_agg(concat_ws(' ', t.title_name, t.version, t.comment), ' '), '') as title_search,
      coalesce(
        sum(coalesce(t.quantity, 0)) filter (
          where v_title_name_norm <> ''
            and arcadegent_normalize_title_name(t.title_name) = v_title_name_norm
        ),
        0
      )::integer as title_quantity
    from arcade_titles t
    group by t.source, t.source_id
  ),
  base as (
    select
      s as shop_row,
      coalesce(t.arcades, '[]'::jsonb) as arcades,
      coalesce(t.arcade_count, 0) as arcade_count,
      coalesce(t.title_search, '') as title_search,
      coalesce(t.title_quantity, 0) as title_quantity
    from arcade_shops s
    left join title_groups t
      on t.source = s.source
     and t.source_id = s.source_id
  ),
  filtered as (
    select
      b.*,
      lower(concat_ws(
        ' ',
        (b.shop_row).name,
        (b.shop_row).name_pinyin,
        (b.shop_row).address,
        (b.shop_row).transport,
        (b.shop_row).comment,
        (b.shop_row).province_name,
        (b.shop_row).city_name,
        (b.shop_row).county_name,
        (b.shop_row).province_code,
        (b.shop_row).city_code,
        (b.shop_row).county_code,
        b.title_search
      )) as search_text
    from base b
    where (p_province_code is null or (b.shop_row).province_code = p_province_code)
      and (p_city_code is null or (b.shop_row).city_code = p_city_code)
      and (p_county_code is null or (b.shop_row).county_code = p_county_code)
      and (
        p_province_name is null
        or arcadegent_normalize_region_name((b.shop_row).province_name) = arcadegent_normalize_region_name(p_province_name)
      )
      and (
        p_city_name is null
        or arcadegent_normalize_region_name((b.shop_row).city_name) = arcadegent_normalize_region_name(p_city_name)
      )
      and (
        p_county_name is null
        or arcadegent_normalize_region_name((b.shop_row).county_name) = arcadegent_normalize_region_name(p_county_name)
      )
      and (
        p_has_arcades is null
        or (p_has_arcades is true and b.arcade_count > 0)
        or (p_has_arcades is false and b.arcade_count = 0)
      )
  ),
  keyword_filtered as (
    select f.*
    from filtered f
    where cardinality(v_terms) = 0
      or not exists (
        select 1
        from unnest(v_terms) as term
        where position(term in f.search_text) = 0
      )
  ),
  scored as (
    select
      k.*,
      case
        when not v_has_origin then null
        when v_origin_coord_system = 'gcj02'
          and arcadegent_valid_lng_lat((k.shop_row).longitude_gcj02, (k.shop_row).latitude_gcj02)
          then round(st_distance(
            st_setsrid(st_makepoint((k.shop_row).longitude_gcj02, (k.shop_row).latitude_gcj02), 4326)::geography,
            st_setsrid(st_makepoint(p_origin_lng, p_origin_lat), 4326)::geography
          ))::integer
        when v_origin_coord_system = 'wgs84'
          and arcadegent_valid_lng_lat((k.shop_row).longitude_wgs84, (k.shop_row).latitude_wgs84)
          then round(st_distance(
            st_setsrid(st_makepoint((k.shop_row).longitude_wgs84, (k.shop_row).latitude_wgs84), 4326)::geography,
            st_setsrid(st_makepoint(p_origin_lng, p_origin_lat), 4326)::geography
          ))::integer
        when arcadegent_valid_lng_lat((k.shop_row).longitude_gcj02, (k.shop_row).latitude_gcj02)
          then round(st_distance(
            st_setsrid(st_makepoint((k.shop_row).longitude_gcj02, (k.shop_row).latitude_gcj02), 4326)::geography,
            st_setsrid(st_makepoint(p_origin_lng, p_origin_lat), 4326)::geography
          ))::integer
        else null
      end as distance_m
    from keyword_filtered k
  ),
  counted as (
    select count(*)::integer as total from scored
  ),
  ranked as (
    select
      scored.*,
      row_number() over (
        order by
          case when v_sort_by = 'distance' then distance_m is null else false end asc,
          case when v_sort_by = 'distance' and v_sort_order = 'asc' then distance_m end asc,
          case when v_sort_by = 'distance' and v_sort_order = 'desc' then distance_m end desc,
          case when v_sort_by = 'title_quantity' and v_sort_order = 'asc' then title_quantity end asc,
          case when v_sort_by = 'title_quantity' and v_sort_order = 'desc' then title_quantity end desc,
          case when v_sort_by = 'arcade_count' and v_sort_order = 'asc' then arcade_count end asc,
          case when v_sort_by = 'arcade_count' and v_sort_order = 'desc' then arcade_count end desc,
          case when v_sort_by = 'updated_at' and v_sort_order = 'asc' then (shop_row).updated_at_src end asc,
          case when v_sort_by = 'updated_at' and v_sort_order = 'desc' then (shop_row).updated_at_src end desc,
          case when v_sort_by = 'source_id' and v_sort_order = 'asc' then (shop_row).source_id end asc,
          case when v_sort_by = 'source_id' and v_sort_order = 'desc' then (shop_row).source_id end desc,
          case when v_sort_by = 'default' then (shop_row).updated_at_src is null else false end asc,
          case when v_sort_by = 'default' then (shop_row).updated_at_src end desc,
          case when v_sort_by = 'default' and (shop_row).updated_at_src is not null then (shop_row).source_id end desc,
          case when v_sort_by = 'default' and (shop_row).updated_at_src is null then (shop_row).source_id end asc,
          case when v_sort_by in ('arcade_count', 'title_quantity') and v_sort_order = 'asc' then (shop_row).updated_at_src end asc,
          case when v_sort_by in ('arcade_count', 'title_quantity') and v_sort_order = 'desc' then (shop_row).updated_at_src end desc,
          case when v_sort_by in ('arcade_count', 'title_quantity') and v_sort_order = 'asc' then (shop_row).source_id end asc,
          case when v_sort_by in ('arcade_count', 'title_quantity') and v_sort_order = 'desc' then (shop_row).source_id end desc,
          (shop_row).source_id asc
      ) as row_order
    from scored
  ),
  paged as (
    select *
    from ranked
    where row_order > v_offset
      and row_order <= v_offset + v_page_size
    order by row_order
  )
  select jsonb_build_object(
    'rows',
    coalesce(
      jsonb_agg(
        arcadegent_shop_runtime_json(
          paged.shop_row,
          paged.arcades,
          paged.arcade_count,
          case when v_sort_by = 'distance' then paged.distance_m else null end
        )
        order by paged.row_order
      ),
      '[]'::jsonb
    ),
    'total',
    (select total from counted)
  )
  into v_result
  from paged;

  return coalesce(v_result, jsonb_build_object('rows', '[]'::jsonb, 'total', 0));
end;
$$;

create or replace function arcadegent_get_shop(p_source_id bigint)
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  with title_groups as (
    select
      t.source,
      t.source_id,
      jsonb_agg(
        jsonb_build_object(
          'id', t.arcade_item_id,
          'title_id', t.title_id,
          'title_name', t.title_name,
          'quantity', t.quantity,
          'version', t.version,
          'coin', t.coin,
          'eacoin', t.eacoin,
          'comment', t.comment
        )
        order by t.id
      ) as arcades,
      count(*)::integer as arcade_count
    from arcade_titles t
    where t.source_id = p_source_id
    group by t.source, t.source_id
  )
  select arcadegent_shop_runtime_json(
    s,
    coalesce(t.arcades, '[]'::jsonb),
    coalesce(t.arcade_count, 0),
    null
  )
  from arcade_shops s
  left join title_groups t
    on t.source = s.source
   and t.source_id = s.source_id
  where s.source_id = p_source_id
  order by s.source asc
  limit 1;
$$;

create or replace function arcadegent_list_regions(
  p_level text,
  p_parent_code text default null
)
returns jsonb
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  v_level text := lower(coalesce(p_level, ''));
  v_result jsonb;
begin
  if v_level = 'province' then
    select coalesce(jsonb_agg(jsonb_build_object('code', code, 'name', name) order by code), '[]'::jsonb)
    into v_result
    from (
      select distinct province_code as code, province_name as name
      from arcade_shops
      where province_code is not null and province_name is not null
    ) q;
    return v_result;
  end if;

  if v_level = 'city' then
    select coalesce(jsonb_agg(jsonb_build_object('code', code, 'name', name) order by code), '[]'::jsonb)
    into v_result
    from (
      select distinct city_code as code, city_name as name
      from arcade_shops
      where province_code = p_parent_code
        and city_code is not null
        and city_name is not null
    ) q;
    return v_result;
  end if;

  if v_level = 'county' then
    select coalesce(jsonb_agg(jsonb_build_object('code', code, 'name', name) order by code), '[]'::jsonb)
    into v_result
    from (
      select distinct county_code as code, county_name as name
      from arcade_shops
      where city_code = p_parent_code
        and county_code is not null
        and county_name is not null
    ) q;
    return v_result;
  end if;

  raise exception 'invalid_region_level:%', p_level;
end;
$$;

create or replace function arcadegent_data_health()
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  select jsonb_build_object(
    'backend', 'supabase',
    'total_lines', count(*)::integer,
    'loaded_rows', count(*)::integer,
    'bad_lines', 0,
    'titles_rows', (select count(*)::integer from arcade_titles),
    'latest_updated_at', max(updated_at_src),
    'latest_ingest_batch_id', (
      select batch_id
      from ingest_runs
      order by created_at desc
      limit 1
    )
  )
  from arcade_shops;
$$;

grant execute on function arcadegent_valid_lng_lat(double precision, double precision)
  to anon, authenticated, service_role;
grant execute on function arcadegent_normalize_region_name(text)
  to anon, authenticated, service_role;
grant execute on function arcadegent_normalize_title_name(text)
  to anon, authenticated, service_role;
grant execute on function arcadegent_shop_runtime_json(arcade_shops, jsonb, integer, integer)
  to anon, authenticated, service_role;
grant execute on function arcadegent_search_shops(
  text, text, text, text, text, text, text, boolean, integer, integer,
  text, text, text, double precision, double precision, text
) to anon, authenticated, service_role;
grant execute on function arcadegent_get_shop(bigint)
  to anon, authenticated, service_role;
grant execute on function arcadegent_list_regions(text, text)
  to anon, authenticated, service_role;
grant execute on function arcadegent_data_health()
  to anon, authenticated, service_role;
