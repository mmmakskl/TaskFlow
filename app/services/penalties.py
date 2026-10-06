from typing import Any

from sqlalchemy import select

from app.models import Penalty, PenaltyType, ScoreEvent, User
from app.repositories.base import Repository
from app.services.rules import DomainError, penalty_points, text_field
from app.utils.time import utcnow


class PenaltyService:
    def __init__(self, repo: Repository) -> None:
        self.repo = repo

    async def kind(self, type_id: int) -> PenaltyType:
        return await self.repo.get(PenaltyType, type_id)

    async def users(self) -> list[User]:
        return await self.repo.users()

    async def types(
        self, include_inactive: bool = False, page: int = 0
    ) -> tuple[list[PenaltyType], bool]:
        if not 0 <= page <= 100000:
            raise DomainError("Недопустимая страница.")
        query = (
            select(PenaltyType)
            .order_by(PenaltyType.sort_order, PenaltyType.id)
            .offset(page * 5)
            .limit(6)
        )
        if not include_inactive:
            query = query.where(PenaltyType.is_active.is_(True))
        rows = list(await self.repo.session.scalars(query))
        return rows[:5], len(rows) > 5

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

    async def create(
        self,
        actor: User,
        target_id: int,
        preview: dict[str, Any],
        key: str,
        confirmed: bool = False,
        comment: str | None = None,
    ) -> Penalty:
        if not confirmed:
            raise DomainError("Сначала подтверди штраф.")
        await self.repo.operation_lock(key)
        existing = await self.repo.session.scalar(
            select(Penalty).where(Penalty.operation_key == key)
        )
        if existing:
            if existing.created_by_user_id != actor.id:
                raise DomainError("Доступ запрещён.")
            return existing
        target = await self.repo.get(User, target_id)
        if target.id not in [u.id for u in await self.repo.users()]:
            raise DomainError("Пользователь недоступен.")
        kind = await self.repo.get(PenaltyType, int(preview["type_id"]), lock=True)
        self.repo.version(kind, int(preview["type_version"]))
        if not kind.is_active:
            raise DomainError("Этот тип штрафа выключен. Выбери другой.")
        manual = preview["points"] if kind.calculation_type == "MANUAL" else None
        points = penalty_points(kind.calculation_type, kind.rate, preview.get("quantity"), manual)
        if points != preview["points"]:
            raise DomainError("Настройки изменились. Подтверди штраф заново.")
        penalty = Penalty(
            target_user_id=target.id,
            created_by_user_id=actor.id,
            penalty_type_id=kind.id,
            name_snapshot=kind.name,
            description_snapshot=kind.description,
            calculation_type_snapshot=kind.calculation_type,
            rate_snapshot=kind.rate,
            quantity=preview.get("quantity"),
            points=points,
            comment=comment,
            operation_key=key,
        )
        self.repo.session.add(penalty)
        await self.repo.session.flush()
        self.repo.session.add(
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
        await self.repo.session.flush()
        return penalty

    async def delete(
        self, actor: User, penalty_id: int, version: int, confirmed: bool = False
    ) -> None:
        if not confirmed:
            raise DomainError("Подтверди удаление штрафа.")
        penalty = await self.repo.get(Penalty, penalty_id, lock=True)
        self.repo.version(penalty, version)
        if penalty.deleted_at:
            raise DomainError("Штраф уже удалён.")
        penalty.deleted_at = utcnow()
        penalty.version += 1
        self.repo.session.add(
            ScoreEvent(
                user_id=penalty.target_user_id,
                category="penalty",
                points=penalty.points,
                effective_at=penalty.created_at,
                operation_key=f"penalty:{penalty.id}:delete",
            )
        )
        self.repo.audit(actor, penalty, "delete", {"points": penalty.points}, {"deleted": True})
        await self.repo.session.flush()

    async def configure(
        self, actor: User, type_id: int | None, version: int | None = None, **changes: Any
    ) -> PenaltyType:
        if type_id:
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
            self.repo.session.add(kind)
            old = {}
        allowed = {"name", "emoji", "description", "calculation_type", "rate", "is_active"}
        if not set(changes) <= allowed:
            raise DomainError("Недопустимые настройки.")
        for field, value in changes.items():
            if field in ("name", "emoji", "description"):
                value = text_field(
                    str(value), {"name": 80, "emoji": 16, "description": 500}[field], field
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
        if kind.calculation_type != "MANUAL" and kind.rate <= 0:
            raise DomainError("Для этого способа расчёта нужна ставка больше нуля.")
        if type_id:
            kind.version += 1
        await self.repo.session.flush()
        self.repo.audit(
            actor, kind, "configure", old, {field: getattr(kind, field) for field in changes}
        )
        return kind

    async def list(self, page: int = 0) -> tuple[list[Penalty], bool]:
        if not 0 <= page <= 100000:
            raise DomainError("Недопустимая страница.")
        rows = list(
            await self.repo.session.scalars(
                select(Penalty)
                .where(Penalty.deleted_at.is_(None))
                .order_by(Penalty.id.desc())
                .offset(page * 5)
                .limit(6)
            )
        )
        return rows[:5], len(rows) > 5
