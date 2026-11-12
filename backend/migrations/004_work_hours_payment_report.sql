-- Миграция 004: график работы бота, ссылка на оплату, сводка дня в Telegram.
-- Выполнить один раз в Supabase → SQL Editor. Повторный запуск безопасен.

-- ---------- График работы бота ----------
-- work_hours_enabled = false — бот отвечает круглосуточно (как раньше)
ALTER TABLE bots ADD COLUMN IF NOT EXISTS work_hours_enabled BOOLEAN NOT NULL DEFAULT FALSE;
-- 7 дней (пн…вс): [{"enabled": true, "start": "09:00", "end": "21:00"}, …]
ALTER TABLE bots ADD COLUMN IF NOT EXISTS work_schedule JSONB NOT NULL DEFAULT '[]'::jsonb;
-- Автоответ клиенту, написавшему в нерабочее время (пусто — не отправлять)
ALTER TABLE bots ADD COLUMN IF NOT EXISTS off_hours_message TEXT NOT NULL DEFAULT '';

-- Клиент написал в нерабочее время и ждёт ответа: бот ответит, как только начнётся рабочее время
ALTER TABLE dialogs ADD COLUMN IF NOT EXISTS off_hours_since TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_dialogs_off_hours ON dialogs(off_hours_since) WHERE off_hours_since IS NOT NULL;

-- ---------- Оплата ----------
ALTER TABLE bots ADD COLUMN IF NOT EXISTS payment_enabled BOOLEAN NOT NULL DEFAULT FALSE;
-- Ссылка на оплату (Kaspi Pay, страница оплаты на сайте и т.п.)
ALTER TABLE bots ADD COLUMN IF NOT EXISTS payment_link TEXT NOT NULL DEFAULT '';
-- Реквизиты / как оплатить (текст, который получит клиент вместе со ссылкой)
ALTER TABLE bots ADD COLUMN IF NOT EXISTS payment_details TEXT NOT NULL DEFAULT '';

-- ---------- Сводка дня в Telegram ----------
ALTER TABLE bots ADD COLUMN IF NOT EXISTS daily_report_enabled BOOLEAN NOT NULL DEFAULT TRUE;
-- Время отправки по местному времени, «ЧЧ:ММ»
ALTER TABLE bots ADD COLUMN IF NOT EXISTS daily_report_time TEXT NOT NULL DEFAULT '20:00';
-- Дата последней отправленной сводки (чтобы не отправить дважды за день)
ALTER TABLE bots ADD COLUMN IF NOT EXISTS daily_report_last_date DATE;
