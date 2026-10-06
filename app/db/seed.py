import asyncio

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings
from app.db.session import database
from app.models import PenaltyType, User
from app.utils.time import utcnow

DEFAULT_PENALTIES = [
    (
        "instagram",
        "Instagram",
        "📱",
        "PER_MINUTE",
        100,
        "Штраф за каждую штрафную минуту использования Instagram.",
    ),
    (
        "tiktok",
        "TikTok",
        "🎵",
        "PER_MINUTE",
        100,
        "Штраф за каждую штрафную минуту использования TikTok.",
    ),
    (
        "youtube",
        "YouTube",
        "▶️",
        "PER_MINUTE",
        10,
        "Штраф за каждую штрафную минуту использования YouTube.",
    ),
    (
        "late",
        "Опоздание",
        "⏰",
        "PER_MINUTE",
        50,
        "Штраф за каждую минуту опоздания к согласованному времени.",
    ),
    (
        "missed",
        "Пропущенная задача",
        "❌",
        "MANUAL",
        0,
        "Штраф за пропущенную или проигнорированную согласованную задачу. Размер указывается при создании штрафа.",
    ),
    (
        "deadline",
        "Срыв дедлайна",
        "📅",
        "MANUAL",
        0,
        "Штраф за нарушение согласованного дедлайна. Размер указывается при создании штрафа.",
    ),
    (
        "other",
        "Другое",
        "📝",
        "MANUAL",
        0,
        "Пользовательский штраф для ситуации, не подходящей под стандартные категории.",
    ),
]


async def seed(session: AsyncSession, settings: Settings) -> None:
    for role, telegram_id, name in (
        ("max", settings.user_max_id, settings.user_max_name),
        ("vika", settings.user_vika_id, settings.user_vika_name),
    ):
        await session.execute(
            insert(User)
            .values(
                role=role,
                telegram_id=telegram_id,
                display_name=name,
                timezone=settings.default_timezone,
                weekly_target=settings.default_weekly_target,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            .on_conflict_do_update(
                index_elements=[User.role],
                set_={"telegram_id": telegram_id, "display_name": name, "updated_at": utcnow()},
            )
        )
    for order, (key, name, emoji, calculation, rate, description) in enumerate(DEFAULT_PENALTIES):
        await session.execute(
            insert(PenaltyType)
            .values(
                seed_key=key,
                name=name,
                emoji=emoji,
                calculation_type=calculation,
                rate=rate,
                description=description,
                sort_order=order,
                is_active=True,
                version=1,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            .on_conflict_do_nothing(index_elements=[PenaltyType.seed_key])
        )
    users = list(await session.scalars(select(User)))
    if len(users) != 2:
        raise RuntimeError("Expected exactly two configured users")


async def main() -> None:
    settings = Settings()
    engine, factory = database(settings)
    async with factory.begin() as session:
        await seed(session, settings)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
