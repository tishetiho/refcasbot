import asyncio
import random
import aiosqlite
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.utils.keyboard import InlineKeyboardBuilder

# --- КОНФИГУРАЦИЯ ---
TOKEN = "8673476742:AAE4GeCi3x__yVgU3VKdtSYIvqfaTOaraJE"
CHANNELS = [
    {"id": -1003884251721, "url": "https://t.me/ludomove"},
]
ADMIN_ID = 5078764886
DB_NAME = "bot_database.db"

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- БАЗА ДАННЫХ (С проверкой структуры) ---
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        # Создаем таблицу, если её нет
        await db.execute('''CREATE TABLE IF NOT EXISTS users 
                          (user_id INTEGER PRIMARY KEY, 
                           balance INTEGER DEFAULT 0, 
                           energy INTEGER DEFAULT 3, 
                           referred_by INTEGER,
                           total_won INTEGER DEFAULT 0,
                           last_bonus TEXT DEFAULT '2000-01-01 00:00:00')''')
        
        # ПРОВЕРКА: Если ты запускал старую версию, добавим колонку last_bonus вручную
        try:
            await db.execute("ALTER TABLE users ADD COLUMN last_bonus TEXT DEFAULT '2000-01-01 00:00:00'")
        except:
            pass # Если колонка уже есть, ошибка проигнорируется
            
        try:
            await db.execute("ALTER TABLE users ADD COLUMN total_won INTEGER DEFAULT 0")
        except:
            pass
            
        await db.commit()

async def get_user_data(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row  # ЭТО ОЧЕНЬ ВАЖНО ДЛЯ РАБОТЫ РУЛЕТКИ
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone()
            
async def add_user(user_id, referrer_id=None):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id, referred_by) VALUES (?, ?)", (user_id, referrer_id))
        if referrer_id:
            await db.execute("UPDATE users SET energy = energy + 5 WHERE user_id = ?", (referrer_id,))
        await db.commit()

async def get_global_stats():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(user_id), SUM(total_won) FROM users") as cursor:
            res = await cursor.fetchone()
            return (res[0] or 0), (res[1] or 0)

# --- ПРОВЕРКА ПОДПИСКИ ---
async def is_subscribed(user_id):
    for channel in CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel["id"], user_id=user_id)
            if member.status not in ["member", "administrator", "creator"]:
                return False # Если хотя бы в одном не состоит, проверка не прошла
        except Exception as e:
            print(f"Ошибка проверки канала {channel['id']}: {e}")
            return False # Если бота выкинули из админов канала, доступ закрываем
    return True # Если цикл прошел по всем и не прервался — всё ок

# --- МЕНЮ ---
def main_menu_kb():
    kb = [
        [types.KeyboardButton(text="🎰 ИГРАТЬ (Рулетка)")],
        [types.KeyboardButton(text="👤 Профиль"), types.KeyboardButton(text="🎁 Бонус")],
        [types.KeyboardButton(text="📊 Статистика"), types.KeyboardButton(text="👥 Рефералы")],
        [types.KeyboardButton(text="🎫 Промокод"), types.KeyboardButton(text="💎 Вывод")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def admin_kb():
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="📢 Сделать рассылку", callback_data="admin_broadcast"))
    builder.row(types.InlineKeyboardButton(text="🎫 Создать промокод", callback_data="admin_add_promo"))
    builder.row(types.InlineKeyboardButton(text="📊 Общая статистика", callback_data="admin_stats"))
    return builder.as_markup()
    
# --- ОБРАБОТЧИКИ ---
@dp.message(Command("start"))
async def start_cmd(message: types.Message, command: CommandObject):
    user_id = message.from_user.id
    ref_id = int(command.args) if command.args and command.args.isdigit() and int(command.args) != user_id else None
    await add_user(user_id, ref_id)
    
    if await is_subscribed(user_id):
        await message.answer("✅ Вы подписаны на все каналы! Удачи в игре!", reply_markup=main_menu_kb())
    else:
        builder = InlineKeyboardBuilder()
        # Циклом добавляем все каналы из списка
        for i, channel in enumerate(CHANNELS, 1):
            builder.row(types.InlineKeyboardButton(text=f"Подписаться на Канал #{i}", url=channel["url"]))
        
        builder.row(types.InlineKeyboardButton(text="✅ Проверить все подписки", callback_data="check_sub"))
        await message.answer("🚀 Чтобы начать игру, нужно подписаться на все наши ресурсы:", reply_markup=builder.as_markup())

@dp.message(Command("admin"), F.from_user.id == ADMIN_ID)
async def admin_panel(message: types.Message):
    await message.answer("🛠 **ПАНЕЛЬ УПРАВЛЕНИЯ**\n\nВыбери действие:", reply_markup=admin_kb(), parse_mode="Markdown")
    
@dp.message(F.text == "👤 Профиль")
async def profile_handler(message: types.Message):
    data = await get_user_data(message.from_user.id)
    if not data: await add_user(message.from_user.id); data = await get_user_data(message.from_user.id)
    
    await message.answer(f"👤 **ПРОФИЛЬ**\n\n"
                         f"💰 Баланс: {data['balance']} ⭐\n"
                         f"⚡ Энергия: {data['energy']}\n"
                         f"🏆 Выиграно за всё время: {data['total_won']} ⭐", parse_mode="Markdown")

@dp.message(F.text == "🎁 Бонус")
async def daily_bonus(message: types.Message):
    user_id = message.from_user.id
    if not await is_subscribed(user_id): return await message.answer("❌ Сначала подпишись на канал!")
    
    data = await get_user_data(user_id)
    # Исправляем чтение времени
    last_bonus_str = data['last_bonus']
    last_bonus_time = datetime.strptime(last_bonus_str, '%Y-%m-%d %H:%M:%S')

    if datetime.now() - last_bonus_time >= timedelta(hours=24):
        new_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET energy = energy + 1, last_bonus = ? WHERE user_id = ?", (new_time, user_id))
            await db.commit()
        await message.answer("🎁 Вы получили бонус: **+1 ⚡ Энергии**!", parse_mode="Markdown")
    else:
        delta = (last_bonus_time + timedelta(hours=24)) - datetime.now()
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, _ = divmod(remainder, 60)
        await message.answer(f"⏳ Бонус будет доступен через **{hours}ч. {minutes}м.**", parse_mode="Markdown")

@dp.message(F.text == "💎 Вывод")
async def withdraw_handler(message: types.Message):
    data = await get_user_data(message.from_user.id)
    balance = data['balance']
    if balance >= 1000:
        await message.answer(f"💎 На вашем балансе {balance} ⭐\n\nДля вывода напишите админу: @твой_логин")
    else:
        await message.answer(f"❌ Недостаточно средств.\nМинимум: **1000 ⭐**\nВаш баланс: **{balance} ⭐**", parse_mode="Markdown")

@dp.message(F.text == "🎰 ИГРАТЬ (Рулетка)")
async def play_game(message: types.Message):
    user_id = message.from_user.id
    
    # 1. Проверка подписки
    if not await is_subscribed(user_id): 
        return await message.answer("❌ Сначала подпишитесь на канал!")

    # 2. Получение данных (с подстраховкой)
    data = await get_user_data(user_id)
    if not data: 
        await add_user(user_id)
        data = await get_user_data(user_id)

    # 3. Проверка энергии
    # Используем get(), чтобы бот не падал, если колонка вдруг не прочиталась
    energy = data['energy'] if data else 0
    
    if energy <= 0: 
        return await message.answer("🪫 Нет энергии! Приглашай друзей или жди бонус.")

    # 4. Списание энергии
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET energy = energy - 1 WHERE user_id = ?", (user_id,))
        await db.commit()

    # 5. Списки цитат
    win_quotes = [
        "«Удача — это когда готовность встречается с возможностью.» — Сенека",
        "«Победа — это еще не все, все — это постоянное желание побеждать.» — Винс Ломбарди",
        "«Успех — это идти от ошибки к ошибке без потери энтузиазма.» — Уинстон Черчилль"
    ]
    
    lose_quotes = [
        "«Проигрыш — не потеря семьи, можно пережить.» — Неизвестный",
        "«Наша величайшая слава не в том, чтобы никогда не падать, а в том, чтобы подниматься.» — Конфуций",
        "«Иногда ты выигрываешь, иногда ты учишься.» — Джон Максвелл"
    ]

    # 6. Анимация казино
    msg = await message.answer_dice(emoji="🎰")
    await asyncio.sleep(3.5) # Ждем завершения анимации

    # 7. Логика результата
    # Значения кубика 1, 22, 43, 64 — это три семерки (джекпот) в анимации Telegram
    if msg.dice.value in [1, 22, 43, 64]:
        win = random.randint(1, 15) 
        quote = random.choice(win_quotes)
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET balance = balance + ?, total_won = total_won + ? WHERE user_id = ?", 
                             (win, win, user_id))
            await db.commit()
        await message.answer(f"🎉 **ПОБЕДА!** Ты выиграл **{win} ⭐**\n\n_{quote}_", parse_mode="Markdown")
    else:
        quote = random.choice(lose_quotes)
        await message.answer(f"💨 **Мимо...** Попробуй еще раз!\n\n_{quote}_", parse_mode="Markdown")
        
@dp.message(F.text == "📊 Статистика")
async def stats_handler(message: types.Message):
    users, won = await get_global_stats()
    await message.answer(f"📊 **СТАТИСТИКА БОТА**\n\n👥 Игроков: {users}\n💰 Выиграно всего: {won} ⭐\n✅ Выплаты активны!", parse_mode="Markdown")

@dp.message(F.text == "👥 Рефералы")
async def ref_handler(message: types.Message):
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start={message.from_user.id}"
    await message.answer(f"👥 **РЕФЕРАЛЬНАЯ СИСТЕМА**\n\nПриглашай друзей и получай **+5 ⚡** за каждого!\n\n🔗 Твоя ссылка:\n`{link}`", parse_mode="Markdown")

# Состояния для админки
class AdminStates(StatesGroup):
    waiting_for_broadcast = State()
    waiting_for_promo = State()
    
class UserStates(StatesGroup):
    waiting_for_promo_activation = State()
    
# 1. Сначала ловим нажатие кнопки
@dp.message(F.text == "🎫 Промокод")
async def promo_start_activation(message: types.Message, state: FSMContext):
    await message.answer("✨ **Активация бонуса**\n\nВведите ваш секретный промокод:", parse_mode="Markdown")
    await state.set_state(UserStates.waiting_for_promo_activation)

# 2. Ловим само сообщение с кодом
@dp.message(UserStates.waiting_for_promo_activation)
async def process_promo_activation(message: types.Message, state: FSMContext):
    code = message.text.strip()
    
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        # Ищем живой промокод
        async with db.execute("SELECT * FROM promos WHERE code = ? AND uses > 0", (code,)) as cursor:
            promo = await cursor.fetchone()
            
            if promo:
                r_type = promo['reward_type']
                amount = promo['reward_amount']
                
                # Начисляем награду
                if r_type == "stars":
                    await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, message.from_user.id))
                    label = "⭐ Звезд"
                else:
                    await db.execute("UPDATE users SET energy = energy + ? WHERE user_id = ?", (amount, message.from_user.id))
                    label = "⚡ Энергии"
                
                # Уменьшаем количество зарядов промокода
                await db.execute("UPDATE promos SET uses = uses - 1 WHERE code = ?", (code,))
                await db.commit()
                
                await message.answer(f"✅ **Успешно!**\nВы получили: +{amount} {label}", parse_mode="Markdown")
            else:
                await message.answer("❌ **Ошибка!**\nПромокод не существует, либо у него закончились активации.", parse_mode="Markdown")
    
    # Выходим из режима ожидания промокода
    await state.clear()

# 📢 Кнопка: Рассылка
@dp.callback_query(F.data == "admin_broadcast", F.from_user.id == ADMIN_ID)
async def start_broadcast(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("📝 Введите текст для рассылки всем пользователям:")
    await state.set_state(AdminStates.waiting_for_broadcast)
    await callback.answer()

# Обработка самого текста рассылки
@dp.message(AdminStates.waiting_for_broadcast, F.from_user.id == ADMIN_ID)
async def process_broadcast(message: types.Message, state: FSMContext):
    text = message.text
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM users") as cursor:
            users = await cursor.fetchall()
    
    await message.answer(f"⏳ Начинаю рассылку на {len(users)} чел...")
    count = 0
    for row in users:
        try:
            await bot.send_message(row[0], text)
            count += 1
            await asyncio.sleep(0.05)
        except: pass
    
    await message.answer(f"✅ Готово! Получили: {count}")
    await state.clear()

# 🎫 Кнопка: Создать промокод
@dp.callback_query(F.data == "admin_add_promo", F.from_user.id == ADMIN_ID)
async def start_promo(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "Формат ввода:\n`КОД ТИП СУММА КОЛВО`\n\n"
        "Пример: `GIFT2026 stars 100 50`", 
        parse_mode="Markdown"
    )
    await state.set_state(AdminStates.waiting_for_promo)
    await callback.answer()

@dp.message(AdminStates.waiting_for_promo, F.from_user.id == ADMIN_ID)
async def process_promo(message: types.Message, state: FSMContext):
    try:
        args = message.text.split()
        code, r_type, amount, uses = args[0], args[1], int(args[2]), int(args[3])
        
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("INSERT INTO promos VALUES (?, ?, ?, ?)", (code, r_type, amount, uses))
            await db.commit()
        await message.answer(f"✅ Промокод `{code}` успешно создан!")
    except:
        await message.answer("❌ Ошибка в формате. Попробуй еще раз.")
    await state.clear()

# 📊 Кнопка: Подробная статистика
@dp.callback_query(F.data == "admin_stats", F.from_user.id == ADMIN_ID)
async def admin_stats_call(callback: types.CallbackQuery):
    users, won = await get_global_stats()
    # Можно добавить больше данных
    await callback.message.answer(
        f"📈 **ДЕТАЛЬНАЯ СТАТИСТИКА**\n\n"
        f"👥 Всего юзеров: {users}\n"
        f"💰 Всего выплачено: {won} ⭐\n"
        f"📅 Сегодня 2026 год, бот работает стабильно.", 
        parse_mode="Markdown"
    )
    await callback.answer()
    
@dp.callback_query(F.data == "check_sub")
async def check_cb(callback: types.CallbackQuery):
    if await is_subscribed(callback.from_user.id):
        await callback.message.delete()
        await callback.message.answer("🎉 Подписка подтверждена!", reply_markup=main_menu_kb())
    else:
        await callback.answer("❌ Вы всё еще не подписаны!", show_alert=True)

async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
