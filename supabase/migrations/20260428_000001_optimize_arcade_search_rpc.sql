-- Optimize arcade search by paginating shops before aggregating title rows.
-- The previous RPC aggregated all arcade_titles for every shop before ranking,
-- which made small pages pay the cost of building thousands of JSON arrays.

create or replace function arcadegent_search_shops(
  p_keyword text default null,
  p_shop_name text default null,
  p_title_name text default null,
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
  v_shop_name_terms text[] := array[]::text[];
  v_page integer := greatest(coalesce(p_page, 1), 1);
  v_page_size integer := least(greatest(coalesce(p_page_size, 20), 1), 100);
  v_offset integer;
  v_sort_by text := lower(coalesce(nullif(btrim(p_sort_by), ''), 'default'));
  v_sort_order text := lower(coalesce(nullif(btrim(p_sort_order), ''), 'desc'));
  v_title_name_norm text := arcadegent_normalize_title_name(p_sort_title_name);
  v_filter_title_name_norm text := arcadegent_normalize_title_name(p_title_name);
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

  if p_shop_name is not null and btrim(p_shop_name) <> '' then
    select coalesce(array_agg(distinct term), array[]::text[])
    into v_shop_name_terms
    from regexp_split_to_table(lower(btrim(p_shop_name)), E'[[:space:],.;!?|/\\\\，。；！？、]+') as term
    where btrim(term) <> '';
  end if;

  with filtered_shops as (
    select
      s.id as shop_id,
      s.source,
      s.source_id,
      s.updated_at_src,
      s.longitude_gcj02,
      s.latitude_gcj02,
      s.longitude_wgs84,
      s.latitude_wgs84,
      lower(concat_ws(
        ' ',
        s.name,
        s.name_pinyin
      )) as shop_name_search_text,
      lower(concat_ws(
        ' ',
        s.name,
        s.name_pinyin,
        s.address,
        s.transport,
        s.comment,
        s.province_name,
        s.city_name,
        s.county_name,
        s.province_code,
        s.city_code,
        s.county_code
      )) as shop_search_text
    from arcade_shops s
    where (p_province_code is null or s.province_code = p_province_code)
      and (p_city_code is null or s.city_code = p_city_code)
      and (p_county_code is null or s.county_code = p_county_code)
      and (
        p_province_name is null
        or arcadegent_normalize_region_name(s.province_name) = arcadegent_normalize_region_name(p_province_name)
      )
      and (
        p_city_name is null
        or arcadegent_normalize_region_name(s.city_name) = arcadegent_normalize_region_name(p_city_name)
      )
      and (
        p_county_name is null
        or arcadegent_normalize_region_name(s.county_name) = arcadegent_normalize_region_name(p_county_name)
      )
      and (
        p_has_arcades is null
        or (
          p_has_arcades is true
          and exists (
            select 1
            from arcade_titles ht
            where ht.source = s.source
              and ht.source_id = s.source_id
          )
        )
        or (
          p_has_arcades is false
          and not exists (
            select 1
            from arcade_titles ht
            where ht.source = s.source
              and ht.source_id = s.source_id
          )
        )
      )
      and (
        v_filter_title_name_norm = ''
        or exists (
          select 1
          from arcade_titles ft
          where ft.source = s.source
            and ft.source_id = s.source_id
            and arcadegent_normalize_title_name(ft.title_name) = v_filter_title_name_norm
        )
      )
  ),
  keyword_filtered as (
    select fs.*
    from filtered_shops fs
    where (
        cardinality(v_terms) = 0
        or not exists (
          select 1
          from unnest(v_terms) as terms(term)
          where position(terms.term in fs.shop_search_text) = 0
            and not exists (
              select 1
              from arcade_titles kt
              where kt.source = fs.source
                and kt.source_id = fs.source_id
                and position(
                  terms.term in lower(concat_ws(' ', kt.title_name, kt.version, kt.comment))
                ) > 0
            )
          )
      )
      and (
        cardinality(v_shop_name_terms) = 0
        or not exists (
          select 1
          from unnest(v_shop_name_terms) as shop_name_terms(term)
          where position(shop_name_terms.term in fs.shop_name_search_text) = 0
        )
      )
  ),
  scored as (
    select
      k.*,
      case
        when v_sort_by = 'arcade_count' then (
          select count(*)::integer
          from arcade_titles ct
          where ct.source = k.source
            and ct.source_id = k.source_id
        )
        else null
      end as sort_arcade_count,
      case
        when v_sort_by = 'title_quantity' and v_title_name_norm <> '' then (
          select coalesce(sum(coalesce(tq.quantity, 0)), 0)::integer
          from arcade_titles tq
          where tq.source = k.source
            and tq.source_id = k.source_id
            and arcadegent_normalize_title_name(tq.title_name) = v_title_name_norm
        )
        when v_sort_by = 'title_quantity' then 0
        else null
      end as sort_title_quantity,
      case
        when v_sort_by <> 'distance' or not v_has_origin then null
        when v_origin_coord_system = 'gcj02'
          and arcadegent_valid_lng_lat(k.longitude_gcj02, k.latitude_gcj02)
          then round(st_distance(
            st_setsrid(st_makepoint(k.longitude_gcj02, k.latitude_gcj02), 4326)::geography,
            st_setsrid(st_makepoint(p_origin_lng, p_origin_lat), 4326)::geography
          ))::integer
        when v_origin_coord_system = 'wgs84'
          and arcadegent_valid_lng_lat(k.longitude_wgs84, k.latitude_wgs84)
          then round(st_distance(
            st_setsrid(st_makepoint(k.longitude_wgs84, k.latitude_wgs84), 4326)::geography,
            st_setsrid(st_makepoint(p_origin_lng, p_origin_lat), 4326)::geography
          ))::integer
        when arcadegent_valid_lng_lat(k.longitude_gcj02, k.latitude_gcj02)
          then round(st_distance(
            st_setsrid(st_makepoint(k.longitude_gcj02, k.latitude_gcj02), 4326)::geography,
            st_setsrid(st_makepoint(p_origin_lng, p_origin_lat), 4326)::geography
          ))::integer
        else null
      end as distance_m
    from keyword_filtered k
  ),
  ranked as materialized (
    select
      scored.shop_id,
      scored.distance_m,
      (count(*) over ())::integer as total_count,
      row_number() over (
        order by
          case when v_sort_by = 'distance' then distance_m is null else false end asc,
          case when v_sort_by = 'distance' and v_sort_order = 'asc' then distance_m end asc,
          case when v_sort_by = 'distance' and v_sort_order = 'desc' then distance_m end desc,
          case when v_sort_by = 'title_quantity' and v_sort_order = 'asc' then sort_title_quantity end asc,
          case when v_sort_by = 'title_quantity' and v_sort_order = 'desc' then sort_title_quantity end desc,
          case when v_sort_by = 'arcade_count' and v_sort_order = 'asc' then sort_arcade_count end asc,
          case when v_sort_by = 'arcade_count' and v_sort_order = 'desc' then sort_arcade_count end desc,
          case when v_sort_by = 'updated_at' and v_sort_order = 'asc' then updated_at_src end asc,
          case when v_sort_by = 'updated_at' and v_sort_order = 'desc' then updated_at_src end desc,
          case when v_sort_by = 'source_id' and v_sort_order = 'asc' then source_id end asc,
          case when v_sort_by = 'source_id' and v_sort_order = 'desc' then source_id end desc,
          case when v_sort_by = 'default' then updated_at_src is null else false end asc,
          case when v_sort_by = 'default' then updated_at_src end desc,
          case when v_sort_by = 'default' and updated_at_src is not null then source_id end desc,
          case when v_sort_by = 'default' and updated_at_src is null then source_id end asc,
          case when v_sort_by in ('arcade_count', 'title_quantity') and v_sort_order = 'asc' then updated_at_src end asc,
          case when v_sort_by in ('arcade_count', 'title_quantity') and v_sort_order = 'desc' then updated_at_src end desc,
          case when v_sort_by in ('arcade_count', 'title_quantity') and v_sort_order = 'asc' then source_id end asc,
          case when v_sort_by in ('arcade_count', 'title_quantity') and v_sort_order = 'desc' then source_id end desc,
          source_id asc
      ) as row_order
    from scored
  ),
  paged as (
    select *
    from ranked
    where row_order > v_offset
      and row_order <= v_offset + v_page_size
    order by row_order
  ),
  paged_shops as (
    select
      p.row_order,
      p.total_count,
      p.distance_m,
      s as shop_row
    from paged p
    join arcade_shops s
      on s.id = p.shop_id
  ),
  paged_with_titles as (
    select
      p.row_order,
      p.total_count,
      p.shop_row,
      p.distance_m,
      coalesce(
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
        ) filter (where t.id is not null),
        '[]'::jsonb
      ) as arcades,
      count(t.id)::integer as arcade_count
    from paged_shops p
    left join arcade_titles t
      on t.source = (p.shop_row).source
     and t.source_id = (p.shop_row).source_id
    group by p.row_order, p.total_count, p.shop_row, p.distance_m
  )
  select jsonb_build_object(
    'rows',
    coalesce(
      jsonb_agg(
        arcadegent_shop_runtime_json(
          paged_with_titles.shop_row,
          paged_with_titles.arcades,
          paged_with_titles.arcade_count,
          case when v_sort_by = 'distance' then paged_with_titles.distance_m else null end
        )
        order by paged_with_titles.row_order
      ),
      '[]'::jsonb
    ),
    'total',
    coalesce(
      (select max(total_count) from ranked),
      0
    )
  )
  into v_result
  from paged_with_titles;

  return coalesce(v_result, jsonb_build_object('rows', '[]'::jsonb, 'total', 0));
end;
$$;

grant execute on function arcadegent_search_shops(
  text, text, text, text, text, text, text, text, text, boolean, integer, integer,
  text, text, text, double precision, double precision, text
) to anon, authenticated, service_role;
