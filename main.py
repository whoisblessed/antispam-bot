import asyncio
import logging
import os

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import ChatMemberUpdatedFilter, Command
from aiogram.filters.chat_member_updated import IS_MEMBER, IS_NOT_MEMBER
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ACTION_MODE = os.getenv("ACTION_MODE", "delete")
ADMIN_USER = os.getenv("ADMIN_USERNAME", "admin")
VERIFY_SEC = int(os.getenv("VERIFY_TIMEOUT", "180"))
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

spam_logger = logging.getLogger("spam")
fh = logging.FileHandler("spam.log", encoding="utf-8")
fh.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
spam_logger.addHandler(fh)
spam_logger.setLevel(logging.INFO)

STOP_WORDS = [
    "казино", "заработок онлайн", "биткоин", "бесплатно", "промокод",
    "скидки", "переходи по ссылке", "инвестиции", "крипта", "займы",
    "заработай", "67", "ставки на спорт", "лотерея",
]

pending = {}
router = Router()


async def ask_llm_is_spam(text):
    if not LLM_API_KEY:
        return None

    prompt = (
        'Является ли следующее сообщение спамом или вредоносным? '
        'Ответь только "да" или "нет".\n'
        f'Сообщение: {text}'
    )

    async def call():
        url = f"{LLM_BASE_URL.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": LLM_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 10,
            "temperature": 0,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                resp.raise_for_status()
                data = await resp.json()
        return data["choices"][0]["message"]["content"].strip().lower()

    try:
        answer = await asyncio.wait_for(call(), timeout=8)
        return "да" in answer[:10]
    except asyncio.TimeoutError:
        logging.warning("LLM: таймаут")
        return None
    except Exception as e:
        logging.error("LLM error: %s", e)
        return None


@router.chat_member(ChatMemberUpdatedFilter(IS_NOT_MEMBER >> IS_MEMBER))
async def new_member(event: ChatMemberUpdated, bot: Bot):
    user = event.new_chat_member.user
    if user.is_bot:
        return

    chat_id = event.chat.id

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Я человек", callback_data=f"verify_{user.id}"),
    ]])

    msg = await bot.send_message(
        chat_id,
        f"{user.mention_html()}, подтвердите, что вы не бот.\n"
        f"Нажмите кнопку или напишите /verify.\n"
        f"У вас <b>{VERIFY_SEC // 60} мин.</b>",
        reply_markup=kb,
        parse_mode="HTML",
    )

    task = asyncio.create_task(_kick_later(bot, chat_id, user.id, msg.message_id))
    pending[user.id] = {"chat_id": chat_id, "msg_id": msg.message_id, "task": task}


async def _kick_later(bot, chat_id, user_id, msg_id):
    await asyncio.sleep(VERIFY_SEC)
    if user_id not in pending:
        return
    del pending[user_id]
    try:
        await bot.ban_chat_member(chat_id, user_id)
        await bot.unban_chat_member(chat_id, user_id)
        await bot.delete_message(chat_id, msg_id)
        await bot.send_message(chat_id, "Пользователь не прошёл верификацию и удалён.")
    except Exception as e:
        logging.error("Ошибка кика %s: %s", user_id, e)


async def _verify_user(bot, user_id):
    entry = pending.pop(user_id, None)
    if entry is None:
        return False
    entry["task"].cancel()
    try:
        await bot.restrict_chat_member(
            entry["chat_id"], user_id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_audios=True,
                can_send_documents=True,
                can_send_photos=True,
                can_send_videos=True,
                can_send_video_notes=True,
                can_send_voice_notes=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
                can_invite_users=True,
            ),
        )
        await bot.delete_message(entry["chat_id"], entry["msg_id"])
    except Exception as e:
        logging.error("Ошибка верификации %s: %s", user_id, e)
        return False
    return True


@router.callback_query(lambda c: c.data and c.data.startswith("verify_"))
async def verify_button(call: CallbackQuery, bot: Bot):
    target_id = int(call.data.split("_")[1])
    if call.from_user.id != target_id:
        await call.answer("Эта кнопка не для вас!", show_alert=True)
        return
    ok = await _verify_user(bot, target_id)
    await call.answer("Добро пожаловать!" if ok else "Время вышло.", show_alert=not ok)


@router.message(Command("verify"))
async def verify_cmd(message: Message, bot: Bot):
    ok = await _verify_user(bot, message.from_user.id)
    if ok:
        await message.reply("Верификация пройдена!")


def has_stop_word(text):
    return any(w in text.lower() for w in STOP_WORDS)


@router.message(F.text)
async def check_message(message: Message, bot: Bot):
    if not message.text or not message.from_user or message.from_user.is_bot:
        return
    if message.text.startswith("/"):
        return

    try:
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        if member.status in ("administrator", "creator"):
            return
    except Exception as e:
        logging.error("Ошибка получения статуса: %s", e)
        return

    if not has_stop_word(message.text):
        return

    is_spam = await ask_llm_is_spam(message.text)

    if is_spam is None:
        logging.warning("LLM недоступна — решение по стоп-словам")
        is_spam = True

    if not is_spam:
        return

    username = message.from_user.username or f"id{message.from_user.id}"
    user_id = message.from_user.id
    thread = message.message_thread_id

    spam_logger.info("user_id=%s | @%s | chat_id=%s | text=%s", user_id, username, message.chat.id, message.text[:300])

    if ACTION_MODE == "delete":
        try:
            await message.delete()
            await bot.ban_chat_member(message.chat.id, user_id)
            await bot.unban_chat_member(message.chat.id, user_id)
            await bot.send_message(
                message.chat.id,
                f"Спам удалён. Пользователь @{username} заблокирован.",
                message_thread_id=thread,
            )
        except Exception as e:
            logging.error("Ошибка удаления спама: %s", e)
    else:
        if message.chat.username:
            msg_link = f"https://t.me/{message.chat.username}/{message.message_id}"
        else:
            clean_id = str(message.chat.id)[4:]
            msg_link = f"https://t.me/c/{clean_id}/{message.message_id}"

        try:
            await bot.send_message(
                message.chat.id,
                f"@{ADMIN_USER}, обнаружен спам от пользователя @{username}!\n"
                f'<a href="{msg_link}">Ссылка на сообщение</a>',
                message_thread_id=thread,
                parse_mode="HTML",
            )
        except Exception as e:
            logging.error("Ошибка уведомления администратора: %s", e)


async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot, allowed_updates=["message", "callback_query", "chat_member"])


if __name__ == "__main__":
    asyncio.run(main())