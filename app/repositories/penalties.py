from sqlalchemy import select

from app.models import Penalty, PenaltyType
from app.repositories.base import Repository
from app.services.rules import DomainError


class PenaltyRepository(Repository):
    async def by_operation(self, key: str) -> Penalty | None:
        return await self.session.scalar(select(Penalty).where(Penalty.operation_key == key))

    async def types(
        self, include_inactive: bool = False, page: int = 0
    ) -> tuple[list[PenaltyType], bool]:
        self.page(page)
        query = (
            select(PenaltyType)
            .order_by(PenaltyType.sort_order, PenaltyType.id)
            .offset(page * 5)
            .limit(6)
        )
        if not include_inactive:
            query = query.where(PenaltyType.is_active.is_(True))
        rows = list(await self.session.scalars(query))
        return rows[:5], len(rows) > 5

    async def list(self, page: int = 0) -> tuple[list[Penalty], bool]:
        self.page(page)
        rows = list(
            await self.session.scalars(
                select(Penalty)
                .where(Penalty.deleted_at.is_(None))
                .order_by(Penalty.id.desc())
                .offset(page * 5)
                .limit(6)
            )
        )
        return rows[:5], len(rows) > 5

    @staticmethod
    def page(value: int) -> None:
        if type(value) is not int or not 0 <= value <= 100000:
            raise DomainError("Недопустимая страница.")
