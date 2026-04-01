import asyncio
import random
import aiosqlite
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.utils.keyboard import InlineKeyboardBuilder

# --- КОНФИГУРАЦИЯ ---
TOKEN = "8673476742:AAE4GeCi3x__yVgU3VKdtSYIvqfaTOaraJE"
CHANNEL_ID = -1003884251721 
CHANNEL_URL = "https://t.me/ludomove"
DB_NAME = "bot_database.db"

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- РАБОТА С БАЗОЙ ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS users 
                          (user_id INTEGER PRIMARY KEY, 
                           balance INTEGER DEFAULT 0, 
                           energy INTEGER DEFAULT 3, 
                           referred_by INTEGER,
                           total_won INTEGER DEFAULT 0,
                           last_bonus TEXT DEFAULT '2000-01-01 00:00:00')''') # Время по умолчанию в прошлом
        await db.commit()

async def get_user_data(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT balance, energy, referred_by, total_won FROM users WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone()

async def add_user(user_id, referrer_id=None):
    async with aiosqlite.connect(DB_NAME) as db:
        # Регистрируем пользователя
        await db.execute("INSERT OR IGNORE INTO users (user_id, referred_by) VALUES (?, ?)", (user_id, referrer_id))
        
        # Если пришел по реф-ссылке и его еще нет в базе
        if referrer_id:
            # Проверяем, не был ли этот пользователь уже зарегистрирован (защита от абуза)
            async with db.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,)) as cursor:
                check = await cursor.fetchone()
                if not check: 
                    # Начисляем ТОЛЬКО энергию (+5⚡) пригласившему
                    await db.execute("UPDATE users SET energy = energy + 5 WHERE user_id = ?", (referrer_id,))
        
        await db.commit()

async def add_user(user_id, referrer_id=None):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id, referred_by) VALUES (?, ?)", (user_id, referrer_id))
        if referrer_id:
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
    referrer_id = int(command.args) if command.args and command.args.isdigit() and int(command.args) != user_id else None

    await add_user(user_id, referrer_id)
    
    if await is_subscribed(user_id):
        await show_main_menu(message)
    else:
        builder = InlineKeyboardBuilder()
        builder.row(types.InlineKeyboardButton(text="1. Подписаться на канал", url=CHANNEL_URL))
        builder.row(types.InlineKeyboardButton(text="2. ✅ Проверить подписку", callback_data="check_sub"))
        await message.answer("🚀 Добро пожаловать! Подпишись на канал, чтобы начать игру.", reply_markup=builder.as_markup())

async def show_main_menu(message: types.Message):
    kb = [
        [types.KeyboardButton(text="🎰 ИГРАТЬ (Рулетка)")],
        [types.KeyboardButton(text="👤 Профиль"), types.KeyboardButton(text="🎁 Ежедневный бонус")],
        [types.KeyboardButton(text="📊 Статистика"), types.KeyboardButton(text="👥 Рефералы")],
        [types.KeyboardButton(text="💎 Вывод")]
    ]
    await message.answer("Главное меню:", reply_markup=types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))
    
@dp.message(F.text == "👤 Профиль")
async def profile_handler(message: types.Message):
    data = await get_user_data(message.from_user.id)
    if not data: return
    
    balance, energy, _, total_won = data
    text = (f"👤 **ВАШ ПРОФИЛЬ**\n\n"
            f"🆔 ID: `{message.from_user.id}`\n"
            f"💰 Текущий баланс: **{balance} ⭐**\n"
            f"⚡ Энергия: **{energy}**\n"
            f"🏆 Всего выиграно: **{total_won} ⭐**\n\n"
            f"Звезды можно вывести, накопив 1000!")
    await message.answer(text, parse_mode="Markdown")

    from datetime import datetime, timedelta

@dp.message(F.text == "🎁 Ежедневный бонус")
async def daily_bonus(message: types.Message):
    user_id = message.from_user.id
    
    # Проверка подписки (обязательно!)
    if not await is_subscribed(user_id):
        await message.answer("❌ Бонус доступен только подписчикам канала!")
        return

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT last_bonus FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            last_bonus_time = datetime.strptime(row[0], '%Y-%m-%d %H:%M:%S')

        # Проверяем, прошло ли 24 часа
        if datetime.now() - last_bonus_time >= timedelta(hours=24):
            # Выдаем бонус +1 энергию
            new_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            await db.execute("UPDATE users SET energy = energy + 1, last_bonus = ? WHERE user_id = ?", 
                             (new_time, user_id))
            await db.commit()
            await message.answer("🎁 Вы получили ежедневный бонус: **+1 ⚡ Энергии**!\nВозвращайтесь завтра!")
        else:
            # Считаем, сколько осталось ждать
            next_bonus = last_bonus_time + timedelta(hours=24)
            wait_time = next_bonus - datetime.now()
            hours, remainder = divmod(int(wait_time.total_seconds()), 3600)
            minutes, _ = divmod(remainder, 60)
            await message.answer(f"⏳ Бонус уже получен! Следующий через {hours}ч. {minutes}м.")
            
@dp.message(F.text == "📊 Статистика")
async def stats_handler(message: types.Message):
    total_users, global_won = await get_global_stats()
    text = (f"📊 **СТАТИСТИКА БОТА**\n\n"
            f"👥 Всего игроков: **{total_users}**\n"
            f"💰 Выплачено (выиграно) всего: **{global_won} ⭐**\n"
            f"🟢 Статус выплат: **Работает**\n\n"
            f"Бот стабильно раздает подарки активным подписчикам!")
    await message.answer(text, parse_mode="Markdown")

@dp.message(F.text == "🎰 ИГРАТЬ (Рулетка)")
async def play_game(message: types.Message):
    user_id = message.from_user.id
    if not await is_subscribed(user_id):
        await message.answer("❌ Подписка не найдена!")
        return

    data = await get_user_data(user_id)
    if data[1] <= 0:
        await message.answer("🪫 Нет энергии! Пригласи друга (/ref), чтобы получить +5⚡")
        return

    # Снимаем энергию
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET energy = energy - 1 WHERE user_id = ?", (user_id,))
        await db.commit()

    msg = await message.answer_dice(emoji="🎰")
    await asyncio.sleep(3.5)

    if msg.dice.value in [1, 22, 43, 64]:
        win = random.randint(50, 250)
        async with aiosqlite.connect(DB_NAME) as db:
            # Обновляем и баланс, и общую статистику выигрыша юзера
            await db.execute("UPDATE users SET balance = balance + ?, total_won = total_won + ? WHERE user_id = ?", 
                             (win, win, user_id))
            await db.commit()
        await message.answer(f"🔥 ПОВЕЗЛО! Ты выиграл {win} ⭐")
    else:
        await message.answer("😢 В этот раз пусто. Попробуешь еще?")

@dp.message(F.text == "👥 Рефералы")
async def ref_handler(message: types.Message):
    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start={message.from_user.id}"
    await message.answer(
        f"🔗 **Твоя реферальная ссылка:**\n`{link}`\n\n"
        f"🎁 За каждого приглашенного друга ты получишь:\n"
        f"⚡ **+5 Энергии** для игры в рулетку!\n\n"
        f"Больше друзей — больше шансов сорвать куш! 🔥", 
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "check_sub")
async def check_cb(callback: types.CallbackQuery):
    if await is_subscribed(callback.from_user.id):
        await callback.message.delete()
        await show_main_menu(callback.message)
    else:
        await callback.answer("❌ Сначала подпишись!", show_alert=True)

async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
