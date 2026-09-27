-- A name you give a device on the dashboard ("Laptop", "Work PC"). Uploads keep refreshing `name`
-- (the computer's name), so the label lives in its own column; Ask and the dashboard prefer it.
alter table public.devices add column label text not null default '';
