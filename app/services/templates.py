from __future__ import annotations

import builtins

from sqlalchemy import select

from app.config.settings import Settings
from app.models import TaskTemplate, User
from app.models.entities import Tag, TemplateTag
from app.repositories.templates import TaskTemplateRepository
from app.services.base import AuthorizedService
from app.services.rules import (
    DomainError,
    estimated_minutes,
    operation_key,
    positive_integer,
    rating_points,
    text_field,
)
from app.services.transactions import atomic


class TaskTemplateService(AuthorizedService):
    repo: TaskTemplateRepository

    def __init__(self, repo: TaskTemplateRepository, settings: Settings) -> None:
        super().__init__(repo, settings)
        self.repo = repo

    async def list(
        self, actor: User, active_only: bool = True, page: int = 0
    ) -> list[TaskTemplate]:
        await self.actor(actor)
        allowed = await self.configured_participant_ids()
        return await self.repo.list(active_only, page, creator_ids=allowed)

    async def count(self, actor: User, active_only: bool = True) -> int:
        await self.actor(actor)
        allowed = await self.configured_participant_ids()
        return await self.repo.count(active_only, creator_ids=allowed)

    async def view(self, actor: User, template_id: int) -> TaskTemplate:
        await self.actor(actor)
        template = await self.repo.get(TaskTemplate, template_id)
        await self.validate_creator(template)
        return template

    async def validate_creator(self, template: TaskTemplate) -> None:
        if template.creator_user_id not in await self.configured_participant_ids():
            raise DomainError("Этот шаблон недоступен.")
        if template.is_deleted:
            raise DomainError("Шаблон удалён.")

    @atomic
    async def create(
        self,
        actor: User,
        title: str,
        points: int,
        key: str,
        estimate: int | None = None,
        *,
        pricing_mode: str = "FIXED",
        base_quantity: int | None = None,
        unit_label: str | None = None,
        tag_ids: builtins.list[int] | None = None,
    ) -> TaskTemplate:
        actor = await self.actor(actor)
        await self.configured_participant_ids()
        key = operation_key(key)
        await self.repo.operation_lock(key)
        existing = await self.repo.by_operation(key)
        if existing:
            await self.validate_creator(existing)
            if existing.creator_user_id != actor.id:
                raise DomainError("Доступ запрещён.")
            return existing
        mode, base, label = self._price_config(pricing_mode, base_quantity, unit_label)
        template = TaskTemplate(
            creator_user_id=actor.id,
            title=text_field(title, 200, "Название"),
            points=rating_points(points),
            estimated_minutes=estimated_minutes(estimate),
            pricing_mode=mode,
            base_quantity=base,
            unit_label=label,
            operation_key=key,
        )
        if tag_ids is not None:
            if len(tag_ids) > 50 or len(set(tag_ids)) != len(tag_ids):
                raise DomainError("Недопустимый список тегов.")
            tags = list(
                await self.repo.session.scalars(
                    select(Tag)
                    .where(Tag.id.in_(tag_ids))
                    .order_by(Tag.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            if len(tags) != len(tag_ids) or any(tag.archived for tag in tags):
                raise DomainError("Тег недоступен или архивирован.")
        self.repo.add(template)
        await self.repo.flush()
        for tag_id in tag_ids or []:
            self.repo.add(TemplateTag(template_id=template.id, tag_id=tag_id))
        self.repo.audit(actor, template, "create", {}, self.snapshot(template))
        return template

    @atomic
    async def edit(
        self, actor: User, template_id: int, version: int, **changes: object
    ) -> TaskTemplate:
        actor = await self.actor(actor)
        template = await self.repo.get(TaskTemplate, template_id, lock=True)
        await self.validate_creator(template)
        self.repo.version(template, version)
        if not changes:
            raise DomainError("Укажи изменение.")
        old = self.snapshot(template)
        pricing_mode = changes.get("pricing_mode", template.pricing_mode)
        base_quantity = changes.get("base_quantity", template.base_quantity)
        unit_label = changes.get("unit_label", template.unit_label)
        pricing_changed = any(
            key in changes for key in ("pricing_mode", "base_quantity", "unit_label")
        )
        if pricing_changed:
            mode, base, label = self._price_config(pricing_mode, base_quantity, unit_label)
            template.pricing_mode, template.base_quantity, template.unit_label = mode, base, label
        for field, value in changes.items():
            if field == "title" and isinstance(value, str):
                template.title = text_field(value, 200, "Название")
            elif field == "points":
                template.points = rating_points(value)
            elif field == "estimated_minutes":
                template.estimated_minutes = estimated_minutes(value)
            elif field in ("pricing_mode", "base_quantity", "unit_label"):
                continue
            elif field == "is_active" and type(value) is bool:
                template.is_active = value
            else:
                raise DomainError("Недопустимое поле.")
        template.version += 1
        self.repo.audit(actor, template, "edit", old, self.snapshot(template))
        await self.repo.flush()
        return template

    async def disable(self, actor: User, template_id: int, version: int) -> TaskTemplate:
        return await self.edit(actor, template_id, version, is_active=False)

    @atomic
    async def delete(self, actor: User, template_id: int, version: int) -> TaskTemplate:
        actor = await self.actor(actor)
        template = await self.repo.get(TaskTemplate, template_id, lock=True)
        await self.validate_creator(template)
        self.repo.version(template, version)
        old = self.snapshot(template)
        template.is_active = False
        template.is_deleted = True
        template.version += 1
        self.repo.audit(actor, template, "delete", old, self.snapshot(template))
        await self.repo.flush()
        return template

    @staticmethod
    def _price_config(
        mode: object, base_quantity: object, unit_label: object
    ) -> tuple[str, int | None, str | None]:
        if mode == "FIXED":
            if base_quantity is not None or unit_label is not None:
                raise DomainError("Фиксированный шаблон не использует базовое количество.")
            return "FIXED", None, None
        if mode == "PER_TIME":
            base = positive_integer(base_quantity, 10080)
            if unit_label is not None:
                raise DomainError("Для шаблона по времени название единицы не требуется.")
            return "PER_TIME", base, None
        if mode == "PER_QUANTITY":
            base = positive_integer(base_quantity, 10000)
            if not isinstance(unit_label, str):
                raise DomainError("Укажи название единицы.")
            label = text_field(unit_label, 40, "Единица")
            return "PER_QUANTITY", base, label
        raise DomainError("Недопустимый способ расчёта шаблона.")

    @staticmethod
    def snapshot(template: TaskTemplate) -> dict[str, object]:
        return {
            "title": template.title,
            "points": template.points,
            "estimated_minutes": template.estimated_minutes,
            "pricing_mode": template.pricing_mode,
            "base_quantity": template.base_quantity,
            "unit_label": template.unit_label,
            "is_active": template.is_active,
            "is_deleted": template.is_deleted,
        }
