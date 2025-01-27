import logging
import asyncio
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, Message, PhotoSize, ReplyKeyboardRemove
import sqlite3
import re
from PIL import Image
import pytesseract
import io
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os

# Инициализация
load_dotenv()
API_TOKEN = os.getenv("API_TOKEN")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

# База данных
conn = sqlite3.connect("parking_bot.db")
cursor = conn.cursor()

cursor.execute('''CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT,
                    telegram_nickname TEXT,
                    phone TEXT,
                    car_number TEXT,
                    stance_on_blocking TEXT,
                    departure_time TEXT,
                    departure_timestamp TEXT)''')
conn.commit()

# Кнопки меню
register_button = KeyboardButton(text='Регистрация')
#update_button = KeyboardButton(text='Обновить данные')
delete_button = KeyboardButton(text='Удалить данные')
search_button = KeyboardButton(text='Поиск контакта')
set_departure_button = KeyboardButton(text='Указать время выезда')
menu_unregistered = ReplyKeyboardMarkup(keyboard=[[register_button]], resize_keyboard=True)
#menu_registered = ReplyKeyboardMarkup(keyboard=[[update_button], [delete_button], [search_button], [set_departure_button]], resize_keyboard=True)
menu_registered = ReplyKeyboardMarkup(keyboard=[[search_button], [set_departure_button],[delete_button] ], resize_keyboard=True)

# Кнопки для согласия на обработку данных
consent_buttons = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text='Согласен')], [KeyboardButton(text='Не согласен')]], resize_keyboard=True)

# Кнопки для выбора отношения к подпиранию
stance_buttons = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text='Против')], [KeyboardButton(text='Готов договориться')]], resize_keyboard=True)

# Кнопка для запроса номера телефона
phone_button = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text='Поделиться номером', request_contact=True)]], resize_keyboard=True)
departure_time_keyboard = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text='Как в прошлый раз')]],
    resize_keyboard=True
)

# Шаги для регистрации
registration_data = {}
update_data = {}
departure_data = {}
search_data = {}
deletion_data = {}

def is_user_registered(telegram_nickname):
    cursor.execute("SELECT 1 FROM users WHERE telegram_nickname = ?", (telegram_nickname,))
    return cursor.fetchone() is not None

# @router.message(Command(commands=['start', 'help']))
# async def send_welcome(message: Message):
#     logging.info(f"start_registration: {message.text}")
#     if is_user_registered(message.from_user.username):
#         await message.answer("Добро пожаловать! Выберите действие:", reply_markup=menu_registered)
#     else:
#         await message.answer("Добро пожаловать! Пожалуйста, зарегистрируйтесь:", reply_markup=menu_unregistered)

@router.message()
async def universal_router(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username

    logging.info(f"universal_router: {message.text}, user_id: {user_id}, username: {username}")
    # Проверяем, если пользователь находится в процессе регистрации
    if user_id in registration_data:
        state = registration_data[user_id].get('state')
        if state == 'awaiting_consent':
            await handle_consent(message)
        elif state == 'awaiting_name':
            await handle_name(message)
        elif state == 'awaiting_phone':
            await handle_phone(message)
        elif state == 'awaiting_car_number':
            await handle_car_number(message)
        elif state == 'awaiting_stance':
            await handle_stance_on_blocking(message)
        return
    # Проверяем, если пользователь находится в процессе обновления данных
    if user_id in search_data and search_data[user_id].get('awaiting_input'):
        await find_contact_by_text(message)
        return
    if user_id in departure_data and departure_data[user_id].get('awaiting_time'):
        if message.text == 'Как в прошлый раз':
            await use_previous_departure_time(message)
        else:
            await handle_departure_time(message)
        return
    if user_id in deletion_data and deletion_data[user_id].get('awaiting_confirmation'):
        await confirm_delete_data(message)
        return        
    # Проверяем, зарегистрирован ли пользователь
    if not is_user_registered(username):
        if message.text == 'Регистрация':
            registration_data[user_id] = {'state': 'awaiting_consent'}
            await message.answer("Вы соглашаетесь на обработку персональных данных?", reply_markup=consent_buttons)
        else:
            await message.answer("Вы не зарегистрированы. Пожалуйста, зарегистрируйтесь:", reply_markup=menu_unregistered)
        return
    
    # Действия для зарегистрированных пользователей
    if message.text == 'Поиск контакта':
        await search_contact(message)
    elif message.text == 'Указать время выезда':
        await set_departure_time_flag(message)
    elif message.text == 'Обновить данные':
        await start_update(message)
    elif message.text == 'Удалить данные':
        await start_delete_data(message)
    else:
        await message.answer("Выберите действие:", reply_markup=menu_registered)


#@router.message(lambda message: message.text == 'Указать время выезда')
async def set_departure_time_flag(message: Message):
    user_id = message.from_user.id
    departure_data[user_id] = {'awaiting_time': True}
    await message.answer(
        "Введите время отправления в формате ЧЧ:ММ или нажмите 'Как в прошлый раз':",
        reply_markup=departure_time_keyboard
    )

async def use_previous_departure_time(message: Message):
    user_id = message.from_user.id

    cursor.execute("SELECT departure_time FROM users WHERE telegram_nickname = ?", (message.from_user.username,))
    result = cursor.fetchone()
    del departure_data[user_id]

    if result and result[0]:
        await update_departure_time(message, result[0])
    else:
        departure_data[user_id] = {'awaiting_time': True}  # Сохраняем флаг для повторного ввода
        await message.answer(
            "Не удалось найти предыдущее время выезда. Укажите его вручную:",
            reply_markup=departure_time_keyboard
        )

async def handle_departure_time(message: Message):
    user_id = message.from_user.id
    # Убираем флаг после обработки времени
    del departure_data[user_id]
    try:
        time = datetime.strptime(message.text, '%H:%M').strftime('%H:%M')
        await update_departure_time(message, time)
    except ValueError:
        # Если формат некорректен, повторяем запрос
        departure_data[user_id] = {'awaiting_time': True}  # Сохраняем флаг
        await message.answer(
            "Неверный формат времени. Попробуйте ещё раз в формате ЧЧ:ММ:",
            reply_markup=departure_time_keyboard
        )

async def update_departure_time(message: Message, time: str):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        cursor.execute(
            "UPDATE users SET departure_time = ?, departure_timestamp = ? WHERE telegram_nickname = ?",
            (time, timestamp, message.from_user.username)
        )
        conn.commit()
        await message.answer(
            f"Время выезда установлено: {time}",
            reply_markup=menu_registered
        )
    except Exception as e:
        logging.error(f"Ошибка при обновлении времени выезда: {e}")
        await message.answer("Ошибка при сохранении времени выезда. Попробуйте ещё раз.")

async def start_registration(message: Message):
    logging.info(f"start_registration: {message.text}")
    if is_user_registered(message.from_user.username):
        await message.answer("Вы уже зарегистрированы!", reply_markup=menu_registered)
        return
    registration_data[message.from_user.id] = {}
    await message.answer("Вы соглашаетесь на обработку персональных данных?", reply_markup=consent_buttons)

async def handle_consent(message: Message):
    user_id = message.from_user.id

    if message.text == 'Согласен':
        registration_data[user_id]['state'] = 'awaiting_name'
        await message.answer("Введите ваше имя:")
    elif message.text == 'Не согласен':
        del registration_data[user_id]
        await message.answer("Регистрация отменена.", reply_markup=menu_unregistered)
    else:
        await message.answer("Пожалуйста, выберите 'Согласен' или 'Не согласен'.")

async def handle_name(message: Message):
    user_id = message.from_user.id

    registration_data[user_id]['username'] = message.text.strip()
    registration_data[user_id]['state'] = 'awaiting_phone'
    await message.answer("Поделитесь своим номером телефона:", reply_markup=phone_button)

async def handle_phone(message: Message):
    user_id = message.from_user.id

    if message.contact:
        registration_data[user_id]['phone'] = message.contact.phone_number
        registration_data[user_id]['state'] = 'awaiting_car_number'
        await message.answer("Введите номер вашего автомобиля:")
    else:
        await message.answer("Пожалуйста, поделитесь своим номером телефона.")

async def handle_car_number(message: Message):
    user_id = message.from_user.id

    car_number = message.text.strip().upper()
    if re.match(r'^[АВЕКМНОРСТУХ]{1}\d{3}[АВЕКМНОРСТУХ]{2}\d{2,3}$', car_number):
        registration_data[user_id]['car_number'] = car_number
        registration_data[user_id]['state'] = 'awaiting_stance'
        await message.answer("Какое ваше отношение к подпиранию?", reply_markup=stance_buttons)
    else:
        await message.answer("Номер автомобиля введён некорректно. Попробуйте снова (пример: А123ВС138).")

async def handle_stance_on_blocking(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username if message.from_user.username else "Не указан"

    registration_data[user_id]['stance_on_blocking'] = message.text.strip()

    try:
        cursor.execute(
            "INSERT INTO users (username, telegram_nickname, phone, car_number, stance_on_blocking, departure_time, departure_timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                registration_data[user_id]['username'],
                username,
                registration_data[user_id]['phone'],
                registration_data[user_id]['car_number'],
                registration_data[user_id]['stance_on_blocking'],
                None,
                None
            )
        )
        conn.commit()
        del registration_data[user_id]  # Удаляем данные после завершения регистрации
        await message.answer("Вы успешно зарегистрированы!", reply_markup=menu_registered)
    except Exception as e:
        logging.error(e)
        await message.answer("Ошибка при регистрации. Попробуйте ещё раз.")

# Обновление данных
@router.message(lambda message: message.text == 'Обновить данные')
async def start_update(message: Message):
    logging.info(f"start_update: {message.text}")
    if not is_user_registered(message.from_user.username):
        await message.answer("Вы не зарегистрированы!", reply_markup=menu_unregistered)
        return
    update_data[message.from_user.id] = {}
    await message.answer("Поделитесь новым номером телефона или введите /отмена для завершения:", reply_markup=phone_button)

@router.message(lambda message: message.contact and message.from_user.id in update_data and 'phone' not in update_data[message.from_user.id])
async def update_phone(message: Message):
    logging.info(f"update_phone: {message.text}")
    update_data[message.from_user.id]['phone'] = message.contact.phone_number
    await message.answer("Введите новый номер автомобиля или введите /отмена для завершения:")

@router.message(lambda message: message.from_user.id in update_data and 'car_number' not in update_data[message.from_user.id])
async def update_car_number(message: Message):
    logging.info(f"Фолбэк обработчик: {message.text}")
    car_number = message.text.strip().upper()
    if not re.match(r'^[АВЕКМНОРСТУХ]{1}\d{3}[АВЕКМНОРСТУХ]{2}\d{2,3}$', car_number):
        await message.answer("Номер автомобиля введён некорректно. Попробуйте снова (пример: А123ВС77).")
        return
    update_data[message.from_user.id]['car_number'] = car_number
    await message.answer("Выберите новое отношение к подпиранию или введите /отмена для завершения:", reply_markup=stance_buttons)

@router.message(lambda message: message.from_user.id in update_data and 'stance_on_blocking' not in update_data[message.from_user.id])
async def update_stance_on_blocking(message: Message):
    logging.info(f"update_stance_on_blocking: {message.text}")
    update_data[message.from_user.id]['stance_on_blocking'] = message.text.strip()
    telegram_nickname = message.from_user.username if message.from_user.username else "Не указан"

    try:
        cursor.execute("UPDATE users SET phone = ?, car_number = ?, stance_on_blocking = ?, telegram_nickname = ? WHERE telegram_nickname = ?",
                       (
                           update_data[message.from_user.id]['phone'],
                           update_data[message.from_user.id]['car_number'],
                           update_data[message.from_user.id]['stance_on_blocking'],
                           telegram_nickname,
                           message.from_user.username
                       ))
        conn.commit()
        await message.answer("Данные обновлены!", reply_markup=menu_registered)
        del update_data[message.from_user.id]
    except Exception as e:
        logging.error(e)
        await message.answer("Ошибка при обновлении данных. Попробуйте ещё раз.")

@router.message(lambda message: message.text == '/отмена' and message.from_user.id in update_data)
async def cancel_update(message: Message):
    logging.info(f"cancel_update: {message.text}")
    del update_data[message.from_user.id]
    await message.answer("Обновление данных отменено.", reply_markup=menu_registered)
# Удаление данных
async def start_delete_data(message: Message):
    user_id = message.from_user.id

    if not is_user_registered(message.from_user.username):
        await message.answer("Вы не зарегистрированы!", reply_markup=menu_unregistered)
        return

    # Устанавливаем флаг для подтверждения удаления
    deletion_data[user_id] = {'awaiting_confirmation': True}
    await message.answer(
        "Вы уверены, что хотите удалить свои данные? Это действие нельзя отменить.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Да, удалить данные")],
                [KeyboardButton(text="Нет, отменить")]
            ],
            resize_keyboard=True
        )
    )
async def confirm_delete_data(message: Message):
    user_id = message.from_user.id

    # Проверяем, если пользователь находится в процессе подтверждения удаления
    if user_id not in deletion_data or not deletion_data[user_id].get('awaiting_confirmation'):
        return

    if message.text == "Да, удалить данные":
        try:
            cursor.execute("DELETE FROM users WHERE telegram_nickname = ?", (message.from_user.username,))
            conn.commit()
            await message.answer("Ваши данные удалены из системы.", reply_markup=menu_unregistered)
        except Exception as e:
            logging.error(f"Ошибка при удалении данных: {e}")
            await message.answer("Произошла ошибка при удалении данных. Попробуйте ещё раз.")
    elif message.text == "Нет, отменить":
        await message.answer("Удаление данных отменено.", reply_markup=menu_registered)

    # Удаляем флаг подтверждения
    del deletion_data[user_id]

# Поиск контактов
async def search_contact(message: Message):
    user_id = message.from_user.id

    if not is_user_registered(message.from_user.username):
        await message.answer("Вы не зарегистрированы!", reply_markup=menu_unregistered)
        return

    # Устанавливаем флаг состояния поиска контакта
    search_data[user_id] = {'awaiting_input': True}
    await message.answer("Отправьте номер автомобиля текстом (можно только цифры)")


async def find_contact_by_text(message: Message):
    user_id = message.from_user.id

    # Проверяем, ожидается ли ввод номера
    if user_id not in search_data or not search_data[user_id].get('awaiting_input'):
        return

    # Удаляем флаг после получения ввода
    del search_data[user_id]
    car_number = re.sub(r'\D', '', message.text.strip().upper())
    cursor.execute("SELECT username, telegram_nickname, phone, stance_on_blocking, departure_time FROM users WHERE car_number LIKE ?", (f"%{car_number}%",))
    result = cursor.fetchone()
    if result:
        name, telegram_nickname, phone, stance_on_blocking, departure_time = result
        departure_info = f"\nВремя выезда: {departure_time}" if departure_time else "\nВремя выезда: не указано"
        await message.answer(f"Контакт владельца:\nИмя: {name}\nТелеграм: @{telegram_nickname}\nТелефон: [{phone}](tel:{phone})\nОтношение к подпиранию: {stance_on_blocking}{departure_info}", parse_mode="Markdown")
    else:
        await message.answer("Контакт не найден.")

@router.message(lambda message: message.photo)
async def find_contact_by_photo(message: Message):
    photo = message.photo[-1]
    photo_file = await bot.download(photo)
    image = Image.open(photo_file)
    text = pytesseract.image_to_string(image, lang='eng+rus')
    car_number_match = re.search(r'[АВЕКМНОРСТУХ]{1}\d{3}[АВЕКМНОРСТУХ]{2}\d{2,3}', text.upper())

    if car_number_match:
        car_number = car_number_match.group(0)
        cursor.execute("SELECT username, telegram_nickname, phone, stance_on_blocking, departure_time FROM users WHERE car_number LIKE ?", (f"%{car_number}%",))
        result = cursor.fetchone()
        if result:
            name, telegram_nickname, phone, stance_on_blocking, departure_time = result
            departure_info = f"\nВремя выезда: {departure_time}" if departure_time else "\nВремя выезда: не указано"
            await message.answer(f"Контакт владельца:\nИмя: {name}\nТелеграм: @{telegram_nickname}\nТелефон: [{phone}](tel:{phone})\nОтношение к подпиранию: {stance_on_blocking}{departure_info}", parse_mode="Markdown")
        else:
            await message.answer("Контакт не найден.")
    else:
        await message.answer("Номер автомобиля не распознан. Попробуйте ещё раз с другим изображением.")

async def main():
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
