from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardMarkup

from app.bot.keyboards.common import button, keyboard, pager
from app.bot.render import progress, safe, short
from app.models import PenaltyType, Task, User
from app.repositories.templates import TaskTemplateRepository
from app.services.deadlines import deadline_delay, delay_label
from app.services.penalties import PenaltyService
from app.services.results import ResultService
from app.services.rules import DomainError
from app.services.tasks import TaskService
from app.services.templates import TaskTemplateService
from app.utils.duration import duration
from app.utils.time import utcnow

FILTER_LABELS = {
    "all": "Все",
    "active": "Активные",
    "work": "В работе",
    "backlog": "Ждут согласования",
    "done": "Выполненные",
    "archive": "Архив",
    "overdue": "🔴 Просроченные",
}
METHOD_LABELS = {
    "PER_MINUTE": "За минуту",
    "FIXED": "Фиксированная сумма",
    "MANUAL": "Вручную при назначении",
}

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
    return task.deadline.astimezone(ZoneInfo(actor.timezone)).strftime("%d.%m.%Y, %H:%M")


class UIService:
    def __init__(
        self, tasks: TaskService, penalties: PenaltyService, results: ResultService
    ) -> None:
        self.tasks, self.penalties, self.results = tasks, penalties, results

    @property
    def templates(self) -> TaskTemplateService:
        return TaskTemplateService(
            TaskTemplateRepository(self.tasks.repo.session), self.tasks.settings
        )

    async def menu(self, actor: User) -> tuple[str, InlineKeyboardMarkup]:
        label, scores = await self.results.week()
        _, pending = await self.tasks.repo.pending_next(actor)
        _, active = await self.tasks.repo.list(actor, "active")
        text = f"🎯 <b>НЕДЕЛЯ • {label}</b>\n"
        for user, earned, lost in scores:
            net = earned - lost
            text += f"\n<b>{safe(user.display_name)}</b>\n{points(net)} / {points(user.weekly_target)}\n{progress(net, user.weekly_target)}\n"
        text += f"\n⭐ На оценку: {pending}\n✅ Активных задач: {active}"
        return text, keyboard(
            [button("➕ Задача", "new"), button("📚 Шаблоны", "templates")],
            [
                button("📋 Все задачи", "list", value="mine_all"),
                button("👥 Общие задачи", "list", value="shared_all"),
            ],
            [button(f"📥 Входящие • {pending}", "ratings"), button("🏆 Результаты", "results")],
            [button("⚠️ Штраф", "penalty"), button("⚙️ Настройки", "settings")],
        )

    async def task_created(self, actor: User, task: Task) -> tuple[str, InlineKeyboardMarkup]:
        text, _ = await self.task_details(actor, task.id)
        return "✅ <b>Задача создана</b>\n\n" + text, keyboard(
            [
                button("Открыть задачу", "task", task.id),
                button("📋 К задачам", "list", value="mine_all"),
            ],
            [button("➕ Ещё задачу", "new"), button("🏠 Меню", "menu")],
        )

    async def settings_screen(self, actor: User) -> tuple[str, InlineKeyboardMarkup]:
        return (
            f"⚙️ <b>Настройки</b>\n\n👤 {safe(actor.display_name)}\n"
            f"Недельная цель: {points(actor.weekly_target)}\nЧасовой пояс дат: {safe(actor.timezone)}\n\n"
            f"Общая неделя результатов: {safe(self.results.settings.default_timezone)}",
            keyboard(
                [button("⚠️ Типы штрафов", "types")],
                [button("🎯 Недельная цель", "user_setting", value="weekly_target")],
                [button("🌍 Часовой пояс дат", "user_setting", value="timezone")],
                [button("🏠 Меню", "menu")],
            ),
        )

    async def task_list(
        self, actor: User, filter_name: str, page: int, scope: str = "mine"
    ) -> tuple[str, InlineKeyboardMarkup]:
        if "_" in filter_name:
            scope, filter_name = filter_name.split("_", 1)
        if (
            scope not in ("mine", "peer", "shared", "assigned", "all")
            or filter_name not in FILTER_LABELS
        ):
            raise DomainError("Недопустимый список.")
        requested_page = page
        tasks, count = await self.tasks.repo.list(actor, filter_name, page, scope=scope)
        page = min(page, max(0, (count - 1) // 5))
        if requested_page != page:
            tasks, count = await self.tasks.repo.list(actor, filter_name, page, scope=scope)
        participants = await self.tasks.user_repo.participants()
        peer = next(user for user in participants if user.id != actor.id)
        scopes = {
            "mine": "Мои",
            "peer": peer.display_name,
            "shared": "Общие",
            "assigned": "Назначенные мной",
            "all": "Все задачи",
        }
        text = f"📋 <b>{safe(scopes[scope])} • {count}</b>\n{FILTER_LABELS[filter_name]}\n"
        for summary_scope in (scope,) if scope in ("mine", "peer") else ():
            summary = await self.tasks.repo.summary(actor, scope=summary_scope)
            text += (
                f"\n{safe(scopes[summary_scope])}: К выполнению {points(summary.todo_points)} • "
                f"В работе {points(summary.in_progress_points)}\n"
                f"План {points(summary.planned_points)} баллов • Ждут {summary.pending_count}\n"
                f"Выполнено за всё время: {points(summary.completed_points)} баллов\n"
            )
        planned = sum(task.estimated_minutes or 0 for task in tasks)
        if planned:
            text += f"\n⏱ План на странице: ~{duration(planned)}\n"
        rows = []
        now = utcnow()
        for task in tasks:
            icon = ("🤝 " if task.kind == "SHARED" else "") + STATUS_LABELS[task.status].split()[0]
            extras = []
            score = task.awarded_points if task.status == "DONE" else task.rated_points
            if score is not None and task.status != "CANCELLED":
                extras.append(f"⭐ {points(score)}" + (" каждому" if task.kind == "SHARED" else ""))
            if task.estimated_minutes:
                extras.append(f"~{duration(task.estimated_minutes)}")
            delay = deadline_delay(task, now)
            if delay is not None:
                extras.append("🔴 опоздание" if task.status == "DONE" else "🔴 просрочено")
            elif task.deadline:
                extras.append(f"до {deadline(task, actor)[:5]}")
            suffix = (" · " + " · ".join(extras)) if extras else ""
            title = short(task.title, max(14, 42 - len(suffix)))
            rows.append([button(f"{icon} {title}{suffix}", "task", task.id)])
        if not tasks:
            text += "\nЗдесь пока нет задач."
        else:
            text += "\nНажми задачу, чтобы открыть детали."
        rows.append(
            pager(
                "list",
                page,
                (page + 1) * 5 < count,
                scope + "_" + filter_name,
                max(1, (count + 4) // 5),
            )
        )
        for filters in (("all", "active", "work"), ("backlog", "done", "archive")):
            rows.append(
                [
                    button(
                        ("✓ " if filter_name == name else "") + FILTER_LABELS[name],
                        "list",
                        value=scope + "_" + name,
                    )
                    for name in filters
                ]
            )
        rows.append(
            [
                button(
                    ("✓ " if filter_name == "overdue" else "") + FILTER_LABELS["overdue"],
                    "list",
                    value=scope + "_overdue",
                )
            ]
        )
        for names in (("mine", "peer", "shared"), ("assigned",)):
            rows.append(
                [
                    button(
                        ("✓ " if scope == name else "") + scopes[name],
                        "list",
                        value=name + "_" + filter_name,
                    )
                    for name in names
                ]
            )
        rows.append([button("➕ Добавить", "new"), button("🏠 Меню", "menu")])
        return text, keyboard(*rows)

    async def task_details(
        self, actor: User, task_id: int, back_filter: str = "mine_all", back_page: int = 0
    ) -> tuple[str, InlineKeyboardMarkup]:
        task = await self.tasks.view(actor, task_id)
        delay = deadline_delay(task, utcnow())
        users = {user.id: user for user in await self.tasks.repo.users()}
        text = f"📝 <b>{safe(task.title)}</b>\n\n👤 {safe(users[task.owner_user_id].display_name)}\n{STATUS_LABELS[task.status]}"
        if task.status == "BACKLOG":
            pending_label = (
                "Ждёт принятия"
                if task.owner_user_id == task.evaluator_user_id and task.kind != "SHARED"
                else "Ждёт оценки"
            )
            text = text.replace(STATUS_LABELS["BACKLOG"], "🆕 " + pending_label)
            text += f"\nСогласует: {safe(users[task.evaluator_user_id].display_name)}"
        text += f"\nАвтор: {safe(users[task.creator_user_id].display_name)}"
        text += "\nИсполнители: " + ", ".join(
            safe(users[uid].display_name) for uid in task.participant_user_ids
        )
        if task.kind == "SHARED":
            text += "\n👥 Общая задача • один статус для обоих"
        text += f"\nПредложение: {task.proposed_points or '—'}" + (
            " каждому" if task.kind == "SHARED" and task.proposed_points else ""
        )
        text += (
            f"\n⭐ {points(task.rated_points)} баллов"
            + (" каждому" if task.kind == "SHARED" else "")
            if task.rated_points
            else ("\n⭐ Не оценена" if task.status == "CANCELLED" else "\n⭐ Ждёт оценки")
        )
        if task.rated_by_user_id:
            text += f"\nОценил(а): {safe(users[task.rated_by_user_id].display_name)}"
        elif task.rating_source == "TEMPLATE":
            text += "\n📚 Оценка из шаблона"
        text += f"\n📅 Дедлайн: {deadline(task, actor)}\n🕒 Создана {task.created_at.astimezone(ZoneInfo(actor.timezone)):%d.%m, %H:%M}"
        if delay is not None:
            if task.status == "DONE":
                text = text.replace("✅ Выполнено", "✅ Выполнено с опозданием", 1)
                text += f"\n🔴 Опоздание: {delay_label(delay)}"
            else:
                text += f"\n🔴 Просрочено на {delay_label(delay)}"
        text += (
            f"\n⏱ План: ~{duration(task.estimated_minutes)}"
            if task.estimated_minutes
            else "\n⏱ План: не указан"
        )
        if task.comment:
            text += f"\n\nКомментарий:\n{safe(task.comment)}"
        rows = []
        if task.status == "DONE":
            assert task.completed_at is not None and task.awarded_points is not None
            text += (
                f"\n\n✅ Выполнено {task.completed_at.astimezone(ZoneInfo(actor.timezone)):%d.%m в %H:%M}\n💰 Начислено: {points(task.awarded_points)}"
                + (" каждому" if task.kind == "SHARED" else "")
            )
        if self.tasks.can_manage(task, actor):
            if task.status == "TODO":
                rows.append(
                    [
                        button("▶️ Начать", "status", task.id, task.version, "IN_PROGRESS"),
                        button(
                            "✅ Выполнено для обоих" if task.kind == "SHARED" else "✅ Выполнено",
                            "status",
                            task.id,
                            task.version,
                            "DONE",
                        ),
                    ]
                )
            elif task.status == "IN_PROGRESS":
                rows.append(
                    [
                        button("📌 К выполнению", "status", task.id, task.version, "TODO"),
                        button(
                            "✅ Выполнено для обоих" if task.kind == "SHARED" else "✅ Выполнено",
                            "status",
                            task.id,
                            task.version,
                            "DONE",
                        ),
                    ]
                )
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
        if task.status == "BACKLOG" and task.evaluator_user_id == actor.id:
            rows.append([button("⭐ Оценить / принять", "ratings", task.id - 1)])
        if task.status in ("TODO", "IN_PROGRESS") and task.rated_by_user_id == actor.id:
            rows.append([button("⭐ Изменить цену", "rerate", task.id, task.version)])
        if delay is not None:
            rows.append([button("⚠️ Назначить штраф", "task_penalty", task.id, task.version)])
        if task.status == "BACKLOG" or task.deadline is not None:
            rows.append([button("🔄 Обновить", "task", task.id)])
        rows.append(
            [button("⬅️ Назад", "list", back_page, value=back_filter), button("🏠 Меню", "menu")]
        )
        return text, keyboard(*rows)

    @staticmethod
    def status_confirmation(task: Task, target: str) -> str:
        if target not in STATUS_LABELS:
            raise DomainError("Недопустимый статус.")
        heading = "🚫 Отменить задачу?" if target == "CANCELLED" else "↩️ Открыть задачу снова?"
        text = f"<b>{heading}</b>\n\n📝 {safe(task.title)}\nБудет: {STATUS_LABELS[target]}"
        if task.status == "DONE":
            text += (
                f"\n\nИз результата исходной недели будет убрано {points(task.awarded_points or 0)} баллов"
                + (" у обоих." if task.kind == "SHARED" else ".")
            )
        else:
            text += "\n\nОтменённая задача не начисляет баллы."
        return text

    async def rating(self, actor: User, after: int = 0) -> tuple[str, InlineKeyboardMarkup]:
        task, count = await self.tasks.repo.pending_next(actor, after)
        if task is None:
            return "✅ Всё оценено!\nВходящих предложений нет.", keyboard(
                [button("🏠 Меню", "menu")]
            )
        users = {user.id: user for user in await self.tasks.repo.users()}
        text = f"📥 <b>Входящие • осталось {count}</b>\n\n{safe(users[task.proposal_by_user_id].display_name)} предлагает:\n\n📝 {safe(task.title)}\n📅 {deadline(task, actor)}\n💬 {safe(task.comment or '—')}\n\nПрими предложение или укажи окончательную цену."
        if task.estimated_minutes:
            text = text.replace("\n💬", f"\n⏱ План: ~{duration(task.estimated_minutes)}\n💬")
        text += f"\n👥 {'Общая задача' if task.kind == 'SHARED' else safe(users[task.owner_user_id].display_name)}"
        text += f"\nПредложение: {task.proposed_points or 'не указано'}" + (
            " каждому" if task.kind == "SHARED" and task.proposed_points else ""
        )
        rows = (
            [
                [
                    button(
                        f"✅ Принять {task.proposed_points}",
                        "rate",
                        task.id,
                        task.version,
                        str(task.proposed_points),
                    )
                ]
            ]
            if task.proposed_points
            else []
        ) + [
            [
                button(str(value), "rate", task.id, task.version, str(value))
                for value in (100, 250, 500)
            ],
            [
                button(str(value), "rate", task.id, task.version, str(value))
                for value in (750, 1000)
            ],
            [
                button("✏️ Другое", "custom_rate", task.id, task.version),
                button("🚫 Отклонить", "reject", task.id, task.version),
            ],
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
                        f"{kind.emoji} {short(kind.name, 30)}{rate}{suffix}",
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
        if settings:
            rows.append([button("⬅️ Настройки", "settings")])
        else:
            rows.append(
                [button("⬅️ Назад", "penalty_back", version=flow), button("❌ Отмена", "menu")]
            )
        return text, keyboard(*rows)

    async def type_details(
        self, type_id: int, back_page: int = 0
    ) -> tuple[str, InlineKeyboardMarkup]:
        kind = await self.penalties.repo.get(PenaltyType, type_id)
        text = f"{safe(kind.emoji)} <b>{safe(kind.name)}</b>\n\n{safe(kind.description)}\n\nРасчёт: {METHOD_LABELS[kind.calculation_type]}\nСтавка: {points(kind.rate) if kind.calculation_type != 'MANUAL' else 'указывается при назначении'}\n{'✅ Включён' if kind.is_active else '⏸ Выключен'}"
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
        rows.append([button("⬅️ Типы штрафов", "types", back_page)])
        return text, keyboard(*rows)

    async def penalty_list(self, actor: User, page: int) -> tuple[str, InlineKeyboardMarkup]:
        penalties, has_next = await self.penalties.list(page)
        if not penalties and page > 0:
            page = 0
            penalties, has_next = await self.penalties.list(0)
        users = {user.id: user for user in await self.penalties.repo.users()}
        text = "⚠️ <b>Штрафы</b>\n"
        rows = []
        for penalty in penalties:
            text += f"\n{safe(users[penalty.target_user_id].display_name)} • {safe(penalty.name_snapshot)}\n−{points(penalty.points)} • {penalty.created_at.astimezone(ZoneInfo(actor.timezone)):%d.%m.%Y}\n"
            rows.append(
                [
                    button(
                        f"🗑 {short(penalty.name_snapshot, 30)} • {points(penalty.points)}",
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
