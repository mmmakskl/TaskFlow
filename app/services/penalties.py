from typing import Any

from pydantic import ValidationError

from app.config.settings import Settings
from app.models import Notification, Penalty, PenaltyType, ScoreEvent, User
from app.repositories.base import Repository
from app.repositories.penalties import PenaltyRepository
from app.schemas.penalties import PenaltyPreview
from app.services.base import AuthorizedService
from app.services.rules import DomainError, StaleAction, operation_key, penalty_points, text_field
from app.services.transactions import atomic
from app.utils.time import utcnow


class PenaltyService(AuthorizedService):
    repo: PenaltyRepository

    def __init__(self, repo: Repository, settings: Settings) -> None:
        super().__init__(repo, settings)
        self.repo = repo if isinstance(repo, PenaltyRepository) else PenaltyRepository(repo.session)

    async def kind(self, type_id: int) -> PenaltyType:
        return await self.repo.get(PenaltyType, type_id)

    async def view(self, actor: User, penalty_id: int, version: int) -> Penalty:
        await self.actor(actor)
        penalty = await self.repo.get(Penalty, penalty_id)
        self.repo.version(penalty, version)
        if penalty.deleted_at:
            raise StaleAction()
        return penalty

    async def users(self) -> list[User]:
        return await self.user_repo.participants()

    async def types(
        self, include_inactive: bool = False, page: int = 0
    ) -> tuple[list[PenaltyType], bool]:
        return await self.repo.types(include_inactive, page)

    async def preview(
        self, type_id: int, quantity: int | None, manual: int | None
    ) -> dict[str, Any]:
        kind = await self.repo.get(PenaltyType, type_id)
        if not kind.is_active:
            raise DomainError("Этот тип штрафа выключен.")
        points = penalty_points(kind.calculation_type, kind.rate, quantity, manual)
        return {
            "type_id": kind.id,
            "type_version": kind.version,
            "name": kind.name,
            "description": kind.description,
            "calculation_type": kind.calculation_type,
            "rate": kind.rate,
            "quantity": quantity,
            "points": points,
        }

    @atomic
    async def create(
        self,
        actor: User,
        target_id: int,
        preview: dict[str, Any],
        key: str,
        confirmed: bool = False,
        comment: str | None = None,
    ) -> Penalty:
        actor = await self.actor(actor)
        if confirmed is not True:
            raise DomainError("Сначала подтверди штраф.")
        key = operation_key(key)
        try:
            draft = PenaltyPreview.model_validate(preview)
        except ValidationError:
            raise DomainError("Недопустимый черновик штрафа. Создай его заново.") from None
        if comment is not None:
            comment = text_field(comment, 1500, "Комментарий")
        await self.repo.operation_lock(key)
        existing = await self.repo.by_operation(key)
        if existing:
            if existing.created_by_user_id != actor.id:
                raise DomainError("Доступ запрещён.")
            return existing
        target = await self.repo.get(User, target_id)
        if target.id not in [u.id for u in await self.user_repo.participants()]:
            raise DomainError("Пользователь недоступен.")
        kind = await self.repo.get(PenaltyType, draft.type_id, lock=True)
        self.repo.version(kind, draft.type_version)
        if not kind.is_active:
            raise DomainError("Этот тип штрафа выключен. Выбери другой.")
        if (draft.name, draft.description, draft.calculation_type, draft.rate) != (
            kind.name,
            kind.description,
            kind.calculation_type,
            kind.rate,
        ):
            raise DomainError("Настройки изменились. Подтверди штраф заново.")
        manual = draft.points if kind.calculation_type == "MANUAL" else None
        points = penalty_points(kind.calculation_type, kind.rate, draft.quantity, manual)
        if points != draft.points:
            raise DomainError("Настройки изменились. Подтверди штраф заново.")
        penalty = Penalty(
            target_user_id=target.id,
            created_by_user_id=actor.id,
            penalty_type_id=kind.id,
            name_snapshot=kind.name,
            description_snapshot=kind.description,
            calculation_type_snapshot=kind.calculation_type,
            rate_snapshot=kind.rate,
            quantity=draft.quantity,
            points=points,
            comment=comment,
            operation_key=key,
        )
        self.repo.add(penalty)
        await self.repo.flush()
        self.repo.add(
            ScoreEvent(
                user_id=target.id,
                category="penalty",
                points=-points,
                effective_at=penalty.created_at,
                operation_key=f"penalty:{penalty.id}:create",
            )
        )
        self.repo.audit(
            actor,
            penalty,
            "create",
            {},
            {"points": points, "target_id": target.id, "rate": kind.rate},
        )
        self.repo.add(
            Notification(
                recipient_user_id=target.id,
                kind="penalty_created",
                payload={
                    "penalty_id": penalty.id,
                    "name": penalty.name_snapshot,
                    "points": penalty.points,
                    "actor": actor.display_name,
                },
                dedupe_key=f"penalty:{penalty.id}",
                available_at=utcnow(),
            )
        )
        await self.repo.flush()
        return penalty

    @atomic
    async def delete(
        self, actor: User, penalty_id: int, version: int, confirmed: bool = False
    ) -> None:
        actor = await self.actor(actor)
        if confirmed is not True:
            raise DomainError("Подтверди удаление штрафа.")
        penalty = await self.repo.get(Penalty, penalty_id, lock=True)
        self.repo.version(penalty, version)
        if penalty.deleted_at:
            raise DomainError("Штраф уже удалён.")
        penalty.deleted_at = utcnow()
        penalty.version += 1
        self.repo.add(
            ScoreEvent(
                user_id=penalty.target_user_id,
                category="penalty",
                points=penalty.points,
                effective_at=penalty.created_at,
                operation_key=f"penalty:{penalty.id}:delete",
            )
        )
        self.repo.audit(actor, penalty, "delete", {"points": penalty.points}, {"deleted": True})
        await self.repo.flush()

    @atomic
    async def configure(
        self, actor: User, type_id: int | None, version: int | None = None, **changes: Any
    ) -> PenaltyType:
        actor = await self.actor(actor)
        allowed = {"name", "emoji", "description", "calculation_type", "rate", "is_active"}
        if not changes or not set(changes) <= allowed:
            raise DomainError("Недопустимые настройки.")
        if type_id is not None:
            kind = await self.repo.get(PenaltyType, type_id, lock=True)
            self.repo.version(kind, version or 0)
            old = {field: getattr(kind, field) for field in changes}
        else:
            kind = PenaltyType(
                name="",
                emoji="⚠️",
                description="",
                calculation_type="MANUAL",
                rate=0,
                is_active=True,
                sort_order=100,
            )
            self.repo.add(kind)
            old = {}
        for field, value in changes.items():
            if field in ("name", "emoji", "description"):
                if not isinstance(value, str):
                    raise DomainError("Введи текстовое значение.")
                value = (
                    ""
                    if field == "description" and value == ""
                    else text_field(
                        str(value), {"name": 80, "emoji": 16, "description": 500}[field], field
                    )
                )
            elif field == "rate":
                if type(value) is not int or not 0 <= value <= 1000000:
                    raise DomainError("Ставка должна быть целым числом от 0 до 1 000 000.")
            elif field == "calculation_type":
                if value not in ("PER_MINUTE", "FIXED", "MANUAL"):
                    raise DomainError("Недопустимый способ расчёта.")
            elif field == "is_active" and type(value) is not bool:
                raise DomainError("Недопустимое состояние.")
            setattr(kind, field, value)
        text_field(kind.name, 80, "Название")
        if kind.calculation_type != "MANUAL" and kind.rate <= 0:
            raise DomainError("Для этого способа расчёта нужна ставка больше нуля.")
        if type_id:
            kind.version += 1
        await self.repo.flush()
        self.repo.audit(
            actor, kind, "configure", old, {field: getattr(kind, field) for field in changes}
        )
        return kind

    async def list(self, page: int = 0) -> tuple[list[Penalty], bool]:
        return await self.repo.list(page)
