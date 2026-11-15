-- Career goals (docs/DATABASE-DESIGN.md, section 4.2).
--
-- Rules enforced here, not in the application:
--   * at most ONE active goal per user (partial unique index; violation = SQLSTATE 23505,
--     which the backend maps to HTTP 409). This also makes concurrent creation safe.
--   * an archived goal is immutable and can never become active again (decision D13);
--   * "authenticated" can never delete a goal (history is preserved).
-- The backend reaches this table as skillloop_api -> SET LOCAL ROLE authenticated; the
-- Data API (PostgREST) can reach it as well, so RLS and column grants are the barrier.

create table public.career_goals (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users (id) on delete cascade,
  target_role text not null,
  seniority   text not null,
  description text,
  status      text not null default 'active',
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  constraint career_goals_target_role_check
    check (char_length(btrim(target_role)) between 1 and 100),
  constraint career_goals_seniority_check check (seniority in ('junior', 'mid', 'senior')),
  constraint career_goals_description_check
    check (description is null or char_length(description) <= 2000),
  constraint career_goals_status_check check (status in ('active', 'archived'))
);

create unique index one_active_goal_per_user
  on public.career_goals (user_id)
  where status = 'active';

create index career_goals_user_id_status_idx
  on public.career_goals (user_id, status);

alter table public.career_goals enable row level security;

-- Explicit REVOKE first (do not rely on default privileges), then the minimum.
revoke all on public.career_goals from public, anon, authenticated;
grant select on public.career_goals to authenticated;
-- status is not insertable: a new goal is always created as 'active' (column default).
grant insert (user_id, target_role, seniority, description) on public.career_goals to authenticated;
-- id, user_id, created_at and updated_at can never be written by a client.
grant update (target_role, seniority, description, status) on public.career_goals to authenticated;
-- No DELETE grant.

create policy career_goals_select_own on public.career_goals
  for select to authenticated
  using (user_id = (select auth.uid()));

create policy career_goals_insert_own on public.career_goals
  for insert to authenticated
  with check (user_id = (select auth.uid()) and status = 'active');

-- USING is evaluated against the EXISTING row: only the owner's ACTIVE goal can be updated,
-- so an archived goal is invisible to UPDATE (0 rows) and can never be restored.
-- WITH CHECK sees only the NEW row (it cannot compare with the old one); together with the
-- status CHECK constraint it allows active -> archived and keeps the row owned by the user.
create policy career_goals_update_own on public.career_goals
  for update to authenticated
  using (user_id = (select auth.uid()) and status = 'active')
  with check (user_id = (select auth.uid()));

-- No DELETE policy on purpose.

create trigger career_goals_set_updated_at
  before update on public.career_goals
  for each row execute function public.set_updated_at();
