-- Parent Insight Assistant: initial schema.
-- Run once in the Supabase SQL editor, or with: python -m app.jobs migrate
-- Safe to re-run.

-- Parents who signed in with Schoology (or the demo).
create table if not exists users (
    id bigserial primary key,
    schoology_uid text unique,
    name text not null,
    email text,
    district_domain text,
    access_token_enc text,
    access_secret_enc text,
    token_issued_at timestamptz,
    token_invalid boolean not null default false,
    is_demo boolean not null default false,
    created_at timestamptz not null default now(),
    last_login_at timestamptz
);

-- Parents' own AI keys (BYOK), encrypted. Used only while the admin allows BYOK.
create table if not exists user_ai_keys (
    user_id bigint not null references users(id) on delete cascade,
    provider text not null check (provider in ('huggingface', 'anthropic')),
    key_enc text not null,
    updated_at timestamptz not null default now(),
    primary key (user_id, provider)
);

-- Admin settings: AI provider, model per provider, BYOK switches.
create table if not exists app_settings (
    key text primary key,
    value jsonb not null,
    updated_at timestamptz not null default now()
);

create table if not exists children (
    id bigserial primary key,
    user_id bigint not null references users(id) on delete cascade,
    schoology_uid text not null,
    name text not null,
    grade_level text,
    last_synced_at timestamptz,
    unique (user_id, schoology_uid)
);

create table if not exists courses (
    id bigserial primary key,
    child_id bigint not null references children(id) on delete cascade,
    ext_id text not null,
    title text not null,
    unique (child_id, ext_id)
);

-- Grading categories with weights. kind is our label: homework, test or other.
create table if not exists categories (
    id bigserial primary key,
    course_id bigint not null references courses(id) on delete cascade,
    ext_id text not null,
    title text not null,
    kind text not null check (kind in ('homework', 'test', 'other')),
    weight double precision,
    unique (course_id, ext_id)
);

create table if not exists assignments (
    id bigserial primary key,
    child_id bigint not null references children(id) on delete cascade,
    course_id bigint not null references courses(id) on delete cascade,
    category_id bigint references categories(id) on delete set null,
    ext_id text not null,
    title text not null,
    due_at timestamptz,
    max_points double precision,
    score double precision,
    graded_at timestamptz,
    excused boolean not null default false,
    marked_missing boolean not null default false,
    late boolean not null default false,
    teacher_comment text,
    unique (child_id, ext_id)
);

-- Marks the child lost. granularity is 'question' when the question is known,
-- or 'assignment' when all we have is the assignment's score.
create table if not exists lost_marks (
    id bigserial primary key,
    child_id bigint not null references children(id) on delete cascade,
    assignment_id bigint not null references assignments(id) on delete cascade,
    granularity text not null check (granularity in ('question', 'assignment')),
    ref text not null,
    question_text text,
    student_answer text,
    teacher_comment text,
    points_possible double precision not null,
    points_lost double precision not null,
    topic text,
    labelled_at timestamptz,
    unique (assignment_id, ref)
);

create table if not exists alerts (
    id bigserial primary key,
    child_id bigint not null references children(id) on delete cascade,
    rule text not null,
    dedupe_key text not null,
    facts jsonb not null,
    message text,
    created_at timestamptz not null default now(),
    read_at timestamptz,
    emailed_at timestamptz,
    unique (child_id, dedupe_key)
);

create table if not exists summaries (
    id bigserial primary key,
    child_id bigint not null references children(id) on delete cascade,
    week_start date not null,
    facts jsonb not null,
    text text not null,
    created_at timestamptz not null default now(),
    emailed_at timestamptz,
    unique (child_id, week_start)
);

-- AI explanations cached by the hash of the facts they explain.
create table if not exists ai_texts (
    id bigserial primary key,
    child_id bigint not null references children(id) on delete cascade,
    kind text not null,
    facts_hash text not null,
    text text not null,
    created_at timestamptz not null default now(),
    unique (child_id, kind, facts_hash)
);

create table if not exists chat_messages (
    id bigserial primary key,
    child_id bigint not null references children(id) on delete cascade,
    role text not null check (role in ('user', 'assistant')),
    text text not null,
    sources jsonb,
    created_at timestamptz not null default now()
);

create index if not exists assignments_child_idx on assignments (child_id, due_at);
create index if not exists lost_marks_child_idx on lost_marks (child_id);
create index if not exists chat_messages_child_idx on chat_messages (child_id, id);

-- Supabase exposes the public schema through its REST API. Row Level Security
-- with no policies closes that route: only the backend's direct Postgres
-- connection can read or write these tables.
alter table users enable row level security;
alter table user_ai_keys enable row level security;
alter table app_settings enable row level security;
alter table children enable row level security;
alter table courses enable row level security;
alter table categories enable row level security;
alter table assignments enable row level security;
alter table lost_marks enable row level security;
alter table alerts enable row level security;
alter table summaries enable row level security;
alter table ai_texts enable row level security;
alter table chat_messages enable row level security;
