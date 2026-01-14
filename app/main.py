import asyncio
import logging
from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.db.connection import get_db
from app.db.repository import Repository
from app.bot.handlers import router as user_router
from app.bot.admin_handlers import router as admin_router
from app.services.openai_client import OpenAIClient
from app.services.summary import build_summary


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()

    # Подключаем роутеры (админ первым)
    dp.include_router(admin_router)
    dp.include_router(user_router)

    db = await get_db(use_fake=settings.use_fake_db, dsn=settings.pg_dsn)
    repo = Repository(
        db=db,
        tz=settings.tz,
        free_limit=settings.free_limit,
        daily_hard_limit=settings.daily_hard_limit,
    )
    llm = OpenAIClient(api_key=settings.openai_api_key, model=settings.openai_model)

    @dp.update.outer_middleware()
    async def inject(handler, event, data):
        data["repo"] = repo
        data["llm"] = llm
        data["settings"] = settings
        return await handler(event, data)

    scheduler = AsyncIOScheduler(timezone=settings.tz)

    async def daily_job():
        chat_ids = await repo.list_chat_ids()
        for chat_id in chat_ids:
            dialog = await repo.get_day_dialog_text(chat_id)
            summary = build_summary(llm, dialog)
            await repo.save_daily_summary(chat_id, summary)

    scheduler.add_job(daily_job, CronTrigger(hour=0, minute=0))
    scheduler.start()

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())