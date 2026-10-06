# Open-source research and attribution

Primary reference: [vintikzq/FastAPI-todo-Telegram-Bot](https://github.com/vintikzq/FastAPI-todo-Telegram-Bot), inspected at commit `dc2267e64a94cd7ef0301fd9d343c0142635f255`. Its MIT license is preserved in `THIRD_PARTY_LICENSES/vintikzq-MIT.txt`.

The arrow/center pagination pattern in `app/bot/keyboards/common.py` is adapted from `src/keyboards/task_menu.py` with inline attribution. FSM creation, inline navigation and edited-message UX informed our design. Domain models, calendar, services, repositories, transactional ledger, durable inbox/outbox and deployment code were implemented for TaskFlow.

We did not copy the reference's polling, separate REST business API, JWT authentication or Redis authorization, which conflict with this product's architecture. A wholesale fork would require removing these components and replacing its scoring/access model; reusing its small compatible UI pattern costs less than adapting the complete server.

Other inspected references: `cyrmee/telegram-task-bot` (MIT, group task management), `nxt-am/tg-bot-reminders` (MIT, personal reminders/recurrence), and `sahandsorouri/telegram-checklist-bot` (FastAPI/PostgreSQL/Alembic; no verified license during research). No code from those projects was copied. None provides this two-person peer-rating, immutable scoring and configurable-penalty model directly.

Dependencies are maintained packages installed from pinned requirements. aiogram and FastAPI are MIT; SQLAlchemy/Alembic are MIT; asyncpg is Apache-2.0; PostgreSQL uses the PostgreSQL License. Dependency metadata remains part of installed distributions. This repository does not relicense upstream copied material.
