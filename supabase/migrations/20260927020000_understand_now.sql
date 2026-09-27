-- Understanding starts as soon as a batch arrives, not at the next minute's cron run (the cron stays
-- as the retry path). One call per ingest batch (statement trigger), so a backlog uploaded at once
-- doesn't start many runs; nothing new in the batch, no call. It never blocks ingest: the call is
-- queued by pg_net, and any error is only a warning.
create function public.understand_now() returns trigger
language plpgsql security definer set search_path = '' as $$
declare
    project_url text := (select decrypted_secret from vault.decrypted_secrets where name = 'recall_project_url');
    secret_key text := (select decrypted_secret from vault.decrypted_secrets where name = 'recall_secret_key');
begin
    if project_url is not null and secret_key is not null and exists (select 1 from inserted) then
        perform net.http_post(
            url := project_url || '/functions/v1/understand',
            headers := jsonb_build_object('apikey', secret_key, 'Content-Type', 'application/json'),
            body := '{}'::jsonb,
            timeout_milliseconds := 90000);
    end if;
    return null;
exception when others then
    raise warning 'understand_now: %', sqlerrm;
    return null;
end $$;
revoke execute on function public.understand_now from public, anon, authenticated;
create trigger raw_episodes_understand_now after insert on public.raw_episodes
    referencing new table as inserted
    for each statement execute function public.understand_now();
