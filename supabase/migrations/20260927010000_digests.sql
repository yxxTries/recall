-- Daily digests: each user's day, summarized once it's over (after 3 am in their timezone).

alter table public.devices add column utc_offset_minutes int not null default 0;

-- Users whose yesterday (in their latest device's timezone) has episodes but no digest yet.
create function public.digests_due(k int default 3)
returns table (user_id uuid, day date, utc_offset_minutes int, day_start timestamptz)
language sql stable security definer set search_path = '' as $$
    with owners as (
        select d.user_id, (array_agg(d.utc_offset_minutes order by d.last_seen desc))[1] as off
        from public.devices d group by d.user_id
    ), local as (
        select o.user_id, o.off, (now() at time zone 'UTC' + make_interval(mins => o.off)) as local_now
        from owners o
    ), due as (
        select l.user_id, l.local_now::date - 1 as day, l.off,
               ((l.local_now::date - 1)::timestamp - make_interval(mins => l.off)) at time zone 'UTC' as day_start
        from local l
        where extract(hour from l.local_now) >= 3
    )
    select d.user_id, d.day, d.off, d.day_start from due d
    where not exists (select 1 from public.digests g where g.user_id = d.user_id and g.day = d.day)
      and exists (select 1 from public.episodes e where e.user_id = d.user_id
                  and e.started >= d.day_start and e.started < d.day_start + interval '1 day')
    limit k
$$;
revoke execute on function public.digests_due from public, anon, authenticated;
grant execute on function public.digests_due to service_role;
