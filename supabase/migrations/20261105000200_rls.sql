-- Row-level security (TDD §10.3; DATA-1, NEWS-2).
--
-- Every transaction made by the backend sets the acting user first:
--   select set_config('app.user_id', '<uuid>', true);   -- a user's request or job
--   select set_config('app.user_id', '', true);         -- newsroom / system work
-- app_rw is not BYPASSRLS and owns no table, so these policies always apply to it.
-- RLS is enabled on EVERY table (including shared and system ones) so that Supabase's PostgREST
-- roles (anon / authenticated) can never read anything: they get no policies and no grants.

-- ---- per-user tables: user_isolation --------------------------------------------------------------
do $$
declare
  t text;
begin
  foreach t in array array[
    'profiles', 'interests', 'delivery_prefs', 'memory_snapshots', 'memory_signals', 'briefs',
    'candidates', 'source_packs', 'sessions', 'turns', 'open_questions', 'saved_items', 'ratings',
    'experiment_assignments', 'topic_requests'
  ]
  loop
    execute format('alter table %I enable row level security', t);
    execute format('drop policy if exists user_isolation on %I', t);
    execute format($p$
      create policy user_isolation on %I to app_rw
        using (user_id = nullif(current_setting('app.user_id', true), '')::uuid)
        with check (user_id = nullif(current_setting('app.user_id', true), '')::uuid)
    $p$, t);
    execute format('grant select, insert, update, delete on %I to app_rw', t);
  end loop;
end
$$;

-- ---- shared (newsroom) tables: readable and writable by the backend in any context ------------------
do $$
declare
  t text;
begin
  foreach t in array array[
    'headline_observations', 'documents', 'passages', 'stories', 'story_items', 'newsroom_runs',
    'digests', 'digest_entries'
  ]
  loop
    execute format('alter table %I enable row level security', t);
    execute format('drop policy if exists shared_rw on %I', t);
    execute format('create policy shared_rw on %I to app_rw using (true) with check (true)', t);
    execute format('grant select, insert, update, delete on %I to app_rw', t);
  end loop;
end
$$;

-- ---- system tables: only in a system context (app.user_id = '') ------------------------------------
do $$
declare
  t text;
begin
  foreach t in array array['jobs', 'invites', 'admin_audit', 'worker_heartbeats', 'admin_alerts']
  loop
    execute format('alter table %I enable row level security', t);
    execute format('drop policy if exists system_only on %I', t);
    execute format($p$
      create policy system_only on %I to app_rw
        using (nullif(current_setting('app.user_id', true), '') is null)
        with check (nullif(current_setting('app.user_id', true), '') is null)
    $p$, t);
    execute format('grant select, insert, update, delete on %I to app_rw', t);
  end loop;
end
$$;

-- ---- llm_calls: anyone may append; users can't read it; the service (system context) can ------------
alter table llm_calls enable row level security;
drop policy if exists ledger_insert on llm_calls;
create policy ledger_insert on llm_calls for insert to app_rw with check (true);
drop policy if exists ledger_service_read on llm_calls;
create policy ledger_service_read on llm_calls for select to app_rw
  using (nullif(current_setting('app.user_id', true), '') is null);
drop policy if exists ledger_service_update on llm_calls;
create policy ledger_service_update on llm_calls for update to app_rw
  using (nullif(current_setting('app.user_id', true), '') is null)
  with check (nullif(current_setting('app.user_id', true), '') is null);
grant select, insert, update on llm_calls to app_rw;

-- ---- app_admin: read profiles, jobs and llm_calls; manage invites; retry jobs; write the audit -------
drop policy if exists admin_read on profiles;
create policy admin_read on profiles for select to app_admin using (true);
grant select on profiles to app_admin;

drop policy if exists admin_read on jobs;
create policy admin_read on jobs for select to app_admin using (true);
drop policy if exists admin_retry on jobs;
create policy admin_retry on jobs for update to app_admin using (true) with check (true);
grant select, update (status, attempts, run_at, last_error, locked_by, locked_at) on jobs to app_admin;

drop policy if exists admin_read on llm_calls;
create policy admin_read on llm_calls for select to app_admin using (true);
grant select on llm_calls to app_admin;

drop policy if exists admin_all on invites;
create policy admin_all on invites to app_admin using (true) with check (true);
grant select, insert, update, delete on invites to app_admin;

drop policy if exists admin_audit_rw on admin_audit;
create policy admin_audit_rw on admin_audit to app_admin using (true) with check (true);
grant select, insert on admin_audit to app_admin;

drop policy if exists admin_read on admin_alerts;
create policy admin_read on admin_alerts for select to app_admin using (true);
grant select on admin_alerts to app_admin;

grant usage on schema public to app_rw, app_admin;

-- ---- Supabase: the Data API roles get nothing (all access goes through our API) ---------------------
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    execute 'revoke all on all tables in schema public from anon';
    execute 'alter default privileges in schema public revoke all on tables from anon';
  end if;
  if exists (select 1 from pg_roles where rolname = 'authenticated') then
    execute 'revoke all on all tables in schema public from authenticated';
    execute 'alter default privileges in schema public revoke all on tables from authenticated';
  end if;
end
$$;

-- ---- scheduler view of profiles --------------------------------------------------------------------
-- The scheduler runs in a system context and so can't read profiles. This definer function exposes
-- only what scheduling needs (no email, no content), and only to a system context.
create or replace function app_schedule_targets()
returns table (user_id uuid, timezone text, brief_time time, run_id text)
language sql
stable
security definer
set search_path = public
as $$
  select p.user_id, p.timezone, p.brief_time, p.run_id
  from profiles p
  where p.status = 'active'
    and p.type = 'real'
    and p.run_id = 'live'
    and nullif(current_setting('app.user_id', true), '') is null
$$;
revoke all on function app_schedule_targets() from public;
grant execute on function app_schedule_targets() to app_rw;
