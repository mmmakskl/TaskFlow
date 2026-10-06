from typing import Any, TypeVar

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEvent, Base, User
from app.services.rules import DomainError, StaleAction

Entity = TypeVar("Entity", bound=Base)


class Repository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, model: type[Entity], entity_id: int, lock: bool = False) -> Entity:
        if type(entity_id) is not int or entity_id < 1:
            raise StaleAction()
        query = select(model).where(model.id == entity_id)  # type: ignore[attr-defined]
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        entity = await self.session.scalar(query)
        if entity is None:
            raise StaleAction()
        return entity

    async def user(self, telegram_id: int) -> User:
        user = await self.session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            raise DomainError("🔒 Это приватный бот. Доступ к нему ограничен.")
        return user

    async def users(self) -> list[User]:
        return list(await self.session.scalars(select(User).order_by(User.id)))

    async def operation_lock(self, key: str) -> None:
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key}
        )

    def audit(
        self, actor: User, entity: Base, action: str, old: dict[str, Any], new: dict[str, Any]
    ) -> None:
        self.session.add(
            AuditEvent(
                actor_user_id=actor.id,
                entity_type=entity.__tablename__,  # type: ignore[attr-defined]
                entity_id=entity.id,  # type: ignore[attr-defined]
                action=action,
                old_data=old,
                new_data=new,
            )
        )

    def add(self, entity: Base) -> None:
        self.session.add(entity)

    async def flush(self) -> None:
        await self.session.flush()

    @staticmethod
    def version(entity: Any, expected: int) -> None:
        if type(expected) is not int or expected < 1 or entity.version != expected:
            raise StaleAction()
