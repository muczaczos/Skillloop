-- Competencies of a career goal (docs/DATABASE-DESIGN.md, sections 4.3, 4.3.1, 4.8 and 6.2.1).
--
-- A competency belongs to exactly one career goal and to the owner of that goal. There is no
-- global catalogue. Rules enforced here, in the database, not only in the application:
--   * (career_goal_id, user_id) is a composite foreign key to career_goals (id, user_id), so
--     user_id is provably the owner of the goal (and RLS needs no join);
--   * the name of an ACTIVE competency is unique within its goal, ignoring case and
--     surrounding whitespace (partial unique index);
--   * at most 50 ACTIVE competencies per goal;
--   * an archived competency is immutable and never becomes active again;
--   * an archived goal freezes its competencies (decision D13, extended);
--   * "authenticated" can never delete a competency (no DELETE grant, no policy).
--
-- WRITE SYNCHRONISATION (design V3, section 4.8). The limit and the freezing cannot be CHECK
-- constraints, and "count, then insert" races under READ COMMITTED. Every write therefore takes
-- a row lock on the GOAL row (FOR NO KEY UPDATE) before anything else, and keeps it until the
-- transaction ends:
--   * competencies_lock_goal (BEFORE INSERT OR UPDATE, FOR EACH STATEMENT) locks the caller's
--     active goal before any competency row is read or locked, so the lock order is always
--     "goal first, then competency rows" and no lock cycle can form;
--   * competencies_enforce_goal_rules (BEFORE INSERT OR UPDATE, FOR EACH ROW) locks the target
--     goal again (a no-op when already held), refuses a goal that is not active, and on INSERT
--     enforces the limit. It runs after the lock, so its count sees every committed competency.
-- Both functions are SECURITY INVOKER (they run as "authenticated"), VOLATILE (required: only a
-- volatile function gets a fresh snapshot per query), and have an empty search_path.
--
-- MANDATORY ARCHITECTURE CONTRACT (rule L2). A privileged role (postgres, service_role) has no
-- auth.uid(), so the statement trigger does nothing for it. EVERY write to competencies by such a
-- role (INSERT, UPDATE and DELETE; data migrations, backfills, scripts, jobs) MUST first lock the
-- career goal in the same transaction:
--     SELECT 1 FROM public.career_goals WHERE id = <goal id> FOR NO KEY UPDATE;
-- A write spanning several goals locks all of them first, in ascending id order. The database does
-- not enforce this; the row trigger still enforces goal status and the limit for every role, so a
-- broken contract cannot corrupt data, it only loses deadlock freedom. A migration or script that
-- writes to competencies must say in its header that it follows this contract. This migration
-- writes no competency rows.
--
-- The triggers fire for INSERT and UPDATE only. DELETE is not guarded: clients have no DELETE
-- grant, and rows disappear only through ON DELETE CASCADE from career_goals (auth.users ->
-- career_goals -> competencies; not an approved deletion policy, DATABASE-DESIGN.md section 8).
--
-- There is no separate foreign key to auth.users: the composite key already guarantees that the
-- user exists. current_level, source, expectations and any hierarchy are deliberately absent.

-- Target of the composite foreign key below. Redundant with the primary key on (id), but
-- PostgreSQL requires a unique constraint on exactly the referenced columns.
alter table public.career_goals
  add constraint career_goals_id_user_id_key unique (id, user_id);

create table public.competencies (
  id             uuid primary key default gen_random_uuid(),
  career_goal_id uuid not null,
  user_id        uuid not null,
  name           text not null,
  description    text,
  rationale      text,
  category       text,
  knowledge_type text not null,
  importance     text not null,
  expected_level text not null,
  display_order  integer not null default 0,
  status         text not null default 'active',
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  constraint competencies_goal_owner_fkey
    foreign key (career_goal_id, user_id)
    references public.career_goals (id, user_id) on delete cascade,
  constraint competencies_name_check check (char_length(btrim(name)) between 1 and 100),
  constraint competencies_description_check
    check (description is null or char_length(description) <= 1000),
  constraint competencies_rationale_check
    check (rationale is null or char_length(rationale) <= 1000),
  constraint competencies_category_check
    check (category is null or char_length(btrim(category)) between 1 and 60),
  constraint competencies_knowledge_type_check
    check (knowledge_type in ('memory', 'application', 'documentation')),
  constraint competencies_importance_check
    check (importance in ('critical', 'high', 'medium', 'low')),
  constraint competencies_expected_level_check
    check (expected_level in ('beginner', 'intermediate', 'advanced')),
  constraint competencies_display_order_check check (display_order >= 0),
  constraint competencies_status_check check (status in ('active', 'archived'))
);

-- At most one ACTIVE competency with a given name per goal; an archived one releases its name.
-- lower() depends on the database collation: not a full Unicode case folding (accepted).
create unique index competencies_active_name_unique
  on public.competencies (career_goal_id, lower(btrim(name)))
  where status = 'active';

-- Listing and counting the competencies of a goal.
create index competencies_goal_status_order_idx
  on public.competencies (career_goal_id, status, display_order);

alter table public.competencies enable row level security;

-- Explicit REVOKE first (do not rely on default privileges), then the minimum.
revoke all on public.competencies from public, anon, authenticated;
grant select on public.competencies to authenticated;
-- status is not insertable: a competency is always created 'active' (column default).
grant insert (career_goal_id, user_id, name, description, rationale, category,
              knowledge_type, importance, expected_level, display_order)
  on public.competencies to authenticated;
-- id, career_goal_id, user_id, created_at and updated_at can never be written by a client after
-- creation, so a competency cannot be moved to another goal or user.
grant update (name, description, rationale, category, knowledge_type, importance,
              expected_level, display_order, status)
  on public.competencies to authenticated;
-- No DELETE grant.

create policy competencies_select_own on public.competencies
  for select to authenticated
  using (user_id = (select auth.uid()));

-- The "active goal" part is an EXISTS evaluated with the snapshot of the statement. The row
-- trigger repeats the check after the goal lock is granted, which closes the race with archiving.
create policy competencies_insert_own on public.competencies
  for insert to authenticated
  with check (
    user_id = (select auth.uid())
    and status = 'active'
    and exists (
      select 1 from public.career_goals g
      where g.id = career_goal_id
        and g.user_id = (select auth.uid())
        and g.status = 'active'
    )
  );

-- USING is evaluated against the EXISTING row: an archived competency, and any competency of an
-- archived goal, is invisible to UPDATE (zero rows). WITH CHECK sees the new row; together with
-- the status CHECK constraint it allows active -> archived and nothing else.
create policy competencies_update_own on public.competencies
  for update to authenticated
  using (
    user_id = (select auth.uid())
    and status = 'active'
    and exists (
      select 1 from public.career_goals g
      where g.id = career_goal_id
        and g.user_id = (select auth.uid())
        and g.status = 'active'
    )
  )
  with check (
    user_id = (select auth.uid())
    and exists (
      select 1 from public.career_goals g
      where g.id = career_goal_id
        and g.user_id = (select auth.uid())
        and g.status = 'active'
    )
  );

-- No DELETE policy on purpose.

create trigger competencies_set_updated_at
  before update on public.competencies
  for each row execute function public.set_updated_at();

-- Statement-level lock of the caller's active goal. It runs before any row of the statement is
-- read or locked (also when the statement changes zero rows). A user has at most one active goal
-- (one_active_goal_per_user). No active goal, or no identity (privileged role): it does nothing
-- and raises nothing; the row trigger decides. The lock query runs as "authenticated": it needs
-- UPDATE on at least one column of career_goals (granted) and the UPDATE policy of career_goals
-- filters it (own and active goals only).
create function public.competencies_lock_active_goal()
returns trigger
language plpgsql
volatile
set search_path = ''
as $$
begin
  perform 1
  from public.career_goals g
  where g.user_id = (select auth.uid())
    and g.status = 'active'
  for no key update;
  return null;
end;
$$;

revoke execute on function public.competencies_lock_active_goal() from public, anon, authenticated;

create trigger competencies_lock_goal
  before insert or update on public.competencies
  for each statement execute function public.competencies_lock_active_goal();

-- Row-level rules. The goal lock makes the status check and the count race-free. An archived goal
-- and another user's goal are invisible to the lock query (UPDATE policy of career_goals), so this
-- function cannot tell "archived" from "does not exist"; both are refused with the same error and
-- the backend reads the goal afterwards. The constraint names are what the backend maps to API
-- errors. On UPDATE there is no count: an UPDATE cannot make an archived competency active.
create function public.competencies_enforce_goal_rules()
returns trigger
language plpgsql
volatile
set search_path = ''
as $$
declare
  c_active_limit constant integer := 50;
  v_goal_status text;
begin
  select g.status
  into v_goal_status
  from public.career_goals g
  where g.id = new.career_goal_id
    and g.user_id = new.user_id
  for no key update;

  if v_goal_status is distinct from 'active' then
    raise exception 'career goal is not active'
      using errcode = '23514', constraint = 'competencies_goal_not_active';
  end if;

  if tg_op = 'INSERT' and new.status = 'active' then
    if (select count(*)
        from public.competencies c
        where c.career_goal_id = new.career_goal_id
          and c.status = 'active') >= c_active_limit then
      raise exception 'active competency limit reached'
        using errcode = '23514', constraint = 'competencies_active_limit';
    end if;
  end if;

  return new;
end;
$$;

revoke execute on function public.competencies_enforce_goal_rules() from public, anon, authenticated;

create trigger competencies_enforce_goal_rules
  before insert or update on public.competencies
  for each row execute function public.competencies_enforce_goal_rules();
