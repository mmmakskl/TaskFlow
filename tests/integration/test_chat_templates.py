"""Template creation, editing and use stay in authenticated ordinary chat messages."""

import pytest
from sqlalchemy import func, select

from app.models import Task, TaskTemplate
from tests.integration.test_form_navigation import data_for
from tests.integration.test_web_and_bot import BotDriver

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "mode,label,base,unit,quantity,points",
    [
        ("FIXED", "Фиксированная цена", None, None, None, 600),
        ("PER_TIME", "По времени", 30, None, 45, 900),
        ("PER_QUANTITY", "По количеству", 10, "страниц", 15, 900),
    ],
)
async def test_chat_creates_uses_and_edits_all_template_types(
    factory, settings, mode, label, base, unit, quantity, points
):
    driver = BotDriver(factory, settings)
    await driver.text(1001, "/templates")
    await driver.press(1001, "Новый шаблон")
    await driver.text(1001, f"Chat {mode}")
    await driver.text(1001, "600")
    await driver.press(1001, "Не указывать")
    mode_callback = data_for(driver, 1001, label)
    await driver.press(1001, data=mode_callback)
    if base:
        await driver.text(1001, "0")
        assert "⚠️" in driver.screen(1001).text
        await driver.text(1001, str(base))
    if unit:
        await driver.text(1001, unit)
    async with factory() as session:
        template = await session.scalar(
            select(TaskTemplate).where(TaskTemplate.title == f"Chat {mode}")
        )
        template_id = template.id
        assert (template.pricing_mode, template.base_quantity, template.unit_label) == (
            mode,
            base,
            unit,
        )
    # A duplicate final mode callback must not create another template or rotate its price.
    await driver.press(1001, data=mode_callback)
    async with factory() as session:
        assert (
            await session.scalar(
                select(func.count(TaskTemplate.id)).where(TaskTemplate.title == f"Chat {mode}")
            )
            == 1
        )
    await driver.text(1001, "/templates")
    await driver.press(1001, f"Chat {mode}")
    await driver.press(1001, "Создать задачу")
    if quantity:
        await driver.press(1001, "Объём")
        await driver.text(1001, str(quantity))
    saved = await driver.press(1001, "✅ Создать")
    await driver.press(1001, data=saved)
    async with factory() as session:
        task = await session.scalar(select(Task).where(Task.template_id == template_id))
        assert task.status == "TODO" and task.rated_points == points
        assert (
            await session.scalar(select(func.count(Task.id)).where(Task.template_id == template_id))
            == 1
        )
    # Both configured participants can manage templates; mode edits are committed atomically.
    await driver.text(1002, "/templates")
    await driver.press(1002, f"Chat {mode}")
    await driver.press(1002, "Способ расчёта")
    await driver.press(1002, "По количеству")
    await driver.text(1002, "20")
    async with factory() as session:
        template = await session.get(TaskTemplate, template_id)
        assert (
            template.pricing_mode == mode
        )  # Unit is still needed before committing the new config.
    await driver.text(1002, "штук")
    async with factory() as session:
        template = await session.get(TaskTemplate, template_id)
        assert (template.pricing_mode, template.base_quantity, template.unit_label) == (
            "PER_QUANTITY",
            20,
            "штук",
        )
    await driver.bot.session.close()
