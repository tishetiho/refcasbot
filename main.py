import asyncio
import random
import aiosqlite
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.utils.keyboard import InlineKeyboardBuilder

# --- КОНФИГУРАЦИЯ ---
TOKEN = "8673476742:AAE4GeCi3x__yVgU3VKdtSYIvqfaTOaraJE"
CHANNEL_ID = -1003884251721  # ID твоего канала (обязательно -100...)
CHANNEL_URL = "https://t.me/ludomove"
DB_NAME = "bot_database.db"

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- РАБОТА С БАЗОЙ ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS users 
                          (user_id INTEGER PRIMARY KEY, balance INTEGER DEFAULT 0, 
                           energy INTEGER DEFAULT 3, referred_by INTEGER)''')
        await db.commit()

async def get_user(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT balance, energy, referred_by FROM users WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone()

async def add_user(user_id, referrer_id=None):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id, referred_by) VALUES (?, ?)", (user_id, referrer_id))
        if referrer_id:
            # Бонус пригласившему: +5 энергии и 10 звезд
            await db.execute("UPDATE users SET energy = energy + 5, balance = balance + 10 WHERE user_id = ?", (referrer_id,))
        await db.commit()

# --- ПРОВЕРКА ПОДПИСКИ ---
async def is_subscribed(user_id):
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except:
        return False

# --- ОБРАБОТЧИКИ ---

@dp.message(Command("start"))
async def start_cmd(message: types.Message, command: CommandObject):
    user_id = message.from_user.id
    referrer_id = None
    
    # Реферальная логика
    if command.args and command.args.isdigit():
        referrer_id = int(command.args)
        if referrer_id == user_id: referrer_id = None

    await add_user(user_id, referrer_id)
    
    if await is_subscribed(user_id):
        await show_main_menu(message)
    else:
        builder = InlineKeyboardBuilder()
        builder.row(types.InlineKeyboardButton(text="1. Подписаться на канал", url=CHANNEL_URL))
        builder.row(types.InlineKeyboardButton(text="2. ✅ Проверить подписку", callback_data="check_sub"))
        await message.answer("🚀 Чтобы начать выигрывать 'Звезды', подпишись на наш канал!", reply_markup=builder.as_markup())

async def show_main_menu(message: types.Message):
    data = await get_user(message.from_user.id)
    balance, energy, _ = data
    text = (f"🎰 **ГЛАВНОЕ МЕНЮ**\n\n"
            f"💰 Баланс: {balance} ⭐\n"
            f"⚡ Энергия: {energy} (1 круток = 1⚡)\n\n"
            f"Приглашай друзей, чтобы получить энергию!")
    
    kb = [
        [types.KeyboardButton(text="🎰 ИГРАТЬ (Рулетка)")],
        [types.KeyboardButton(text="👥 Реферальная ссылка"), types.KeyboardButton(text="💎 Вывод Stars")]
    ]
    await message.answer(text, reply_markup=types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True), parse_mode="Markdown")

@dp.message(F.text == "🎰 ИГРАТЬ (Рулетка)")
async def play_game(message: types.Message):
    user_id = message.from_user.id
    if not await is_subscribed(user_id):
        await message.answer("❌ Сначала подпишись на канал!")
        return

    data = await get_user(user_id)
    if data[1] <= 0:
        await message.answer("🪫 Закончилась энергия! Пригласи друга по своей ссылке, чтобы получить +5⚡")
        return

    # Снимаем энергию
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET energy = energy - 1 WHERE user_id = ?", (user_id,))
        await db.commit()

    msg = await message.answer_dice(emoji="🎰")
    await asyncio.sleep(3.5)

    if msg.dice.value in [1, 22, 43, 64]: # Выигрышные комбинации анимации
        win = random.randint(50, 200)
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (win, user_id))
            await db.commit()
        await message.answer(f"🔥 ПОБЕДА! +{win} ⭐ на баланс!")
    else:
        await message.answer("💨 Мимо! Попробуй еще раз.")

@dp.message(F.text == "👥 Реферальная ссылка")
async def ref_link(message: types.Message):
    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start={message.from_user.id}"
    await message.answer(f"🔗 Твоя ссылка для приглашения:\n`{link}`\n\n"
                         f"🎁 За каждого друга ты получишь:\n"
                         f"+5 ⚡ (Энергия для игры)\n"
                         f"+10 ⭐ (Звезды на баланс)", parse_mode="Markdown")

@dp.message(F.text == "💎 Вывод Stars")
async def withdraw(message: types.Message):
    await message.answer("📤 Вывод доступен от **1000 ⭐**\n"
                         "Продолжай играть и приглашать друзей, чтобы накопить минимальную сумму!", parse_mode="Markdown")

@dp.callback_query(F.data == "check_sub")
async def check_cb(callback: types.CallbackQuery):
    if await is_subscribed(callback.from_user.id):
        await callback.message.delete()
        await show_main_menu(callback.message)
    else:
        await callback.answer("❌ Подписка не найдена!", show_alert=True)

# --- ЗАПУСК ---
async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
