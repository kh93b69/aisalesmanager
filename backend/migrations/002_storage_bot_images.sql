-- ============================================================
-- Миграция 002: хранилище картинок бота (Supabase Storage)
-- Дата: 2026-11-12
--
-- Без этого бакета загрузка в «Настройки → Картинки» падает с «Bucket not found».
-- Как применить: Supabase Dashboard → SQL Editor → вставить файл целиком → Run.
-- Можно запускать повторно.
-- ============================================================

-- Публичный бакет: WhatsApp (Wazzup) скачивает картинку по прямой ссылке.
-- Загружает и удаляет файлы только наш бэкенд (service role), посторонние писать не могут.
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
  'bot_images',
  'bot_images',
  TRUE,
  10485760,                                        -- 10 МБ, как и проверка на бэкенде
  ARRAY['image/jpeg', 'image/png', 'image/webp']
)
ON CONFLICT (id) DO UPDATE SET
  public = TRUE,
  file_size_limit = 10485760,
  allowed_mime_types = ARRAY['image/jpeg', 'image/png', 'image/webp'];
