-- Recall cloud memory: raw episodes in, understood episodes, timeline, threads and digests out.
-- Every table is private per user (row-level security). Devices write through the ingest function
-- as the signed-in user; the understand function writes with the secret key.

create extension if not exists vector with schema extensions;
create extension if not exists pgmq;
create extension if not exists pg_cron;
create extension if not exists pg_net with schema extensions;

create table public.devices (
    user_id uuid not null default auth.uid() references auth.users on delete cascade,
    device_id text not null,
    name text not null default '',
    first_seen timestamptz not null default now(),
    last_seen timestamptz not null default now(),
    primary key (user_id, device_id)
);

-- Redacted episode text as the device sent it; deleted 24 h after it's understood.
create table public.raw_episodes (
    user_id uuid not null default auth.uid() references auth.users on delete cascade,
    episode_id text not null,
    device_id text not null,
    started timestamptz not null,
    ended timestamptz not null,
    text text not null default '',
    received timestamptz not null default now(),
    understood timestamptz,
    primary key (user_id, episode_id)
);

-- Exact app and window spans, by rules (no model).
create table public.timeline_spans (
    user_id uuid not null default auth.uid() references auth.users on delete cascade,
    episode_id text not null,
    device_id text not null,
    app text not null,
    title text not null,
    url text not null default '',
    started timestamptz not null,
    ended timestamptz not null,
    primary key (user_id, device_id, app, title, started)
);
create index timeline_spans_time on public.timeline_spans (user_id, started);

create table public.threads (
    id bigint generated always as identity primary key,
    user_id uuid not null references auth.users on delete cascade,
    title text not null,
    summary text not null default '',
    started timestamptz not null,
    ended timestamptz not null,
    episodes int not null default 1,
    embedding extensions.vector(384)
);
create index threads_user on public.threads (user_id, ended desc);

-- array_to_string is only STABLE, but generated columns need IMMUTABLE; joining text is safe to mark so.
create function public.joined(parts text[]) returns text
language sql immutable parallel safe set search_path = '' as $$ select coalesce(array_to_string(parts, ' '), '') $$;

create table public.episodes (
    user_id uuid not null references auth.users on delete cascade,
    episode_id text not null,
    device_id text not null,
    started timestamptz not null,
    ended timestamptz not null,
    apps text[] not null default '{}',
    worked_on text not null,
    context text not null default '',
    actions text[] not null default '{}',
    important text[] not null default '{}',
    topics text[] not null default '{}',
    people text[] not null default '{}',
    importance int not null default 5,
    continues_previous boolean not null default false,
    evidence text[] not null default '{}',
    thread_id bigint references public.threads on delete set null,
    embedding extensions.vector(384),
    words tsvector generated always as (
        setweight(to_tsvector('english', worked_on), 'A') ||
        setweight(to_tsvector('english', public.joined(topics) || ' ' || public.joined(people)), 'A') ||
        setweight(to_tsvector('english', context), 'B') ||
        setweight(to_tsvector('english', public.joined(actions) || ' ' || public.joined(important)), 'B') ||
        setweight(to_tsvector('english', public.joined(evidence)), 'C')
    ) stored,
    primary key (user_id, episode_id)
);
create index episodes_time on public.episodes (user_id, started);
create index episodes_words on public.episodes using gin (words);
create index episodes_embedding on public.episodes using hnsw (embedding extensions.vector_cosine_ops);

create table public.digests (
    user_id uuid not null references auth.users on delete cascade,
    day date not null,
    summary text not null,
    highlights text[] not null default '{}',
    created timestamptz not null default now(),
    primary key (user_id, day)
);

-- Row-level security: each user sees only their own rows. Devices may add raw episodes, spans and
-- themselves; everything the model writes goes through the secret key and bypasses RLS.
alter table public.devices enable row level security;
alter table public.raw_episodes enable row level security;
alter table public.timeline_spans enable row level security;
alter table public.threads enable row level security;
alter table public.episodes enable row level security;
alter table public.digests enable row level security;

create policy "own devices" on public.devices for all to authenticated
    using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));
create policy "own raw episodes: read" on public.raw_episodes for select to authenticated
    using (user_id = (select auth.uid()));
create policy "own raw episodes: add" on public.raw_episodes for insert to authenticated
    with check (user_id = (select auth.uid()));
create policy "own spans: read" on public.timeline_spans for select to authenticated
    using (user_id = (select auth.uid()));
create policy "own spans: add" on public.timeline_spans for insert to authenticated
    with check (user_id = (select auth.uid()));
create policy "own threads" on public.threads for select to authenticated using (user_id = (select auth.uid()));
create policy "own episodes" on public.episodes for select to authenticated using (user_id = (select auth.uid()));
create policy "own digests" on public.digests for select to authenticated using (user_id = (select auth.uid()));

-- Queue: every new raw episode is a job for the understand function.
select pgmq.create('understand');

create function public.enqueue_understanding() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
    perform pgmq.send('understand', jsonb_build_object('user_id', new.user_id, 'episode_id', new.episode_id));
    return new;
end $$;
create trigger raw_episode_queued after insert on public.raw_episodes
    for each row execute function public.enqueue_understanding();

-- Queue access for the understand function (secret key only). A message read but not deleted
-- comes back after its visibility timeout, so a failed attempt is retried.
create function public.understand_next(visibility_seconds int default 120)
returns table (msg_id bigint, read_ct int, message jsonb)
language sql security definer set search_path = '' as $$
    select msg_id, read_ct, message from pgmq.read('understand', visibility_seconds, 1)
$$;
create function public.understand_done(id bigint) returns boolean
language sql security definer set search_path = '' as $$ select pgmq.delete('understand', id) $$;
revoke execute on function public.understand_next, public.understand_done from public, anon, authenticated;
grant execute on function public.understand_next, public.understand_done to service_role;

-- Nearest threads of a user by meaning (for linking episodes into threads).
create function public.similar_threads(owner uuid, query extensions.vector(384), since timestamptz, k int default 3)
returns table (id bigint, title text, summary text, similarity float)
language sql stable set search_path = '' as $$
    select t.id, t.title, t.summary, 1 - (t.embedding operator(extensions.<=>) query)
    from public.threads t
    where t.user_id = owner and t.ended >= since and t.embedding is not null
    order by t.embedding operator(extensions.<=>) query
    limit k
$$;
revoke execute on function public.similar_threads from public, anon, authenticated;
grant execute on function public.similar_threads to service_role;

-- One search: keywords + meaning + recency + importance, within an optional time range.
-- Runs as the caller, so row-level security limits it to their own episodes.
create function public.search_memory(
    query_text text, query_embedding extensions.vector(384),
    since timestamptz default null, until timestamptz default null, k int default 10
)
returns table (episode_id text, device_id text, started timestamptz, ended timestamptz, apps text[],
               worked_on text, context text, important text[], topics text[], people text[],
               importance int, evidence text[], thread_id bigint, score float)
language sql stable security invoker set search_path = '' as $$
    with candidates as (
        select e.*,
               ts_rank_cd(e.words, websearch_to_tsquery('english', query_text), 32) as keyword,
               1 - (e.embedding operator(extensions.<=>) query_embedding) as meaning
        from public.episodes e
        where (since is null or e.ended >= since) and (until is null or e.started <= until)
    )
    select c.episode_id, c.device_id, c.started, c.ended, c.apps, c.worked_on, c.context, c.important, c.topics,
           c.people, c.importance, c.evidence, c.thread_id,
           0.55 * coalesce(c.meaning, 0) + 0.25 * least(c.keyword * 4, 1)
             + 0.1 * exp(-extract(epoch from now() - c.ended) / 604800.0) + 0.1 * c.importance / 10.0 as score
    from candidates c
    order by score desc
    limit k
$$;
grant execute on function public.search_memory to authenticated;

-- Raw text is kept only until it's understood, plus 24 h.
select cron.schedule('recall-forget-raw', '17 * * * *',
    $$delete from public.raw_episodes where understood < now() - interval '24 hours'$$);
