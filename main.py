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

# Инициализация
API_TOKEN = '7773402317:AAGBdwqO_kLtpzJwNkp0s4VRD4fSsDWgQus'
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
update_button = KeyboardButton(text='Обновить данные')
delete_button = KeyboardButton(text='Удалить данные')
search_button = KeyboardButton(text='Поиск контакта')
set_departure_button = KeyboardButton(text='Указать время выезда')
menu_unregistered = ReplyKeyboardMarkup(keyboard=[[register_button]], resize_keyboard=True)
menu_registered = ReplyKeyboardMarkup(keyboard=[[update_button], [delete_button], [search_button], [set_departure_button]], resize_keyboard=True)

# Кнопки для согласия на обработку данных
consent_buttons = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text='Согласен')], [KeyboardButton(text='Не согласен')]], resize_keyboard=True)

# Кнопки для выбора отношения к подпиранию
stance_buttons = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text='Против')], [KeyboardButton(text='Готов договориться')]], resize_keyboard=True)

# Кнопка для запроса номера телефона
phone_button = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text='Поделиться номером', request_contact=True)]], resize_keyboard=True)

# Шаги для регистрации
registration_data = {}
update_data = {}
departure_data = {}

def is_user_registered(telegram_nickname):
    cursor.execute("SELECT 1 FROM users WHERE telegram_nickname = ?", (telegram_nickname,))
    return cursor.fetchone() is not None

@router.message(Command(commands=['start', 'help']))
async def send_welcome(message: Message):
    if is_user_registered(message.from_user.username):
        await message.answer("Добро пожаловать! Выберите действие:", reply_markup=menu_registered)
    else:
        await message.answer("Добро пожаловать! Пожалуйста, зарегистрируйтесь:", reply_markup=menu_unregistered)

@router.message(lambda message: message.text == 'Указать время выезда')
async def set_departure_time(message: Message):
    if not is_user_registered(message.from_user.username):
        await message.answer("Вы не зарегистрированы!", reply_markup=menu_unregistered)
        return
    cursor.execute("SELECT departure_time, departure_timestamp FROM users WHERE telegram_nickname = ?", (message.from_user.username,))
    result = cursor.fetchone()
    if result and result[1]:
        previous_time = datetime.strptime(result[1], '%Y-%m-%d %H:%M:%S')
        if datetime.now() - previous_time < timedelta(days=1):
            await message.answer(f"Вы можете выбрать: \n1. Ввести новое время \n2. Использовать прошлое время ({result[0]})", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text='Как в прошлый раз')], [KeyboardButton(text='Ввести новое время')]], resize_keyboard=True))
            return
    await message.answer("Введите время выезда в формате ЧЧ:ММ:", reply_markup=ReplyKeyboardRemove())

@router.message(lambda message: message.text == 'Как в прошлый раз')
async def use_previous_departure_time(message: Message):
    cursor.execute("SELECT departure_time FROM users WHERE telegram_nickname = ?", (message.from_user.username,))
    result = cursor.fetchone()
    if result:
        await update_departure_time(message, result[0])

@router.message(lambda message: re.match(r'^\d{2}:\d{2}$', message.text))
async def input_departure_time(message: Message):
    try:
        time = datetime.strptime(message.text, '%H:%M').strftime('%H:%M')
        await update_departure_time(message, time)
    except ValueError:
        await message.answer("Неверный формат времени. Попробуйте ещё раз в формате ЧЧ:ММ:", reply_markup=ReplyKeyboardRemove())

async def update_departure_time(message: Message, time: str):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        cursor.execute("UPDATE users SET departure_time = ?, departure_timestamp = ? WHERE telegram_nickname = ?", (time, timestamp, message.from_user.username))
        conn.commit()
        await message.answer(f"Время выезда установлено: {time}", reply_markup=menu_registered)
    except Exception as e:
        logging.error(e)
        await message.answer("Ошибка при сохранении времени выезда. Попробуйте ещё раз.", reply_markup=menu_registered)

@router.message(lambda message: message.text == 'Регистрация')
async def start_registration(message: Message):
    if is_user_registered(message.from_user.username):
        await message.answer("Вы уже зарегистрированы!", reply_markup=menu_registered)
        return
    registration_data[message.from_user.id] = {}
    await message.answer("Вы соглашаетесь на обработку персональных данных?", reply_markup=consent_buttons)

@router.message(lambda message: message.from_user.id in registration_data and 'consent' not in registration_data[message.from_user.id])
async def handle_consent(message: Message):
    if message.text == 'Согласен':
        registration_data[message.from_user.id]['consent'] = True
        await message.answer("Введите ваше имя:")
    elif message.text == 'Не согласен':
        del registration_data[message.from_user.id]
        await message.answer("Регистрация отменена.", reply_markup=menu_unregistered)

@router.message(lambda message: message.from_user.id in registration_data and 'username' not in registration_data[message.from_user.id])
async def get_name(message: Message):
    registration_data[message.from_user.id]['username'] = message.text.strip()
    await message.answer("Поделитесь своим номером телефона:", reply_markup=phone_button)

@router.message(lambda message: message.contact and message.from_user.id in registration_data and 'phone' not in registration_data[message.from_user.id])
async def get_phone(message: Message):
    registration_data[message.from_user.id]['phone'] = message.contact.phone_number
    await message.answer("Введите номер вашего автомобиля:")

@router.message(lambda message: message.from_user.id in registration_data and 'car_number' not in registration_data[message.from_user.id])
async def get_car_number(message: Message):
    car_number = message.text.strip().upper()
    if not re.match(r'^[АВЕКМНОРСТУХ]{1}\d{3}[АВЕКМНОРСТУХ]{2}\d{2,3}$', car_number):
        await message.answer("Номер автомобиля введён некорректно. Попробуйте снова (пример: А123ВС77).")
        return
    registration_data[message.from_user.id]['car_number'] = car_number
    await message.answer("Какое ваше отношение к подпиранию?", reply_markup=stance_buttons)

@router.message(lambda message: message.from_user.id in registration_data and 'stance_on_blocking' not in registration_data[message.from_user.id])
async def get_stance_on_blocking(message: Message):
    registration_data[message.from_user.id]['stance_on_blocking'] = message.text.strip()
    telegram_nickname = message.from_user.username if message.from_user.username else "Не указан"

    try:
        cursor.execute("INSERT INTO users (username, telegram_nickname, phone, car_number, stance_on_blocking, departure_time, departure_timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (
                           registration_data[message.from_user.id]['username'],
                           telegram_nickname,
                           registration_data[message.from_user.id]['phone'],
                           registration_data[message.from_user.id]['car_number'],
                           registration_data[message.from_user.id]['stance_on_blocking'],
                           None,
                           None
                       ))
        conn.commit()
        await message.answer("Вы успешно зарегистрированы!", reply_markup=menu_registered)
        del registration_data[message.from_user.id]
    except Exception as e:
        logging.error(e)
        await message.answer("Ошибка при регистрации. Попробуйте ещё раз.")
# Обновление данных
@router.message(lambda message: message.text == 'Обновить данные')
async def start_update(message: Message):
    if not is_user_registered(message.from_user.username):
        await message.answer("Вы не зарегистрированы!", reply_markup=menu_unregistered)
        return
    update_data[message.from_user.id] = {}
    await message.answer("Поделитесь новым номером телефона или введите /отмена для завершения:", reply_markup=phone_button)

@router.message(lambda message: message.contact and message.from_user.id in update_data and 'phone' not in update_data[message.from_user.id])
async def update_phone(message: Message):
    update_data[message.from_user.id]['phone'] = message.contact.phone_number
    await message.answer("Введите новый номер автомобиля или введите /отмена для завершения:")

@router.message(lambda message: message.from_user.id in update_data and 'car_number' not in update_data[message.from_user.id])
async def update_car_number(message: Message):
    car_number = message.text.strip().upper()
    if not re.match(r'^[АВЕКМНОРСТУХ]{1}\d{3}[АВЕКМНОРСТУХ]{2}\d{2,3}$', car_number):
        await message.answer("Номер автомобиля введён некорректно. Попробуйте снова (пример: А123ВС77).")
        return
    update_data[message.from_user.id]['car_number'] = car_number
    await message.answer("Выберите новое отношение к подпиранию или введите /отмена для завершения:", reply_markup=stance_buttons)

@router.message(lambda message: message.from_user.id in update_data and 'stance_on_blocking' not in update_data[message.from_user.id])
async def update_stance_on_blocking(message: Message):
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
    del update_data[message.from_user.id]
    await message.answer("Обновление данных отменено.", reply_markup=menu_registered)
# Удаление данных
@router.message(lambda message: message.text == 'Удалить данные')
async def delete_data(message: Message):
    if not is_user_registered(message.from_user.username):
        await message.answer("Вы не зарегистрированы!", reply_markup=menu_unregistered)
        return
    try:
        cursor.execute("DELETE FROM users WHERE telegram_nickname = ?", (message.from_user.username,))
        conn.commit()
        await message.answer("Ваши данные удалены из системы.", reply_markup=menu_unregistered)
    except Exception as e:
        logging.error(e)
        await message.answer("Ошибка при удалении данных. Попробуйте ещё раз.")

# Поиск контактов
@router.message(lambda message: message.text == 'Поиск контакта')
async def search_contact(message: Message):
    if not is_user_registered(message.from_user.username):
        await message.answer("Вы не зарегистрированы!", reply_markup=menu_unregistered)
        return
    await message.answer("Отправьте номер автомобиля текстом или загрузите фотографию номера.")

@router.message(lambda message: message.text and re.match(r'^\w+$', message.text))
async def find_contact_by_text(message: Message):
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
