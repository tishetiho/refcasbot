import os
import uuid
import asyncio
import random
import aiosqlite
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import BaseMiddleware
from aiogram.types import Message
from typing import Any, Awaitable, Callable, Dict
import time

# --- КОНФИГУРАЦИЯ ---
TOKEN = "8673476742:AAE4GeCi3x__yVgU3VKdtSYIvqfaTOaraJE"
OFFICIAL_CHANNEL_ID = -1003884251721
DISCUSSION_GROUP_ID = -1003446103260
CHANCE_TO_WIN = 0.05
ADMIN_ID = 5078764886
CHANNELS = [
    {"id": -1003884251721, "url": "https://t.me/ludomove", "name": "ЛУДО ДВИЖ"},
    {"id": -1003674572550, "url": "https://t.me/banknotagifts", "name": "Pepe | NFT"},
    ]
FISH_TYPES = {
    "boot": {"name": "Старый башмак 👞", "price": 0, "chance": 0.95},
    "common": {"name": "Плотва 🐟", "price": 0.5, "chance": 0.03},
    "rare": {"name": "Окунь 🐠", "price": 1, "chance": 0.015},
    "epic": {"name": "Щука 🐊", "price": 2.5, "chance": 0.004},
    "legendary": {"name": "Золотая рыбка 👑", "price": 5.0, "chance": 0.001}
}
KNB_TIMEOUT = 120  # 2 минуты на ход
KNB_COMMISSION = 0.05 # 5% комиссия
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "data", "bot_database.db")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- БАЗА ДАННЫХ (С проверкой структуры) ---
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS inventory (
                    user_id INTEGER, 
                    item_name TEXT, 
                    count INTEGER DEFAULT 0,
                    PRIMARY KEY (user_id, item_name))''')
        await db.execute('''CREATE TABLE IF NOT EXISTS knb_games 
                        (game_id TEXT PRIMARY KEY, creator_id INTEGER, joiner_id INTEGER, 
                        chat_id INTEGER, bet INTEGER, c_move TEXT, j_move TEXT, status TEXT)''')
    async with aiosqlite.connect(DB_NAME) as db:
        # Основные таблицы
        await db.execute('''CREATE TABLE IF NOT EXISTS tasks 
                          (task_id INTEGER PRIMARY KEY AUTOINCREMENT, 
                           title TEXT, url TEXT, channel_id TEXT, reward INTEGER)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS completed_tasks 
                          (user_id INTEGER, task_id INTEGER, PRIMARY KEY (user_id, task_id))''')
        await db.execute('''CREATE TABLE IF NOT EXISTS groups 
                          (chat_id INTEGER PRIMARY KEY, chat_name TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS post_bonuses 
                          (user_id INTEGER, post_id TEXT, PRIMARY KEY (user_id, post_id))''')
        await db.execute('''CREATE TABLE IF NOT EXISTS referrals 
                          (referrer_id INTEGER, referral_id INTEGER PRIMARY KEY)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS checks 
                          (check_id TEXT PRIMARY KEY, creator_id INTEGER, 
                           amount INTEGER, type TEXT, is_claimed INTEGER DEFAULT 0)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS settings 
                          (key TEXT PRIMARY KEY, value INTEGER)''')
        await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('bonus_enabled', 1)")
        
        # Таблица пользователей
        await db.execute('''CREATE TABLE IF NOT EXISTS users 
                          (user_id INTEGER PRIMARY KEY, 
                           balance INTEGER DEFAULT 0, 
                           energy INTEGER DEFAULT 3, 
                           referred_by INTEGER,
                           total_won INTEGER DEFAULT 0,
                           is_premium INTEGER DEFAULT 0,
                           last_bonus TEXT DEFAULT '2000-01-01 00:00:00')''')
        
        await db.execute('''CREATE TABLE IF NOT EXISTS sub_channels 
                          (channel_id INTEGER PRIMARY KEY, url TEXT, name TEXT)''')
        
        # Блок обновления структуры (строка 79 и далее)
        # УБЕДИСЬ, ЧТО ЭТИ TRY СТОЯТ ПРЯМО ПОД AWAIT ВЫШЕ
        try:
            await db.execute("ALTER TABLE users ADD COLUMN last_bonus TEXT DEFAULT '2000-01-01 00:00:00'")
        except:
            pass
            
        try:
            await db.execute("ALTER TABLE users ADD COLUMN total_won INTEGER DEFAULT 0")
        except:
            pass

        try:
            await db.execute("ALTER TABLE users ADD COLUMN is_premium INTEGER DEFAULT 0")
        except:
            pass
            
        await db.commit()

async def get_user_data(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row  # ЭТО ОЧЕНЬ ВАЖНО ДЛЯ РАБОТЫ РУЛЕТКИ
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone()
            
async def add_user(user_id, is_premium=False, referrer_id=None):
    premium_val = 1 if is_premium else 0
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id, referred_by, is_premium) VALUES (?, ?, ?)", 
                         (user_id, referrer_id, premium_val))
        await db.execute("UPDATE users SET is_premium = ? WHERE user_id = ?", (premium_val, user_id))
        await db.commit()

async def get_global_stats():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(user_id), SUM(total_won) FROM users") as cursor:
            res = await cursor.fetchone()
            return (res[0] or 0), (res[1] or 0)

# --- ПРОВЕРКА ПОДПИСКИ ---
async def is_subscribed_with_alert(message: types.Message, user_id: int):
    if not await is_subscribed(user_id):
        # Если это группа, даем ссылку на бота в личку
        if message.chat.type in ["group", "supergroup"]:
            await message.reply("❌ Чтобы играть, нужно подписаться на наши каналы!\nПерейди в бота: @luudorobot")
        else:
            # В личке просто выводим кнопки (как раньше)
            pass 
        return False
    return True
    
async def is_subscribed(user_id):
    # Если список в коде пуст — пропускаем всех
    if not CHANNELS:
        return True

    for channel in CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel["id"], user_id=user_id)
            if member.status not in ["member", "administrator", "creator"]:
                return False
        except Exception as e:
            print(f"Ошибка доступа к каналу {channel['id']}: {e}")
            # Если бот не админ, считаем что юзер подписан, чтобы не стопить бота
            continue 
    return True

@dp.callback_query(F.data == "check_sub")
async def check_cb(callback: types.CallbackQuery):
    if await is_subscribed(callback.from_user.id):
        await callback.message.delete()
        await callback.message.answer("🎉 Подписка подтверждена!", reply_markup=main_menu_kb())
    else:
        # Формируем сообщение с актуальными кнопками из БД
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT url, name FROM sub_channels") as cursor:
                channels = await cursor.fetchall()
        
        builder = InlineKeyboardBuilder()
        for url, name in channels:
            builder.row(types.InlineKeyboardButton(text=name, url=url))
        builder.row(types.InlineKeyboardButton(text="✅ Проверить подписки", callback_data="check_sub"))
        
        await callback.answer("❌ Вы всё еще не подписаны!", show_alert=True)

class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, slow_mode_delay: float = 0.7):
        # slow_mode_delay — задержка между сообщениями в секундах
        self.user_limits = {}
        self.delay = slow_mode_delay
        super().__init__()

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any]
    ) -> Any:
        user_id = event.from_user.id
        current_time = time.time()

        # Проверяем, когда пользователь писал последний раз
        if user_id in self.user_limits:
            last_time = self.user_limits[user_id]
            if current_time - last_time < self.delay:
                # Если пишет слишком быстро — игнорируем или шлем предупреждение
                if current_time - last_time > 0.2: # Чтобы не спамить в ответ на спам
                    return await event.answer("⚠️ Не спеши! Подожди немного.")
                return 

        # Обновляем время последнего сообщения
        self.user_limits[user_id] = current_time
        return await handler(event, data)
        
# --- МЕНЮ ---
def main_menu_kb():
    kb = [
        [types.KeyboardButton(text="🎰 ИГРАТЬ (Рулетка)"), types.KeyboardButton(text="🎣 Рыбалка")],
        [types.KeyboardButton(text="👤 Профиль"), types.KeyboardButton(text="🎁 Бонус")],
        [types.KeyboardButton(text="📊 Статистика"), types.KeyboardButton(text="👥 Рефералы")],
        [types.KeyboardButton(text="📜 Задания"), types.KeyboardButton(text="💎 Вывод")]
    ]
    return types.ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

async def admin_kb():
    is_enabled = 1
    
    # Теперь всё это находится ПРЯМО внутри async def
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT value FROM settings WHERE key = 'bonus_enabled'") as cursor:
                row = await cursor.fetchone()
                if row:
                    is_enabled = row[0]
    except Exception as e:
        print(f"Ошибка в admin_kb: {e}")

    builder = InlineKeyboardBuilder()
    status_text = "✅ Бонусы: ВКЛ" if is_enabled == 1 else "❌ Бонусы: ВЫКЛ"
    
    # Добавляем все твои кнопки
    builder.row(types.InlineKeyboardButton(text=status_text, callback_data="toggle_bonuses"))
    builder.row(types.InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"))
    builder.row(types.InlineKeyboardButton(text="📢 Сделать рассылку", callback_data="admin_broadcast"))
    builder.row(types.InlineKeyboardButton(text="🎫 Создать промокод", callback_data="admin_add_promo"))
    builder.row(types.InlineKeyboardButton(text="📢 Рассылка по чатам", callback_data="broadcast_chats"))
    builder.row(types.InlineKeyboardButton(text="🗑 Удалить задание", callback_data="admin_manage_tasks"))
    builder.row(types.InlineKeyboardButton(text="➕ Добавить задание", callback_data="admin_add_task"))
    builder.row(types.InlineKeyboardButton(text="➕ Добавить канал подписки", callback_data="admin_add_sub_channel"))
    builder.row(types.InlineKeyboardButton(text="🗑 Удалить канал подписки", callback_data="admin_list_sub_channels"))

    return builder.as_markup()
    
# --- ОБРАБОТЧИКИ ---
@dp.message(Command("start"))
async def start_cmd(message: types.Message, command: CommandObject):
    args = command.args
    user_id = message.from_user.id
    
    # 1. Реферальная система и бонусы за посты (оставляем, они работают хорошо)
    if args and args.isdigit():
        referrer_id = int(args)
        if referrer_id != user_id:
            async with aiosqlite.connect(DB_NAME) as db:
                async with db.execute("SELECT referrer_id FROM referrals WHERE referral_id = ?", (user_id,)) as cursor:
                    if not await cursor.fetchone():
                        await db.execute("INSERT INTO referrals (referrer_id, referral_id) VALUES (?, ?)", (referrer_id, user_id))
                        await db.execute("UPDATE users SET energy = energy + 5 WHERE user_id = ?", (referrer_id,))
                        await db.commit()
                        try: await bot.send_message(referrer_id, "🎉 +5 ⚡️ за нового реферала!")
                        except: pass

    await add_user(user_id)
    
    if args and args.startswith("post_bonus_"):
        post_id = args.split("_")[-1]
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT 1 FROM post_bonuses WHERE user_id = ? AND post_id = ?", (user_id, post_id)) as cursor:
                if not await cursor.fetchone():
                    await db.execute("INSERT INTO post_bonuses (user_id, post_id) VALUES (?, ?)", (user_id, post_id))
                    await db.execute("UPDATE users SET energy = energy + 3 WHERE user_id = ?", (user_id,))
                    await db.commit()
                    await message.answer(f"✅ +3 ⚡️ за пост №{post_id}")

    # 2. СТАРАЯ ДОБРАЯ ОП (через CHANNELS)
    if await is_subscribed(user_id):
        await message.answer("🧸 главное меню. Добро пожаловать, с чего начнем? 👇", reply_markup=main_menu_kb())
    else:
        builder = InlineKeyboardBuilder()
        for channel in CHANNELS:
            builder.row(types.InlineKeyboardButton(text=channel["name"], url=channel["url"]))
        
        builder.row(types.InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_sub"))
        
        await message.answer(
            "🚀 Чтобы пользоваться ботом, подпишись на наши каналы:",
            reply_markup=builder.as_markup(),
            parse_mode="Markdown"
        )
                
@dp.message(Command("admin"), F.from_user.id == ADMIN_ID)
async def admin_panel(message: types.Message):
    await message.answer("🛠 **ПАНЕЛЬ УПРАВЛЕНИЯ**\n\nВыбери действие:", reply_markup=await admin_kb(), parse_mode="Markdown")
    
@dp.message(F.text == "👤 Профиль")
async def profile_handler(message: types.Message):
    # --- ПРОВЕРКА ПОДПИСКИ ---
    if not await is_subscribed(message.from_user.id):
        return await message.answer(
            "❌ Доступ ограничен!\n\n"
            "Подробнее — /start",
            parse_mode="Markdown"
        )
    # -------------------------

    data = await get_user_data(message.from_user.id)
    if not data: 
        await add_user(message.from_user.id)
        data = await get_user_data(message.from_user.id)
    
    await message.answer(f"👤 Ваш профиль:\n\n"
                         f"💰 Баланс: {data['balance']} ⭐\n"
                         f"⚡ Энергия: {data['energy']}\n"
                         f"🏆 Выиграно за всё время: {data['total_won']} ⭐", parse_mode="Markdown")

@dp.message(F.text == "🎁 Бонус")
async def daily_bonus(message: types.Message):
    user_id = message.from_user.id
    
    # --- ПРОВЕРКА ПОДПИСКИ ---
    if not await is_subscribed(user_id): 
        return await message.answer(
            "❌ **Доступ ограничен!**\n\n"
            "Чтобы забирать ежедневный бонус, пожалуйста, подпишитесь на наши каналы из /start", 
            parse_mode="Markdown"
        )
    
    data = await get_user_data(user_id)
    
    # Проверка КД (24 часа)
    last_bonus_str = data['last_bonus']
    if not last_bonus_str:
        last_bonus_time = datetime.now() - timedelta(hours=25)
    else:
        last_bonus_time = datetime.strptime(last_bonus_str, '%Y-%m-%d %H:%M:%S')

    if datetime.now() - last_bonus_time < timedelta(hours=24):
        delta = (last_bonus_time + timedelta(hours=24)) - datetime.now()
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, _ = divmod(remainder, 60)
        return await message.answer(
            f"⏳ Бонус будет доступен через **{hours}ч. {minutes}м.**", 
            parse_mode="Markdown"
        )

    # --- ЛОГИКА ПРОВЕРКИ НИКА И ОПИСАНИЯ ---
    tag = "@luudorobot"
    bio_text = "Выбивай 777 и забирай мишку — @luudorobot"
    
    reward = 0
    # 1. Проверяем ник (имя + фамилия)
    user_full_name = message.from_user.full_name
    has_tag_in_name = tag in user_full_name

    # 2. Проверяем описание (BIO)
    # В message.from_user описания нет, нужно запрашивать полный объект чата
    try:
        full_user_info = await bot.get_chat(user_id)
        user_bio = full_user_info.bio if full_user_info.bio else ""
    except:
        user_bio = ""
    
    has_bio = bio_text in user_bio

    # Определяем размер награды
    if has_tag_in_name and has_bio:
        reward = 2
        result_text = f"✅ Вы получили максимальный бонус: **+{reward} ⚡ Энергии**!"
    elif has_tag_in_name:
        reward = 1
        result_text = f"✅ Вы получили бонус за тег в нике: **+{reward} ⚡ Энергия**!"
    else:
        # Если ничего не соблюдено - выдаем инструкцию и НЕ обновляем время бонуса
        return await message.answer(
            "🌹 **Как получить бонус:**\n\n"
            f"1. Добавь `{tag}` в своё имя в Telegram — получишь **+1 ⚡**\n"
            f"2. Добавь в описание (BIO) фразу:\n`{bio_text}` — получишь еще **+1 ⚡**\n\n"
            "Как выполнишь условия, жми кнопку снова!",
            parse_mode="Markdown"
        )

    # Обновляем базу данных только если условия выполнены
    new_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE users SET energy = energy + ?, last_bonus = ? WHERE user_id = ?", 
            (reward, new_time, user_id)
        )
        await db.commit()
        
    await message.answer(result_text, parse_mode="Markdown")
            
@dp.message(F.text == "💎 Вывод", F.chat.type == "private") # Добавили проверку на личку
async def withdraw_handler(message: types.Message):
    user_id = message.from_user.id
    
    # ПРОВЕРКА ПОДПИСКИ
    if not await is_subscribed(user_id):
        return await message.answer(
            "❌ **Доступ к выводу ограничен!**\n\n"
            "Чтобы выводить заработанные ⭐, пожалуйста, подпишитесь на наши каналы из /start",
            parse_mode="Markdown"
        )

    data = await get_user_data(user_id)
    balance = data['balance']
    
    if balance >= 30:
        await message.answer(
            f"💎 На вашем балансе **{balance} ⭐**\n\n"
            "Для вывода напишите сумму вывода и ожидайте поступления средств на ваш баланс Telegram Stars.",
            parse_mode="Markdown"
        )
    else:
        await message.answer(
            f"❌ Недостаточно средств.\n"
            f"Минимум для вывода: **30 ⭐**\n"
            f"Ваш баланс: **{balance} ⭐**", 
            parse_mode="Markdown"
        )

@dp.message(F.text.in_(["🎰 ИГРАТЬ (Рулетка)", "/play", "/slot"]))
async def play_game(message: types.Message):
    user_id = message.from_user.id
    chat_type = message.chat.type # Определяем, где идет игра

    # 1. Проверка подписки
    if not await is_subscribed_with_alert(message, user_id):
        return
    if not await is_subscribed(user_id): 
        return await message.answer("❌ Сначала подпишись на каналы!")

    # 2. Получение данных (с подстраховкой)
    data = await get_user_data(user_id)
    if not data: 
        await add_user(user_id)
        data = await get_user_data(user_id)

    # 3. Проверка энергии
    # Используем get(), чтобы бот не падал, если колонка вдруг не прочиталась
    energy = data['energy'] if data else 0
    
    if energy <= 0: 
        return await message.answer("🪫 Нет энергии! Приглашай друзей или забирай ежедневный бонус.")

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
    await asyncio.sleep(1.5) # Ждем завершения анимации
    
    win = 0
    
    # 7. Логика результата
    # Значения кубика 1, 22, 43, 64 — это три семерки (джекпот) в анимации Telegram
    if msg.dice.value in [1, 22, 43, 64]:
        win = random.randint(1, 5) 
        quote = random.choice(win_quotes)
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (win, user_id))
            await db.commit()
        
        # Ответ при победе
        await asyncio.sleep(1.5) # Ждем, пока анимация докрутится
        await message.reply(f"🏆 Юзер @{message.from_user.username} выиграл {win} ⭐!", parse_mode="HTML")
    
    else:
        # Ответ при проигрыше
        await asyncio.sleep(1.5)
        # Здесь мы НЕ используем переменную win, чтобы не было путаницы
        await message.reply(f"❌ Юзер @{message.from_user.username} ничего не выиграл. Попробуй еще раз!")

@dp.message(F.text == "🎣 Рыбалка", F.chat.type == "private")
async def start_fishing(message: types.Message):
    user_id = message.from_user.id
    
    # Проверка подписки
    if not await is_subscribed(user_id):
        return await message.answer("❌ Сначала подпишитесь на каналы!")

    data = await get_user_data(user_id)
    if data['energy'] < 1:
        return await message.answer("🪫 Недостаточно энергии! Нужно минимум **1 ⚡**", parse_mode="Markdown")

    # Списываем 1 энергию сразу
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET energy = energy - 1 WHERE user_id = ?", (user_id,))
        await db.commit()

    # Красивая анимация заброса
    msg = await message.answer("🎣 Закидываем удочку... Ждём поклёвки...")
    await asyncio.sleep(1.5)
    await msg.edit_text("🌊 ⏳ . . .")
    await asyncio.sleep(1.5)
    await msg.edit_text("🌊 ⏳ 🐟 . .")
    await asyncio.sleep(1.5)
    await msg.edit_text("🎣 **ТЯНИ! ЧТО-ТО ЕСТЬ!**", parse_mode="Markdown")
    await asyncio.sleep(1)

    # Логика шансов (как в рулетке)
    rand = random.random()
    cumulative = 0
    caught_item = FISH_TYPES["boot"] # По умолчанию мусор

    for fish_id, info in FISH_TYPES.items():
        cumulative += info["chance"]
        if rand <= cumulative:
            caught_item = info
            break

    # Сохраняем улов в инвентарь (или сразу продаем, если хочешь упростить)
    async with aiosqlite.connect(DB_NAME) as db:
        # Для простоты в этом примере: сразу зачисляем ⭐ на баланс
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", 
                         (caught_item["price"], user_id))
        await db.commit()

    await msg.edit_text(
        f"🎉 **Улов!**\n\n"
        f"Вы поймали: {caught_item['name']}\n"
        f"Награда: **+{caught_item['price']} ⭐**\n\n"
        f"Баланс обновлен!", 
        parse_mode="Markdown"
    )
    
@dp.message(Command("top"))
async def chat_top(message: types.Message):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id, balance FROM users ORDER BY balance DESC LIMIT 5") as cursor:
            rows = await cursor.fetchall()
    
    text = "🏆 **ТОП БОГАЧЕЙ ЛУДОБОТА:**\n\n"
    for i, row in enumerate(rows, 1):
        text += f"{i}. ID {row[0]} — {row[1]} ⭐\n"
    
    await message.answer(text, parse_mode="Markdown")

# Словарик для хранения активных вызовов (кто кого вызвал)
active_duels = {}

@dp.message(Command("duel"), F.chat.type.in_(["group", "supergroup"]))
async def start_duel(message: types.Message, command: CommandObject):
    user_id = message.from_user.id
    
    # Проверка ставки (например: /duel 50)
    if not command.args or not command.args.isdigit():
        return await message.reply("⚠️ Напиши ставку: `/duel 50`", parse_mode="Markdown")
    
    bet = int(command.args)
    
    # Проверка баланса игрока в БД
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()
            if not user or user['balance'] < bet:
                return await message.reply(f"❌ Недостаточно ⭐ для такой ставки! Твой баланс: {user['balance'] if user else 0}")

    # Запоминаем дуэль
    active_duels[message.chat.id] = {
        "challenger": user_id,
        "bet": bet,
        "message_id": message.message_id
    }
    
    await message.answer(
        f"⚔️ ВЫЗОВ НА ДУЭЛЬ!\n\n"
        f"👤 Игрок: {message.from_user.mention_html()}\n"
        f"💰 Ставка: {bet} ⭐\n\n"
        f"Чтобы принять вызов, ответь на это сообщение командой /accept",
        parse_mode="HTML"
    )

@dp.message(Command("accept"), F.chat.type.in_(["group", "supergroup"]))
async def accept_duel(message: types.Message):
    chat_id = message.chat.id
    acceptor_id = message.from_user.id
    
    if chat_id not in active_duels:
        return await message.reply("❌ Сейчас нет активных вызовов в этом чате.")
    
    duel_data = active_duels[chat_id]
    challenger_id = duel_data['challenger']
    bet = duel_data['bet']
    
    if acceptor_id == challenger_id:
        return await message.reply("🤔 Нельзя играть против самого себя.")

    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        # Проверяем баланс того, кто принимает
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (acceptor_id,)) as cursor:
            user = await cursor.fetchone()
            if not user or user['balance'] < bet:
                return await message.reply(f"❌ У тебя не хватает ⭐ для принятия вызова!")

        # Бросаем кости
        await message.answer(f"🎲 Бросаем кости для {message.from_user.first_name} и игрока выше...")
        
        d1 = await bot.send_dice(chat_id, emoji="🎲")
        val1 = d1.dice.value # Результат зачинщика
        
        await asyncio.sleep(2.5) # Пауза для драматизма
        
        d2 = await bot.send_dice(chat_id, emoji="🎲")
        val2 = d2.dice.value # Результат принявшего
        
        await asyncio.sleep(2.5)

        if val1 == val2:
            await message.answer("🤝 Ничья! Очки равны, звезды остаются при своих.")
        else:
            winner_id = challenger_id if val1 > val2 else acceptor_id
            loser_id = acceptor_id if val1 > val2 else challenger_id
            
            # Твоя комиссия 10% (опционально)
            prize = int(bet * 1) 
            
            # Обновляем балансы
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (prize, winner_id))
            await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (bet, loser_id))
            await db.commit()

            winner_name = "Первый игрок" if val1 > val2 else message.from_user.first_name
            await message.answer(
                f"🎉 Победил {winner_name}!\n"
                f"📈 Результат: {val1} vs {val2}\n"
                f"💰 Выигрыш: {prize} ⭐ (с учетом комиссии)",
                parse_mode="Markdown"
            )
    
    # Удаляем дуэль из активных
    del active_duels[chat_id]

@dp.message(F.chat.id == DISCUSSION_GROUP_ID)
async def bonus_in_discussion(message: types.Message):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT value FROM settings WHERE key = 'bonus_enabled'") as cursor:
            row = await cursor.fetchone()
            if row and row[0] == 0:
                return # Бонусы выключены, просто выходим
    # ПРОВЕРКА 1: Это сообщение от канала?
    # ПРОВЕРКА 2: В сообщении НЕТ команд (не начинается с /)
    if message.sender_chat and message.sender_chat.id == OFFICIAL_CHANNEL_ID:
        if not message.text or not message.text.startswith("/"):
            
            builder = InlineKeyboardBuilder()
            builder.row(types.InlineKeyboardButton(
                text="🎁 получить бонус", 
                url=f"https://t.me/{(await bot.get_me()).username}?start=post_bonus_{message.forward_from_message_id}"
            ))

            await message.reply(
                "‼️ успей забрать бонус пока не разобрали!\n жми👇:",
                reply_markup=builder.as_markup(),
                parse_mode="Markdown"
            )
            return # Выходим, чтобы не мешать другим проверкам
    
    # Если это не пост из канала, бот просто проигнорирует и 
    # aiogram пойдет искать другие хендлеры (например, игры)
    
@dp.message(F.text == "📊 Статистика")
async def stats_handler(message: types.Message):
    user_id = message.from_user.id
    
    # --- ПРОВЕРКА ПОДПИСКИ ---
    if not await is_subscribed(user_id):
        return await message.answer(
            "❌ Доступ ограничен!\n\n"
            "Подробнее — /start",
            parse_mode="Markdown"
        )
    # -------------------------

    users, won = await get_global_stats()
    await message.answer(
        f"📊 СТАТИСТИКА ЛУДОБОТА\n\n"
        f"👥 Всего игроков: {users}\n"
        f"💰 Выиграно за всё время: {won} ⭐\n\n"
        f"✅ Выплаты работают в штатном режиме!", 
        parse_mode="Markdown"
    )

@dp.message(F.text == "👥 Рефералы")
async def ref_handler(message: types.Message):
    user_id = message.from_user.id
    
    # --- ПРОВЕРКА ПОДПИСКИ ---
    if not await is_subscribed(user_id):
        return await message.answer(
            "❌ Доступ ограничен!\n\n"
            "Подробнее — /start",
            parse_mode="Markdown"
        )
    # -------------------------

    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start={user_id}"
    
    await message.answer(
        f"👥 РЕФЕРАЛЬНАЯ ПРОГРАММА\n\n"
        f"Приглашай друзей и получай +5 ⚡ Энергии за каждого приглашенного!\n\n"
        f"🔗 **Твоя ссылка для приглашения:**\n"
        f"`{link}`", 
        parse_mode="Markdown"
    )

# Состояния для админки
class AdminStates(StatesGroup):
    waiting_for_broadcast_text = State()
    waiting_for_broadcast = State()
    waiting_for_promo = State()
    waiting_for_task_title = State()
    waiting_for_task_url = State()
    waiting_for_task_reward = State()
    waiting_for_task_channel_id = State()
    waiting_for_sub_channel_id = State()
    waiting_for_sub_channel_url = State()
    waiting_for_sub_channel_name = State()

class UserStates(StatesGroup):
    waiting_for_promo_activation = State()

@dp.callback_query(F.data == "admin_add_task", F.from_user.id == ADMIN_ID)
async def add_task_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите название задания (например: Подпишись на канал спонсора):")
    await state.set_state(AdminStates.waiting_for_task_title)
    await callback.answer()

@dp.message(AdminStates.waiting_for_task_title)
async def add_task_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text)
    await message.answer("Отправьте ссылку на канал (https://t.me/...):")
    await state.set_state(AdminStates.waiting_for_task_url)

@dp.message(AdminStates.waiting_for_task_url)
async def add_task_url(message: types.Message, state: FSMContext):
    await state.update_data(url=message.text)
    await message.answer("Введите ID канала (например: -1001234567). Бот должен быть там админом!")
    await state.set_state(AdminStates.waiting_for_task_channel_id)

@dp.message(AdminStates.waiting_for_task_channel_id)
async def add_task_channel(message: types.Message, state: FSMContext):
    await state.update_data(channel_id=message.text)
    await message.answer("Введите награду (количество энергии):")
    await state.set_state(AdminStates.waiting_for_task_reward)

@dp.message(AdminStates.waiting_for_task_reward)
async def add_task_final(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("Введите число!")
    
    data = await state.get_data()
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT INTO tasks (title, url, channel_id, reward) VALUES (?, ?, ?, ?)",
                         (data['title'], data['url'], data['channel_id'], int(message.text)))
        await db.commit()
    
    await message.answer("✅ Задание успешно добавлено!")
    await state.clear()

@dp.callback_query(F.data == "admin_manage_tasks", F.from_user.id == ADMIN_ID)
async def admin_manage_tasks(callback: types.CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT task_id, title, reward FROM tasks") as cursor:
            tasks = await cursor.fetchall()

    if not tasks:
        return await callback.answer("📭 Список заданий пуст!", show_alert=True)

    builder = InlineKeyboardBuilder()
    for task_id, title, reward in tasks:
        builder.row(types.InlineKeyboardButton(
            text=f"❌ {title} ({reward}⚡️)", 
            callback_data=f"admin_delete_task_{task_id}"
        ))
    
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")) # Кнопка возврата в меню
    
    await callback.message.edit_text("Выберите задание, которое хотите удалить:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("admin_delete_task_"), F.from_user.id == ADMIN_ID)
async def admin_confirm_delete_task(callback: types.CallbackQuery):
    task_id = int(callback.data.split("_")[-1])
    
    async with aiosqlite.connect(DB_NAME) as db:
        # Удаляем само задание
        await db.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
        # Удаляем историю выполнений, чтобы не засорять базу
        await db.execute("DELETE FROM completed_tasks WHERE task_id = ?", (task_id,))
        await db.commit()
    
    await callback.answer("✅ Задание успешно удалено!")
    # Обновляем список после удаления
    await admin_manage_tasks(callback)

@dp.message(F.text == "📜 Задания")
async def show_tasks(message: types.Message):
    user_id = message.from_user.id
    
    # --- ПРОВЕРКА ПОДПИСКИ ---
    if not await is_subscribed(user_id):
        return await message.answer(
            "❌ Доступ ограничен!\n\n"
            "Подробнее — /start",
            parse_mode="Markdown"
        )
    # -------------------------

    builder = InlineKeyboardBuilder()
    
    # Дальше идет твой старый код формирования списка заданий...
    # Например:
    # builder.row(types.InlineKeyboardButton(text="Сделать репост", callback_data="task_repost"))
    
    await message.answer("📜 ДОСТУПНЫЕ ЗАДАНИЯ\n\nВыполняйте задания и получайте ⚡ Энергию и ⭐ Звезды!", 
                         reply_markup=builder.as_markup(),
                         parse_mode="Markdown")
    
    async with aiosqlite.connect(DB_NAME) as db:
        # Показываем только те задания, которые юзер еще не выполнил
        async with db.execute('''SELECT task_id, title, reward FROM tasks 
                                 WHERE task_id NOT IN (SELECT task_id FROM completed_tasks WHERE user_id = ?)''', 
                              (user_id,)) as cursor:
            tasks = await cursor.fetchall()

    if not tasks:
        return await message.answer("🎉 Ты выполнил все доступные задания! Приходи позже.")

    for task_id, title, reward in tasks:
        builder.row(types.InlineKeyboardButton(
            text=f"{title} (+{reward}⚡️)", 
            callback_data=f"view_task_{task_id}"
        ))
    
    await message.answer("Выбирай задание для выполнения:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("view_task_"))
async def view_task(callback: types.CallbackQuery):
    task_id = int(callback.data.split("_")[-1])
    
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT title, url, reward FROM tasks WHERE task_id = ?", (task_id,)) as cursor:
            task = await cursor.fetchone()
    
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="🔗 Перейти в канал", url=task[1]))
    builder.row(types.InlineKeyboardButton(text="✅ Проверить подписку", callback_data=f"check_task_{task_id}"))
    
    await callback.message.edit_text(f"📝 Задание: {task[0]}\n💰 Награда: {task[2]}⚡️", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("check_task_"))
async def check_task(callback: types.CallbackQuery):
    task_id = int(callback.data.split("_")[-1])
    user_id = callback.from_user.id
    
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT channel_id, reward FROM tasks WHERE task_id = ?", (task_id,)) as cursor:
            task = await cursor.fetchone()
            
    # Проверяем подписку через метод get_chat_member
    try:
        member = await bot.get_chat_member(chat_id=task[0], user_id=user_id)
        if member.status in ["member", "administrator", "creator"]:
            async with aiosqlite.connect(DB_NAME) as db:
                # Начисляем награду и помечаем как выполнено
                await db.execute("INSERT INTO completed_tasks (user_id, task_id) VALUES (?, ?)", (user_id, task_id))
                await db.execute("UPDATE users SET energy = energy + ? WHERE user_id = ?", (task[1], user_id))
                await db.commit()
            
            await callback.message.edit_text(f"✅ Задание выполнено! Начислено {task[1]}⚡️")
            await callback.answer("Успешно!")
        else:
            await callback.answer("❌ Ты не подписался на канал!", show_alert=True)
    except Exception as e:
        await callback.answer("⚠️ Ошибка: бот не является администратором в этом канале.", show_alert=True)

    # --- УПРАВЛЕНИЕ КАНАЛАМИ ПОДПИСКИ ---
@dp.callback_query(F.data == "admin_add_sub_channel", F.from_user.id == ADMIN_ID)
async def add_sub_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите ID канала (например: -100123456789):")
    await state.set_state(AdminStates.waiting_for_sub_channel_id)
    await callback.answer()

@dp.message(AdminStates.waiting_for_sub_channel_id)
async def add_sub_id(message: types.Message, state: FSMContext):
    # Убираем лишние пробелы и проверяем, что это число (ID канала всегда числовое)
    raw_id = message.text.strip()
    try:
        ch_id = int(raw_id)
        await state.update_data(ch_id=ch_id)
        await message.answer("Отправьте ссылку на канал (https://t.me/...):")
        await state.set_state(AdminStates.waiting_for_sub_channel_url)
    except ValueError:
        await message.answer("❌ Ошибка: ID канала должен быть числом (например, -100123456789).")
        
@dp.message(AdminStates.waiting_for_sub_channel_url)
async def add_sub_url(message: types.Message, state: FSMContext):
    await state.update_data(url=message.text)
    await message.answer("Введите название для кнопки (например: Наш спонсор):")
    await state.set_state(AdminStates.waiting_for_sub_channel_name)

@dp.message(AdminStates.waiting_for_sub_channel_name)
async def add_sub_final(message: types.Message, state: FSMContext):
    data = await state.get_data()
    try:
        async with aiosqlite.connect(DB_NAME) as db:
            # Используем INSERT OR REPLACE, чтобы обновить данные, если ID совпадает
            await db.execute(
                "INSERT OR REPLACE INTO sub_channels (channel_id, url, name) VALUES (?, ?, ?)",
                (data['ch_id'], data['url'], message.text.strip())
            )
            await db.commit()
        await message.answer(f"✅ Канал «{message.text}» успешно добавлен/обновлен!")
    except Exception as e:
        await message.answer(f"❌ Ошибка БД: {e}")
    await state.clear()

@dp.callback_query(F.data == "admin_list_sub_channels", F.from_user.id == ADMIN_ID)
async def list_sub_channels(callback: types.CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT channel_id, url, name FROM sub_channels") as cursor:
            rows = await cursor.fetchall()

    builder = InlineKeyboardBuilder()
    text = "📢 Список каналов для подписки:\n\n"
    
    if not rows:
        text += "Список пуст."
    else:
        for row in rows:
            ch_id, url, name = row
            text += f"🔹 {name} ({ch_id})\n🔗 {url}\n\n"
            builder.row(types.InlineKeyboardButton(
                text=f"❌ Удалить {name}", 
                callback_data=f"del_sub_{ch_id}")
            )

    builder.row(types.InlineKeyboardButton(text="➕ Добавить канал", callback_data="add_sub_channel"))
    builder.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_main"))

    # Используем edit_text, чтобы обновить текущее меню
    try:
        await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")
    except:
        await callback.message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        
@dp.callback_query(F.data.startswith("del_sub_"), F.from_user.id == ADMIN_ID)
async def delete_sub_channel(callback: types.CallbackQuery):
    # Безопасное извлечение ID (все, что после 'del_sub_')
    ch_id = int(callback.data.replace("del_sub_", ""))
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM sub_channels WHERE channel_id = ?", (ch_id,))
        await db.commit()
    await callback.answer("✅ Канал удален из списка ОП")
    await list_sub_channels(callback)
    
@dp.message(AdminStates.waiting_for_broadcast_text, F.from_user.id == ADMIN_ID)
async def process_broadcast(message: types.Message, state: FSMContext):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT chat_id FROM groups") as cursor:
            chats = await cursor.fetchall()

    if not chats:
        await message.answer("❌ В базе пока нет ни одного чата.")
        await state.clear()
        return

    count = 0
    errors = 0
    
    msg = await message.answer(f"🚀 Начинаю рассылку по {len(chats)} чатам...")

    for (chat_id,) in chats:
        try:
            # Копируем сообщение (текст, фото, видео — не важно)
            await message.copy_to(chat_id=chat_id)
            count += 1
            # Небольшая пауза, чтобы Telegram не забанил за спам
            await asyncio.sleep(0.05) 
        except Exception as e:
            errors += 1
            print(f"Ошибка отправки в {chat_id}: {e}")

    await msg.edit_text(f"✅ Рассылка завершена!\n\n📈 Успешно: {count}\n⚠️ Ошибок: {errors}")
    await state.clear()
    
# 1. Сначала ловим нажатие кнопки
@dp.message(F.text == "🎫 Промокод")
async def promo_start_activation(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    # --- ПРОВЕРКА ПОДПИСКИ ---
    if not await is_subscribed(user_id):
        return await message.answer(
            "❌ Доступ ограничен!\n\n"
            "Подробнее — /start",
            parse_mode="Markdown"
        )
    # -------------------------

    await message.answer(
        "✨ Активация бонуса\n\n"
        "Введите ваш секретный промокод:", 
        parse_mode="Markdown"
    )
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

@dp.callback_query(F.data == "broadcast_chats", F.from_user.id == ADMIN_ID)
async def start_broadcast(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите текст рассылки (можно с фото):")
    await state.set_state(AdminStates.waiting_for_broadcast_text)
    await callback.answer()

@dp.callback_query(F.data.startswith("claim_"))
async def claim_check(callback: types.CallbackQuery):
    check_id = callback.data.split("_")[1]
    user_id = callback.from_user.id

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT creator_id, amount, type, is_claimed FROM checks WHERE check_id = ?", (check_id,)) as cursor:
            check = await cursor.fetchone()

        if not check:
            return await callback.answer("Чек не найден!", show_alert=True)
        
        creator_id, amount, ctype, is_claimed = check

        if is_claimed:
            return await callback.answer("Этот чек уже кто-то забрал! 😔", show_alert=True)
        
        if user_id == creator_id:
            return await callback.answer("Вы не можете забрать свой собственный чек!", show_alert=True)

        # 1. Помечаем как использованный
        await db.execute("UPDATE checks SET is_claimed = 1 WHERE check_id = ?", (check_id,))
        # 2. Начисляем валюту
        column = "energy" if ctype == "energy" else "stars"
        await db.execute(f"UPDATE users SET {column} = {column} + ? WHERE user_id = ?", (amount, user_id))
        await db.commit()

    await callback.message.edit_text(f"✅ Чек на {amount} {ctype} активирован юзером {callback.from_user.first_name}!")
    await callback.answer("Поздравляем! Валюта начислена.")

@dp.callback_query(F.data.startswith("cancel_check_"))
async def cancel_check(callback: types.CallbackQuery):
    check_id = callback.data.split("_")[2]
    user_id = callback.from_user.id

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT creator_id, amount, type, is_claimed FROM checks WHERE check_id = ?", (check_id,)) as cursor:
            check = await cursor.fetchone()

        if not check or check[0] != user_id or check[3] == 1:
            return await callback.answer("Невозможно отменить этот чек!", show_alert=True)

        # Возвращаем валюту
        column = "energy" if check[2] == "energy" else "stars"
        await db.execute(f"UPDATE users SET {column} = {column} + ? WHERE user_id = ?", (check[1], user_id))
        await db.execute("DELETE FROM checks WHERE check_id = ?", (check_id,))
        await db.commit()

    await callback.message.edit_text("🚫 Чек аннулирован, средства вернулись владельцу.")
    await callback.answer("Деньги возвращены!")
    
# 📢 Кнопка: Рассылка
@dp.callback_query(F.data == "admin_broadcast", F.from_user.id == ADMIN_ID)
async def start_broadcast(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("📝 Введите текст для рассылки всем пользователям:")
    await state.set_state(AdminStates.waiting_for_broadcast)
    await callback.answer()

# Обработка самого текста рассылки
@dp.message(AdminStates.waiting_for_broadcast, F.from_user.id == ADMIN_ID)
async def process_broadcast_to_users(message: types.Message, state: FSMContext):
    # Берем список всех ID пользователей
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM users") as cursor:
            users = await cursor.fetchall()
    
    if not users:
        await message.answer("❌ Пользователей в базе нет.")
        await state.clear()
        return

    status_msg = await message.answer(f"⏳ Начинаю рассылку пользователям ({len(users)} чел.)...")
    
    count = 0
    blocked = 0
    
    for (user_id,) in users:
        try:
            # Копируем любое сообщение (текст, фото, видео)
            await message.copy_to(chat_id=user_id)
            count += 1
            await asyncio.sleep(0.05) # Пауза, чтобы не словить бан от ТГ
        except Exception:
            blocked += 1
    
    await status_msg.edit_text(
        f"✅ **Рассылка по пользователям завершена!**\n\n"
        f"👤 Получили: {count}\n"
        f"🚫 Заблокировали бота: {blocked}"
    )
    await state.clear() # Сбрасываем состояние, чтобы бот снова слушала команды

@dp.callback_query(F.data == "toggle_bonuses", F.from_user.id == ADMIN_ID)
async def toggle_bonuses_callback(callback: types.CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        # Получаем текущее состояние
        async with db.execute("SELECT value FROM settings WHERE key = 'bonus_enabled'") as cursor:
            row = await cursor.fetchone()
            current_status = row[0] if row else 1
        
        # Меняем на противоположное
        new_status = 0 if current_status == 1 else 1
        await db.execute("UPDATE settings SET value = ? WHERE key = 'bonus_enabled'", (new_status,))
        await db.commit()

    # Обновляем сообщение админа с новой кнопкой
    await callback.message.edit_reply_markup(reply_markup=await get_admin_kb())
    await callback.answer(f"Статус бонусов изменен на {'ВКЛ' if new_status else 'ВЫКЛ'}")

@dp.my_chat_member()
async def on_my_chat_member(update: types.ChatMemberUpdated):
    # Если бота добавили в чат как участника или администратора
    if update.new_chat_member.status in ["member", "administrator"]:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("INSERT OR IGNORE INTO groups (chat_id, chat_name) VALUES (?, ?)", 
                             (update.chat.id, update.chat.title))
            await db.commit()
            
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

@dp.message(Command("knb"), F.chat.type.in_(["group", "supergroup"]))
async def create_knb_duel(message: types.Message, command: CommandObject):
    user_id = message.from_user.id
    
    # 1. Проверка: запущен ли бот (есть ли в БД) и подписка
    data = await get_user_data(user_id)
    if not data:
        return await message.reply("❌ Ты не зарегистрирован в боте! Напиши мне в ЛС /start")
    
    if not await is_subscribed(user_id):
        return await message.reply("❌ Подпишись на каналы в боте, чтобы играть!")

    if not command.args or not command.args.isdigit():
        return await message.reply("⚠️ Напиши ставку энергии: `/knb 2`")
    
    bet = int(command.args)
    if data['energy'] < bet or bet < 1:
        return await message.reply(f"❌ Недостаточно ⚡️. Твой баланс: {data['energy']}")

    game_id = f"knb_{user_id}_{int(time.time())}"
    
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(text="Принять вызов ⚔️", callback_data=f"knb_join_{game_id}_{bet}"))
    
    await message.answer(
        f"✊✌️✋ **ВЫЗОВ КНБ!**\n\n"
        f"👤 Игрок: {message.from_user.mention_html()}\n"
        f"⚡️ Ставка: **{bet} энергии**\n"
        f"⚖️ Комиссия: {int(KNB_COMMISSION*100)}%\n\n"
        f"Жми кнопку, чтобы принять бой!",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("knb_join_"))
async def accept_knb_duel(callback: types.CallbackQuery):
    data_parts = callback.data.split("_")
    game_id, bet = "_".join(data_parts[2:5]), int(data_parts[5])
    creator_id = int(data_parts[3])
    joiner_id = callback.from_user.id

    if joiner_id == creator_id:
        return await callback.answer("Нельзя играть с самим собой!", show_alert=True)

    # 2. Проверка второго игрока (Бот запущен + Подписка + Энергия)
    j_data = await get_user_data(joiner_id)
    if not j_data:
        return await callback.answer("❌ Сначала запусти бота в ЛС!", show_alert=True)
    if not await is_subscribed(joiner_id):
        return await callback.answer("❌ Подпишись на каналы в боте!", show_alert=True)
    if j_data['energy'] < bet:
        return await callback.answer("❌ У тебя не хватает энергии!", show_alert=True)

    # Проверка первого игрока (не слил ли он энергию, пока ждал)
    c_data = await get_user_data(creator_id)
    if c_data['energy'] < bet:
        return await callback.answer("У создателя уже нет столько энергии!", show_alert=True)

    # Создаем запись игры и отправляем выбор в ЛС
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO knb_games (game_id, creator_id, joiner_id, bet, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (game_id, creator_id, joiner_id, bet, "WAITING", datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        )
        await db.commit()

    # Кнопки для выбора в ЛС
    kb = InlineKeyboardBuilder()
    for move, icon in [("r", "🪨 Камень"), ("s", "✂️ Ножницы"), ("p", "📄 Бумага")]:
        kb.row(types.InlineKeyboardButton(text=icon, callback_data=f"knb_move_{game_id}_{move}"))

    try:
        await bot.send_message(creator_id, f"🎮 Игра началась! Сделай свой выбор для дуэли на {bet} ⚡️:", reply_markup=kb.as_markup())
        await bot.send_message(joiner_id, f"🎮 Ты принял вызов! Сделай свой выбор для дуэли на {bet} ⚡️:", reply_markup=kb.as_markup())
        await callback.message.edit_text(f"🤝 Дуэль принята! Игроки делают выбор в ЛС бота...")
        
        # Запускаем таймер на 2 минуты
        asyncio.create_task(knb_timeout_check(game_id, callback.message))
    except Exception:
        await callback.answer("❌ Не удалось отправить сообщение в ЛС. Убедитесь, что бот не заблокирован!", show_alert=True)

@dp.callback_query(F.data.startswith("knb_move_"))
async def process_knb_move(callback: types.CallbackQuery):
    data = callback.data.split("_")
    game_id, move = "_".join(data[2:5]), data[5]
    user_id = callback.from_user.id

    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM knb_games WHERE game_id = ?", (game_id,)) as cursor:
            game = await cursor.fetchone()
        
        if not game or game['status'] != "WAITING":
            return await callback.message.edit_text("⌛️ Время вышло или игра завершена.")

        # Записываем ход
        col = "c_move" if user_id == game['creator_id'] else "j_move"
        await db.execute(f"UPDATE knb_games SET {col} = ? WHERE game_id = ?", (move, game_id))
        await db.commit()

        # Проверяем, сделали ли оба ход
        async with db.execute("SELECT c_move, j_move, creator_id, joiner_id, bet FROM knb_games WHERE game_id = ?", (game_id,)) as cursor:
            updated_game = await cursor.fetchone()
            
        if updated_game['c_move'] and updated_game['j_move']:
            await finish_knb_game(updated_game, game_id)
            await callback.message.edit_text("✅ Ход принят! Результаты в группе.")
        else:
            await callback.message.edit_text("⏳ Ход принят! Ожидаем соперника...")

async def finish_knb_game(game, game_id):
    m1, m2 = game['c_move'], game['j_move']
    c_id, j_id = game['creator_id'], game['joiner_id']
    bet = game['bet']
    
    # Логика победителя
    if m1 == m2:
        res = "draw"
    else:
        win_map = {'r': 's', 's': 'p', 'p': 'r'}
        res = "win1" if win_map[m1] == m2 else "win2"

    names = {'r': '🪨 Камень', 's': '✂️ Ножницы', 'p': '📄 Бумага'}
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE knb_games SET status = 'FINISHED' WHERE game_id = ?", (game_id,))
        
        if res == "draw":
            result_text = f"🤝 **Ничья!** Оба выбрали {names[m1]}. Энергия сохранена."
        else:
            winner = c_id if res == "win1" else j_id
            loser = j_id if res == "win1" else c_id
            # Чистый выигрыш (забираем ставку у одного, даем другому минус комиссия)
            # У обоих отнимается комиссия 5%: Победитель получает (bet * 0.95), проигравший теряет (bet)
            prize = int(bet * (1 - KNB_COMMISSION))
            
            await db.execute("UPDATE users SET energy = energy - ? WHERE user_id = ?", (bet, loser))
            await db.execute("UPDATE users SET energy = energy + ? WHERE user_id = ?", (prize, winner))
            
            result_text = (f"🏆 Победил <a href='tg://user?id={winner}'>игрок</a>!\n"
                           f"🧤 Выбор: {names[m1]} vs {names[m2]}\n"
                           f"💰 Выигрыш: **+{prize} ⚡️**")
        await db.commit()

    # Здесь нужно отправить сообщение в группу. 
    # Так как у нас нет chat_id в таблице, его лучше передать при создании или хранить в game_id.
    # Для примера отправим в DISCUSSION_GROUP_ID или используем логику уведомлений в ЛС.
    # Чтобы отправить в ту же группу, можно сохранить chat_id в таблицу knb_games.

async def knb_timeout_check(game_id, message: types.Message):
    await asyncio.sleep(KNB_TIMEOUT)
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM knb_games WHERE game_id = ? AND status = 'WAITING'", (game_id,)) as cursor:
            game = await cursor.fetchone()
            
        if not game: return # Игра уже завершилась нормально

        # Проверяем, кто не походил
        winner, loser = None, None
        if not game['c_move'] and game['j_move']:
            winner, loser = game['joiner_id'], game['creator_id']
        elif game['c_move'] and not game['j_move']:
            winner, loser = game['creator_id'], game['joiner_id']
        
        if winner:
            prize = int(game['bet'] * (1 - KNB_COMMISSION))
            await db.execute("UPDATE users SET energy = energy - ? WHERE user_id = ?", (game['bet'], loser))
            await db.execute("UPDATE users SET energy = energy + ? WHERE user_id = ?", (prize, winner))
            await db.execute("UPDATE knb_games SET status = 'TIMEOUT' WHERE game_id = ?", (game_id,))
            await db.commit()
            await message.edit_text(f"⏰ Время вышло! <a href='tg://user?id={loser}'>Игрок</a> не сделал выбор. \n🏆 Победа присуждена оппоненту!", parse_mode="HTML")
        else:
            await db.execute("UPDATE knb_games SET status = 'CANCELLED' WHERE game_id = ?", (game_id,))
            await db.commit()
            await message.edit_text("⏰ Оба игрока проигнорировали выбор. Дуэль аннулирована.")
        
# 📊 Кнопка: Подробная статистика
@dp.callback_query(F.data == "admin_stats", F.from_user.id == ADMIN_ID)
async def admin_stats_call(callback: types.CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        # 1. Общее кол-во
        async with db.execute("SELECT COUNT(*) FROM users") as c:
            total_users = (await c.fetchone())[0]
        
        # 2. Премиум пользователи
        async with db.execute("SELECT COUNT(*) FROM users WHERE is_premium = 1") as c:
            premium_users = (await c.fetchone())[0]
            
        # 3. Активные (кто играл или брал бонус за последние 24 часа)
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
        async with db.execute("SELECT COUNT(*) FROM users WHERE last_bonus > ?", (yesterday,)) as c:
            active_users = (await c.fetchone())[0]

        # 4. Процент подписавшихся
        # Считаем тех, кто прошел проверку хотя бы раз (наличие в базе + успешный get_chat_member)
        # Так как прямой флаг в БД мы не храним, самым точным будет проверить тех, 
        # кто уже начал тратить энергию (значит, они прошли проверку подписки для игры)
        async with db.execute("SELECT COUNT(*) FROM users WHERE energy < 3 OR total_won > 0") as c:
            subscribed_users = (await c.fetchone())[0]
            
        sub_percentage = (subscribed_users / total_users * 100) if total_users > 0 else 0

        # 5. Группы
        async with db.execute("SELECT chat_id, chat_name FROM groups") as c:
            groups_list = await c.fetchall()

    # Формируем текст групп
    groups_text = ""
    if groups_list:
        for gid, name in groups_list:
            # Ссылку на группу бот может получить только если у него есть invite_link
            groups_text += f"• {name} (<code>{gid}</code>)\n"
    else:
        groups_text = "Бот пока не добавлен в группы."

    stats_msg = (
        f"📊 Расширенная статистика\n\n"
        f"👥 Пользователи:\n"
        f"├ Всего: {total_users}\n"
        f"├ С Premium: {premium_users}\n"
        f"├ Без Premium: {total_users - premium_users}\n"
        f"└ Активные (24ч): {active_users}\n\n"
        f"📈 Конверсия:\n"
        f"└ Подписались: {sub_percentage:.1f}% ({subscribed_users} чел.)\n\n"
        f"🏢 Группы ({len(groups_list)}):\n"
        f"{groups_text}\n"
        f"💰 Экономика:\n"
        f"└ Всего выиграно: {(await get_global_stats())[1]} ⭐"
    )

    await callback.message.answer(stats_msg, parse_mode="HTML")
    await callback.answer()
    
@dp.callback_query(F.data == "check_sub")
async def check_cb(callback: types.CallbackQuery):
    if await is_subscribed(callback.from_user.id):
        await callback.message.delete()
        await callback.message.answer("🎉 Подписка подтверждена!", reply_markup=main_menu_kb())
    else:
        await callback.answer("❌ Вы всё еще не подписаны!", show_alert=True)


@dp.inline_query()
async def inline_check_handler(inline_query: types.InlineQuery):
    text = inline_query.query.strip().split()
    
    if len(text) < 2 or not text[0].isdigit():
        return

    amount = int(text[0])
    ctype = text[1].lower()
    user_id = inline_query.from_user.id
    
    # Определяем колонку
    if ctype in ['energy', 'энергия', '⚡️']:
        column, display_type = "energy", "energy"
    elif ctype in ['stars', 'звезды', '⭐']:
        column, display_type = "balance", "stars"
    else:
        return

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(f"SELECT {column} FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            user_balance = row[0] if row else 0

    if user_balance < amount:
        # ... (блок ошибки оставляем как был)
        return

    check_id = str(uuid.uuid4())[:8]
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(f"UPDATE users SET {column} = {column} - ? WHERE user_id = ?", (amount, user_id))
        await db.execute("INSERT INTO checks (check_id, creator_id, amount, type) VALUES (?, ?, ?, ?)",
                         (check_id, user_id, amount, display_type))
        await db.commit()

    # Ссылка на картинку
    photo_url = "https://i.postimg.cc/C5n4jpKt/Bez-nazvania246-20260404171802.png"
    
    # Формируем текст
    # Ссылка стоит первой — это критично
    message_text = (
        f'<a href="{photo_url}">&#8203;</a>'
        f"Чек на {amount} 🪙 {display_type}\n"
        f"➖➖➖➖➖\n"
        f"👤 Отправитель: {inline_query.from_user.mention_html()}\n"
        f"➖➖➖➖➖\n"
        f"👇 Жми чтобы получить 👇"
    )

    results = [
        types.InlineQueryResultArticle(
            id=check_id,
            title=f"🎁 Создать чек на {amount} {display_type}",
            input_message_content=types.InputTextMessageContent(
                message_text=message_text,
                parse_mode="HTML",
                # Включаем превью и просим Telegram показать его НАД текстом
                link_preview_options=types.LinkPreviewOptions(
                    url=photo_url,
                    show_above_text=True, # ВОТ ЭТОТ ПАРАМЕТР СТАВИТ ФОТО ВЫШЕ
                    prefer_large_media=True # Сделать картинку большой
                )
            ),
            reply_markup=InlineKeyboardBuilder().row(
                types.InlineKeyboardButton(text="Получить 🪙", callback_data=f"claim_{check_id}")
            ).as_markup()
        )
    ]
    
    await inline_query.answer(results, cache_time=1, is_personal=True)

@dp.message(F.chat.id == DISCUSSION_GROUP_ID)
async def chat_activity_bonus(message: types.Message):
    # Игнорируем входы в группу, выходы и прочие сервисные сообщения
    if not message.text:
        return
        
    if message.text.startswith("/") or message.from_user.is_bot:
        return

    # Шанс 5%
    if random.random() > CHANCE_TO_WIN:
        return
        
    # 3. Если шанс выпал — начисляем
    user_id = message.from_user.id
    
    try:
        data = await get_user_data(user_id)
        if not data:
            await add_user(user_id)

        # Начисляем 3 энергии
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "UPDATE users SET energy = energy + 3 WHERE user_id = ?", 
                (user_id,)
            )
            await db.commit()

        # 4. Отвечаем КРАТКО, чтобы не мешать общению
        await message.reply(
            "🎁 **Рандомный бонус за активность!**\n"
            "Тебе начислено **+3 ⚡ Энергии**.\n"
            "Продолжай общаться!", 
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"Ошибка в чат-бонусе: {e}")
    
# Регистрация мидлвари для всех сообщений
dp.message.middleware(ThrottlingMiddleware(slow_mode_delay=0.6))

async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
