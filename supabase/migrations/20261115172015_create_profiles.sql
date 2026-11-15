-- User profiles: one row per Supabase Auth user (docs/DATABASE-DESIGN.md, section 4.1).
--
-- Rows are created ONLY by the on_auth_user_created trigger (and the backfill below).
-- The "authenticated" role can read its own row and update two columns of it; it cannot
-- insert or delete. The backend reaches this table as skillloop_api -> SET LOCAL ROLE
-- authenticated, so RLS is the barrier (the Data API / PostgREST can reach it as well).

create table public.profiles (
  id                 uuid primary key references auth.users (id) on delete cascade,
  display_name       text,
  preferred_language text not null default 'en',
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  constraint profiles_preferred_language_check check (preferred_language in ('en', 'pl')),
  constraint profiles_display_name_check
    check (display_name is null or char_length(display_name) between 1 and 100)
);

alter table public.profiles enable row level security;

-- Explicit REVOKE first (do not rely on default privileges), then the minimum.
revoke all on public.profiles from public, anon, authenticated;
grant select on public.profiles to authenticated;
-- Column-level: id, created_at and updated_at can never be written by a client.
grant update (display_name, preferred_language) on public.profiles to authenticated;

create policy profiles_select_own on public.profiles
  for select to authenticated
  using (id = (select auth.uid()));

create policy profiles_update_own on public.profiles
  for update to authenticated
  using (id = (select auth.uid()))
  with check (id = (select auth.uid()));

-- No INSERT / DELETE policies on purpose.

-- Generic updated_at maintenance, reused by later tables. clock_timestamp() (not now())
-- so that several updates inside one transaction each get their own timestamp.
create function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = clock_timestamp();
  return new;
end;
$$;

revoke execute on function public.set_updated_at() from public, anon, authenticated;

create trigger profiles_set_updated_at
  before update on public.profiles
  for each row execute function public.set_updated_at();

-- Creates the profile when a user registers. SECURITY DEFINER because the inserting
-- role (supabase_auth_admin) has no privileges on public.profiles. It deliberately reads
-- nothing from raw_user_meta_data (client-controlled) and must never raise: a failure
-- here would block sign-up.
create function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (id) values (new.id) on conflict (id) do nothing;
  return new;
end;
$$;

revoke execute on function public.handle_new_user() from public, anon, authenticated;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- Users that existed before this migration.
insert into public.profiles (id)
select id from auth.users
on conflict (id) do nothing;
