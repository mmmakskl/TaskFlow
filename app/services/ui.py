from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardMarkup

from app.bot.keyboards.common import button, keyboard, pager
from app.bot.render import progress, safe
from app.models import PenaltyType, Task, User
from app.services.penalties import PenaltyService
from app.services.results import ResultService
from app.services.tasks import TaskService

STATUS_LABELS = {
    "BACKLOG": "🆕 Ждёт оценки",
    "TODO": "📌 К выполнению",
    "IN_PROGRESS": "▶️ В работе",
    "DONE": "✅ Выполнено",
    "CANCELLED": "🚫 Отменено",
}


def points(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def deadline(task: Task, actor: User) -> str:
    if task.deadline is None:
        return "Без дедлайна"
    return task.deadline.astimezone(ZoneInfo(actor.timezone)).strftime("%d.%m.%Y")


class UIService:
    def __init__(
        self, tasks: TaskService, penalties: PenaltyService, results: ResultService
    ) -> None:
        self.tasks, self.penalties, self.results = tasks, penalties, results

    async def menu(self, actor: User) -> tuple[str, InlineKeyboardMarkup]:
        label, scores = await self.results.week()
        _, pending = await self.tasks.repo.pending_next(actor)
        _, active = await self.tasks.repo.list(actor)
        text = f"🎯 <b>НЕДЕЛЯ • {label}</b>\n"
        for user, earned, lost in scores:
            net = earned - lost
            text += f"\n<b>{safe(user.display_name)}</b>\n{points(net)} / {points(user.weekly_target)}\n{progress(net, user.weekly_target)}\n"
        text += f"\n⭐ На оценку: {pending}\n✅ Активных задач: {active}"
        rating = f"⭐ Оценить {pending}" if pending else "✅ Всё оценено"
        return text, keyboard(
            [button("➕ Задача", "new"), button("⚠️ Штраф", "penalty")],
            [button("📋 Задачи", "list", value="active"), button(rating, "ratings")],
            [button("🏆 Результаты", "results")],
            [button("⚙️ Настройки", "settings")],
        )

    async def task_list(
        self, actor: User, filter_name: str, page: int
    ) -> tuple[str, InlineKeyboardMarkup]:
        tasks, count = await self.tasks.repo.list(actor, filter_name, page)
        page = min(page, max(0, (count - 1) // 5))
        text = f"📋 <b>МОИ ЗАДАЧИ • {count}</b>\n"
        rows = []
        for task in tasks:
            icon = STATUS_LABELS[task.status].split()[0]
            extra = (
                "ждёт оценки" if task.rated_points is None else f"⭐ {points(task.rated_points)}"
            )
            text += f"\n{icon} {safe(task.title[:70])}\n   {extra}"
            if task.deadline:
                text += f" • до {deadline(task, actor)}"
            text += "\n"
            rows.append([button(f"{icon} {task.title[:45]}", "task", task.id)])
        if not tasks:
            text += "\nЗдесь пока нет задач."
        rows.append(pager("list", page, (page + 1) * 5 < count, filter_name))
        rows += [
            [button("Активные", "list", value="active"), button("В работе", "list", value="work")],
            [
                button("Ждут оценки", "list", value="backlog"),
                button("Выполненные", "list", value="done"),
            ],
            [button("🚫 Архив", "list", value="archive")],
            [button("➕ Добавить", "new"), button("🏠 Меню", "menu")],
        ]
        return text, keyboard(*rows)

    async def task_details(
        self, actor: User, task_id: int, back_filter: str = "active", back_page: int = 0
    ) -> tuple[str, InlineKeyboardMarkup]:
        task = await self.tasks.view(actor, task_id)
        users = {user.id: user for user in await self.tasks.repo.users()}
        text = f"📝 <b>{safe(task.title)}</b>\n\n👤 {safe(users[task.owner_user_id].display_name)}\n{STATUS_LABELS[task.status]}"
        text += (
            f"\n⭐ {points(task.rated_points)} баллов" if task.rated_points else "\n⭐ Ждёт оценки"
        )
        text += f"\n📅 {deadline(task, actor)}\n🕒 Создана {task.created_at.astimezone(ZoneInfo(actor.timezone)):%d.%m, %H:%M}"
        if task.comment:
            text += f"\n\nКомментарий:\n{safe(task.comment)}"
        rows = []
        if task.status == "DONE":
            assert task.completed_at is not None and task.awarded_points is not None
            text += f"\n\n✅ Выполнено {task.completed_at.astimezone(ZoneInfo(actor.timezone)):%d.%m в %H:%M}\n💰 Начислено: {points(task.awarded_points)}"
        if actor.id == task.owner_user_id:
            if task.status == "TODO":
                rows.append(
                    [
                        button("▶️ Начать", "status", task.id, task.version, "IN_PROGRESS"),
                        button("✅ Выполнено", "status", task.id, task.version, "DONE"),
                    ]
                )
            elif task.status == "IN_PROGRESS":
                rows.append([button("✅ Выполнено", "status", task.id, task.version, "DONE")])
            if task.status == "DONE":
                rows.append(
                    [
                        button("↩️ К выполнению", "status", task.id, task.version, "TODO"),
                        button("▶️ В работу", "status", task.id, task.version, "IN_PROGRESS"),
                    ]
                )
            if task.status not in ("DONE", "CANCELLED"):
                rows.append(
                    [
                        button("✏️ Изменить", "edit", task.id, task.version),
                        button("🚫 Отменить", "status", task.id, task.version, "CANCELLED"),
                    ]
                )
        elif task.status == "BACKLOG":
            rows.append([button("⭐ Оценить", "ratings")])
        rows.append(
            [button("⬅️ Назад", "list", back_page, value=back_filter), button("🏠 Меню", "menu")]
        )
        return text, keyboard(*rows)

    async def rating(self, actor: User, after: int = 0) -> tuple[str, InlineKeyboardMarkup]:
        task, count = await self.tasks.repo.pending_next(actor, after)
        if task is None:
            return "✅ Всё оценено!\nНовых задач на оценку нет.", keyboard(
                [button("🏠 Меню", "menu")]
            )
        users = {user.id: user for user in await self.tasks.repo.users()}
        text = f"⭐ <b>Задачи на оценку • осталось {count}</b>\n\n{safe(users[task.owner_user_id].display_name)} добавил(а):\n\n📝 {safe(task.title)}\n📅 {deadline(task, actor)}\n💬 {safe(task.comment or '—')}\n\nСколько баллов стоит задача?"
        rows = [
            [
                button(str(value), "rate", task.id, task.version, str(value))
                for value in (100, 250, 500)
            ],
            [
                button(str(value), "rate", task.id, task.version, str(value))
                for value in (750, 1000)
            ],
            [button("✏️ Другое", "custom_rate", task.id, task.version)],
            [button("⏭ Пропустить", "ratings", task.id), button("🏠 Меню", "menu")],
        ]
        return text, keyboard(*rows)

    async def results_screen(self, offset: int = 0) -> tuple[str, InlineKeyboardMarkup]:
        if not -520 <= offset <= 0:
            offset = 0
        label, scores = await self.results.week(offset)
        text = f"🏆 <b>{label}</b>\n"
        nets = []
        for user, earned, lost in scores:
            net = earned - lost
            nets.append(net)
            text += f"\n<b>{safe(user.display_name.upper())}</b>\n{points(net)} / {points(user.weekly_target)}\n{progress(net, user.weekly_target)}\n\n✅ Задачи: +{points(earned)}\n⚠️ Штрафы: −{points(lost)}\nОсталось: {points(max(0, user.weekly_target - net))}\n"
        text += f"\nРазница: {points(abs(nets[0] - nets[1]))} баллов"
        return text, keyboard(
            [button("Эта неделя", "results"), button("Прошлая неделя", "results", value="-1")],
            [button("📜 История", "history"), button("⚠️ Все штрафы", "penalty_list")],
            [button("🏠 Меню", "menu")],
        )

    async def history(self, page: int) -> tuple[str, InlineKeyboardMarkup]:
        weeks, page, has_next = await self.results.history(page)
        text = "📜 <b>История</b>\n"
        if not weeks:
            text += "\nЗдесь появятся результаты завершённых недель."
        for label, scores in weeks:
            text += f"\n<b>{label}</b>\n"
            for user, earned, lost in scores:
                net = earned - lost
                text += f"{safe(user.display_name)} {points(net)} {'✅' if net >= user.weekly_target else ''}\n"
        return text, keyboard(
            pager("history", page, has_next),
            [button("🏆 Результаты", "results"), button("🏠 Меню", "menu")],
        )

    async def penalty_types(
        self, page: int = 0, settings: bool = False, flow: int = 0
    ) -> tuple[str, InlineKeyboardMarkup]:
        kinds, has_next = await self.penalties.types(settings, page)
        text = "⚙️ <b>Типы штрафов</b>" if settings else "⚠️ Выбери тип штрафа"
        rows = []
        for kind in kinds:
            rate = (
                f" — {points(kind.rate)}/мин"
                if kind.calculation_type == "PER_MINUTE"
                else (f" — {points(kind.rate)}" if kind.calculation_type == "FIXED" else "")
            )
            suffix = "" if kind.is_active else " • выкл"
            rows.append(
                [
                    button(
                        f"{kind.emoji} {kind.name[:40]}{rate}{suffix}",
                        "type_view" if settings else "penalty_type",
                        kind.id,
                        kind.version,
                        value=str(flow) if not settings else "",
                    )
                ]
            )
        rows.append(
            pager(
                "types" if settings else "penalty_kinds",
                page,
                has_next,
                value=str(flow) if not settings else "",
            )
        )
        if settings:
            rows.append([button("➕ Новый тип", "type_new")])
        rows.append(
            [button("⬅️ Настройки" if settings else "❌ Отмена", "settings" if settings else "menu")]
        )
        return text, keyboard(*rows)

    async def type_details(self, type_id: int) -> tuple[str, InlineKeyboardMarkup]:
        kind = await self.penalties.repo.get(PenaltyType, type_id)
        text = f"{safe(kind.emoji)} <b>{safe(kind.name)}</b>\n\n{safe(kind.description)}\n\nРасчёт: {kind.calculation_type}\nСтавка: {points(kind.rate)}\n{'✅ Включён' if kind.is_active else '⏸ Выключен'}"
        rows = [
            [button(label, "type_edit", kind.id, kind.version, field) for label, field in row]
            for row in [
                [("Название", "name"), ("Emoji", "emoji")],
                [("Описание", "description"), ("Ставка", "rate")],
                [("Способ расчёта", "calculation_type")],
            ]
        ]
        rows.append(
            [
                button(
                    "⏸ Выключить" if kind.is_active else "✅ Включить",
                    "type_toggle",
                    kind.id,
                    kind.version,
                )
            ]
        )
        rows.append([button("⬅️ Типы штрафов", "types")])
        return text, keyboard(*rows)

    async def penalty_list(self, actor: User, page: int) -> tuple[str, InlineKeyboardMarkup]:
        penalties, has_next = await self.penalties.list(page)
        users = {user.id: user for user in await self.penalties.repo.users()}
        text = "⚠️ <b>Штрафы</b>\n"
        rows = []
        for penalty in penalties:
            text += f"\n{safe(users[penalty.target_user_id].display_name)} • {safe(penalty.name_snapshot)}\n−{points(penalty.points)} • {penalty.created_at.astimezone(ZoneInfo(actor.timezone)):%d.%m.%Y}\n"
            rows.append(
                [
                    button(
                        f"🗑 {penalty.name_snapshot[:30]} • {points(penalty.points)}",
                        "penalty_delete",
                        penalty.id,
                        penalty.version,
                    )
                ]
            )
        if not penalties:
            text += "\nШтрафов пока нет."
        return text, keyboard(
            *rows,
            pager("penalty_list", page, has_next),
            [button("🏆 Результаты", "results"), button("🏠 Меню", "menu")],
        )
