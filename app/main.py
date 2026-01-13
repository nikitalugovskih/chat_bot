import asyncio
from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.db.connection import get_db
from app.db.repository import Repository
from app.bot.handlers import router
from app.services.openai_client import OpenAIClient
from app.services.summary import build_summary

async def main():
    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()

    # DB + repo + llm
    db = get_db(use_fake=settings.use_fake_db)
    repo = Repository(
        db=db,
        tz=settings.tz,
        daily_limit_free=settings.daily_limit_free,
        daily_hard_limit=settings.daily_hard_limit,
    )
    llm = OpenAIClient(api_key=settings.openai_api_key, model=settings.openai_model)

    # dependency injection (простой вариант через middleware-like context)
    @dp.update.outer_middleware()
    async def inject(handler, event, data):
        data["repo"] = repo
        data["llm"] = llm
        return await handler(event, data)

    dp.include_router(router)

    # scheduler: ежедневно в 00:00 по МСК делаем выжимку по каждому chat_id
    scheduler = AsyncIOScheduler(timezone=settings.tz)

    async def daily_job():
        # берём всех пользователей
        for chat_id in list(db.user_subscriptions.keys()):
            dialog = repo.get_day_dialog_text(chat_id)
            summary = build_summary(llm, dialog)
            repo.save_daily_summary(chat_id, summary)

    scheduler.add_job(daily_job, CronTrigger(hour=0, minute=0))
    scheduler.start()

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())