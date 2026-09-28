-- Marked papers uploaded by parents. The photo or PDF itself is never stored:
-- it is read in memory and discarded. Only the confirmed results are kept.

-- Assignments that exist only because a parent uploaded a paper for work that
-- is not in Schoology. They feed weak topics and advice, never course averages.
alter table assignments add column if not exists source text not null default 'schoology'
    check (source in ('schoology', 'paper'));

create table if not exists papers (
    id bigserial primary key,
    child_id bigint not null references children(id) on delete cascade,
    course_id bigint not null references courses(id) on delete cascade,
    assignment_id bigint references assignments(id) on delete set null,
    title text not null,
    status text not null default 'review' check (status in ('review', 'confirmed')),
    page_count int not null,
    read_error text,              -- why reading failed, when the parent entered results by hand
    advice text,
    advice_error text,
    created_by bigint references users(id) on delete set null,
    created_at timestamptz not null default now(),
    confirmed_at timestamptz
);

-- One row per question, as read by the AI and then checked by the parent.
create table if not exists paper_questions (
    id bigserial primary key,
    paper_id bigint not null references papers(id) on delete cascade,
    position int not null,
    label text,                   -- the paper's own numbering, e.g. "3b"
    question_text text,
    student_answer text,
    teacher_comment text,
    points_earned double precision,
    points_possible double precision,
    unique (paper_id, position)
);

create index if not exists papers_child_idx on papers (child_id, created_at desc);

alter table papers enable row level security;
alter table paper_questions enable row level security;
