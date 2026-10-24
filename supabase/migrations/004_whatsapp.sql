-- WhatsApp subscriptions. A family registers on the website with their child's Schoology
-- personal key and their WhatsApp number; the key is stored encrypted so the server can sync
-- and send updates while they are away. The number becomes active once the family sends the
-- JOIN code from it (proving it is theirs, and their opt-in). Deleting the user deletes all of it.
alter table users add column if not exists own_key_enc text;
alter table users add column if not exists consented_at timestamptz;
alter table users add column if not exists whatsapp_phone text;
alter table users add column if not exists whatsapp_status text not null default 'none';  -- none, active, stopped
alter table users add column if not exists pending_phone text;
alter table users add column if not exists join_code text;
alter table users add column if not exists join_code_expires_at timestamptz;
alter table users add column if not exists paid_at timestamptz;
alter table users add column if not exists delete_code text;
alter table users add column if not exists delete_code_expires_at timestamptz;
alter table users add column if not exists delete_requested_at timestamptz;
create unique index if not exists users_whatsapp_phone on users (whatsapp_phone) where whatsapp_phone is not null;
-- Each alert and weekly summary is sent on WhatsApp once.
alter table alerts add column if not exists whatsapp_sent_at timestamptz;
alter table summaries add column if not exists whatsapp_sent_at timestamptz;
