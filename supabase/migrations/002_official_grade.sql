-- Schoology's own course grade for the current grading period, when it reports one.
-- Shown instead of the app's calculated average, which can differ when category
-- weights are hidden from the signed-in account.
alter table courses add column if not exists official_grade double precision;
alter table courses add column if not exists official_grade_label text;
