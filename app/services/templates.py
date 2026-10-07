from app.config.settings import Settings
from app.models import TaskTemplate, User
from app.repositories.templates import TaskTemplateRepository
from app.services.base import AuthorizedService
from app.services.rules import (
    DomainError,
    estimated_minutes,
    operation_key,
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
        self, actor: User, title: str, points: int, key: str, estimate: int | None = None
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
        template = TaskTemplate(
            creator_user_id=actor.id,
            title=text_field(title, 200, "Название"),
            points=rating_points(points),
            estimated_minutes=estimated_minutes(estimate),
            operation_key=key,
        )
        self.repo.add(template)
        await self.repo.flush()
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
        for field, value in changes.items():
            if field == "title" and isinstance(value, str):
                template.title = text_field(value, 200, "Название")
            elif field == "points":
                template.points = rating_points(value)
            elif field == "estimated_minutes":
                template.estimated_minutes = estimated_minutes(value)
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
    def snapshot(template: TaskTemplate) -> dict[str, object]:
        return {
            "title": template.title,
            "points": template.points,
            "estimated_minutes": template.estimated_minutes,
            "is_active": template.is_active,
            "is_deleted": template.is_deleted,
        }
