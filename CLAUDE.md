# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Инструкции

- Всегда отвечать на русском языке
- Писать комментарии в коде на русском
- Объяснять ошибки и предложения на русском
- Используй Sequential Thinking для сложных размышлений
- Никогда не используй наследование, присваивание классу внешних функций, рефлексию и другие сложные техники. Код должен быь понятен Junior разработчику с минимальным опытом
- Используй Context7 для досткупа к документации всех библиотек
- Для реализации любых фич с использованием интеграций с внешним api/библиотеками изучай документации с помощью Context7 инструментов
- Если есть изменения на фронтенде, то проверь что фронт работает, открыв его через Playwright

## Проект

AI Sales Manager — сервис для создания и подключения ИИ-менеджеров в мессенджеры (WhatsApp, Instagram).

## Стек

- **Backend:** Python 3.12 + FastAPI
- **Frontend:** Next.js 15 + React 19 + TypeScript
- **БД:** Supabase (PostgreSQL + pgvector)
- **AI:** Claude API (Anthropic SDK)
- **WhatsApp:** WAHA (self-hosted)
- **Хостинг:** Railway (авто-деплой из GitHub)
- **Репозиторий:** github.com/kh93b69/aisalesmanager

## Команды

```bash
# Локальная разработка (Docker)
docker-compose up

# Бэкенд отдельно
cd backend && pip install -r requirements.txt && uvicorn app.main:app --reload

# Фронтенд отдельно
cd frontend && npm install && npm run dev
```

## Архитектура

```
backend/app/
  main.py        — точка входа FastAPI, CORS, подключение роутов
  config.py      — переменные окружения
  database.py    — клиент Supabase
  ai_engine.py   — обёртка Claude API + RAG контекст
  rag.py         — поиск по базе знаний (knowledge_chunks)
  whatsapp.py    — отправка сообщений через WAHA
  routes/
    webhooks.py  — входящие сообщения от мессенджеров → ИИ → ответ
    dialogs.py   — API диалогов, сообщений, режим "Перехват"
    settings.py  — CRUD ботов, загрузка базы знаний

frontend/src/app/
  layout.tsx     — корневой layout
  page.tsx       — дашборд (список ботов, диалоги, сообщения)
```

## Таблицы Supabase

- **bots** — настройки ботов (system_prompt, whatsapp_session)
- **dialogs** — диалоги (bot_id, chat_id, channel, ai_disabled)
- **messages** — сообщения (dialog_id, role, content)
- **knowledge_chunks** — фрагменты базы знаний (bot_id, content)

## Переменные окружения

Задаются в `.env` (локально) и в Railway Dashboard (продакшн):
SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY, ANTHROPIC_API_KEY, WAHA_API_URL, WAHA_API_KEY
