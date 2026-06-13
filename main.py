import os
import json
import base64
import logging
import tempfile
from datetime import date
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import (
    Application, MessageHandler, filters, ContextTypes, CommandHandler
)
from openai import AsyncOpenAI
from supabase import create_client, Client

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

CONFIRM_WORDS = ["да", "подтверждаю", "сохраняй", "ок", "окей", "yes", "верно", "точно", "всё верно", "все верно"]
DENY_WORDS = ["нет", "неверно", "не то", "отмена", "cancel", "no", "стоп"]


def is_confirm(text: str) -> bool:
    return any(w in text.lower() for w in CONFIRM_WORDS)


def is_deny(text: str) -> bool:
    return any(w in text.lower() for w in DENY_WORDS)


def get_pending():
    result = supabase.table("pending_actions").select("*").eq("status", "pending").order("created_at", desc=True).limit(1).execute()
    if result.data:
        return result.data[0]
    return None


def clear_pending(pending_id: str):
    supabase.table("pending_actions").update({"status": "done"}).eq("id", pending_id).execute()


def save_pending(action_type: str, data: dict, telegram_message_id: str = None):
    supabase.table("pending_actions").insert({
        "type": action_type,
        "data": data,
        "status": "pending",
        "telegram_message_id": str(telegram_message_id) if telegram_message_id else None
    }).execute()


async def transcribe_voice(file_path: str) -> str:
    with open(file_path, "rb") as audio_file:
        response = await openai_client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language="ru",
            timeout=30
        )
    return response.text


async def analyze_image(image_bytes: bytes, prompt: str) -> str:
    b64 = base64.b64encode(image_bytes).decode()
    response = await openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                {"type": "text", "text": prompt}
            ]
        }],
        max_tokens=1000,
        timeout=30
    )
    return response.choices[0].message.content


async def ask_ai(system: str, user_message: str) -> str:
    response = await openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_message}
        ],
        max_tokens=1000,
        timeout=30
    )
    return response.choices[0].message.content


def parse_json_response(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
    return json.loads(text.strip())


async def handle_body_measurement_image(image_bytes: bytes) -> dict:
    prompt = """On this image is a screenshot from a smart scale app. Extract all numeric measurements.
Return ONLY JSON, no other text:
{
  "weight": number or null,
  "bmi": number or null,
  "fat_percent": number or null,
  "muscle_percent": number or null,
  "water_percent": number or null,
  "visceral_fat": integer or null,
  "bmr": integer or null,
  "fat_mass": number or null,
  "lean_mass": number or null,
  "bone_mass": number or null
}"""
    result = await analyze_image(image_bytes, prompt)
    return parse_json_response(result)


async def handle_product_image(image_bytes: bytes) -> dict:
    prompt = """On this image is a food product label. Extract name, brand and nutrition info per 100g.
Return ONLY JSON, no other text:
{
  "name": "product name",
  "brand": "brand or null",
  "calories": number per 100g,
  "protein": number per 100g,
  "fat": number per 100g,
  "carbs": number per 100g
}"""
    result = await analyze_image(image_bytes, prompt)
    return parse_json_response(result)


async def classify_message(text: str) -> dict:
    system = """You are a classifier for a health and nutrition assistant. Classify the user message and return JSON:
{
  "type": one of the types below,
  "data": extracted data
}

Types:
- "body_measurement_text" - user describes body measurements in text
- "add_product" - add a product to database with nutrition info
- "daily_goals" - request daily nutrition goals
- "meal_plan" - create a meal plan for the day
- "food_log" - log what was eaten
- "workout_update" - user mentions workout
- "other" - anything else

For "food_log" extract: [{"name": "product", "grams": number}, ...]
For "add_product" extract: {"name": "...", "brand": "...", "calories": ..., "protein": ..., "fat": ..., "carbs": ...}
For "workout_update" extract: {"has_workout": true/false}
For "meal_plan" extract: {"products": ["list"], "meals_count": number, "notes": "notes"}

Return ONLY JSON."""

    result = await ask_ai(system, text)
    return parse_json_response(result)


def format_measurement(data: dict) -> str:
    lines = ["Я распознал замер:\n"]
    if data.get("weight"): lines.append(f"Вес: {data['weight']} кг")
    if data.get("bmi"): lines.append(f"ИМТ: {data['bmi']}")
    if data.get("fat_percent"): lines.append(f"Жир: {data['fat_percent']}%")
    if data.get("muscle_percent"): lines.append(f"Мышцы: {data['muscle_percent']}%")
    if data.get("water_percent"): lines.append(f"Вода: {data['water_percent']}%")
    if data.get("visceral_fat"): lines.append(f"Висцеральный жир: {data['visceral_fat']}")
    if data.get("bmr"): lines.append(f"Базовый обмен: {data['bmr']} ккал")
    if data.get("fat_mass"): lines.append(f"Жировая масса: {data['fat_mass']} кг")
    if data.get("lean_mass"): lines.append(f"Вес без жира: {data['lean_mass']} кг")
    lines.append("\nПодтверждаешь сохранение?")
    return "\n".join(lines)


def format_product(data: dict) -> str:
    brand = f" ({data['brand']})" if data.get("brand") else ""
    return (
        f"Нашёл продукт:\n\n"
        f"{data['name']}{brand}\n"
        f"на 100 г:\n"
        f"{data['calories']} ккал\n"
        f"Белки: {data['protein']} г\n"
        f"Жиры: {data['fat']} г\n"
        f"Углеводы: {data['carbs']} г\n\n"
        f"Подтверждаешь добавление в базу?"
    )


async def save_confirmed_action(pending: dict) -> str:
    action_type = pending["type"]
    data = pending["data"]
    today = date.today().isoformat()

    if action_type == "body_measurement":
        data["date"] = today
        supabase.table("body_measurements").upsert(data, on_conflict="date").execute()
        return "Замер сохранён!"

    elif action_type == "product":
        supabase.table("products").insert(data).execute()
        return f"Продукт «{data['name']}» добавлен в базу!"

    elif action_type == "food_log":
        for item in data.get("items", []):
            item["date"] = today
            supabase.table("food_log").insert(item).execute()
        return "Еда записана в дневник!"

    elif action_type == "daily_goals":
        data["date"] = today
        supabase.table("daily_goals").upsert(data, on_conflict="date").execute()
        return "Дневные цели сохранены!"

    return "Сохранено!"


async def calculate_daily_goals(bmr: int, has_workout: bool) -> dict:
    calories = int(bmr * 1.4) if has_workout else int(bmr * 1.2)
    protein = int((calories * 0.30) / 4)
    fat = int((calories * 0.25) / 9)
    carbs = int((calories * 0.45) / 4)
    return {
        "calories": calories,
        "protein": protein,
        "fat": fat,
        "carbs": carbs,
        "calorie_buffer": 250,
        "has_workout": has_workout,
        "bmr_used": bmr
    }


async def process_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    pending = get_pending()

    if pending:
        if is_confirm(text):
            clear_pending(pending["id"])
            result_msg = await save_confirmed_action(pending)
            await update.message.reply_text(f"✓ {result_msg}")
            return
        elif is_deny(text):
            clear_pending(pending["id"])
            await update.message.reply_text("Отменено. Что хочешь сделать?")
            return

    try:
        classified = await classify_message(text)
        msg_type = classified.get("type")
        data = classified.get("data", {})
    except Exception as e:
        logger.error(f"classify error: {e}")
        await update.message.reply_text("Не смог разобрать запрос. Попробуй ещё раз или переформулируй.")
        return

    if msg_type == "body_measurement_text":
        save_pending("body_measurement", data, update.message.message_id)
        await update.message.reply_text(format_measurement(data))

    elif msg_type == "add_product":
        save_pending("product", data, update.message.message_id)
        await update.message.reply_text(format_product(data))

    elif msg_type in ("daily_goals", "workout_update"):
        has_workout = data.get("has_workout", False)
        measurement = supabase.table("body_measurements").select("bmr").order("date", desc=True).limit(1).execute()
        bmr = 1800
        if measurement.data and measurement.data[0].get("bmr"):
            bmr = measurement.data[0]["bmr"]

        goals = await calculate_daily_goals(bmr, has_workout)
        workout_text = "с тренировкой" if has_workout else "без тренировки"
        msg = (
            f"Сегодня {workout_text}.\n\n"
            f"Цель дня:\n"
            f"{goals['calories']} ккал\n"
            f"{goals['protein']} г белка\n"
            f"{goals['fat']} г жиров\n"
            f"{goals['carbs']} г углеводов\n"
            f"Запас: {goals['calorie_buffer']} ккал\n\n"
            f"Подтверждаешь?"
        )
        save_pending("daily_goals", goals, update.message.message_id)
        await update.message.reply_text(msg)

    elif msg_type == "food_log":
        items = data if isinstance(data, list) else data.get("items", [])
        total_cal = total_p = total_f = total_c = 0
        log_items = []

        for item in items:
            product_name = item.get("name", "")
            grams = item.get("grams", 100)
            product = supabase.table("products").select("*").ilike("name", f"%{product_name}%").limit(1).execute()

            if product.data:
                p = product.data[0]
                ratio = grams / 100
                cal = round(p["calories"] * ratio, 1)
                prot = round(p["protein"] * ratio, 1)
                fat_v = round(p["fat"] * ratio, 1)
                carb = round(p["carbs"] * ratio, 1)
                log_items.append({"product_name": product_name, "grams": grams, "calories": cal, "protein": prot, "fat": fat_v, "carbs": carb, "product_id": p["id"]})
                total_cal += cal; total_p += prot; total_f += fat_v; total_c += carb
            else:
                log_items.append({"product_name": product_name, "grams": grams, "calories": None, "protein": None, "fat": None, "carbs": None})

        lines = ["Записать как съеденное?\n"]
        for item in log_items:
            if item.get("calories"):
                lines.append(f"{item['product_name']}: {item['grams']} г ({item['calories']} ккал)")
            else:
                lines.append(f"{item['product_name']}: {item['grams']} г (нет в базе — добавь продукт сначала)")
        if total_cal > 0:
            lines.append(f"\nИтого: {round(total_cal)} ккал | Б: {round(total_p)} г | Ж: {round(total_f)} г | У: {round(total_c)} г")
        lines.append("\nПодтверждаешь?")
        save_pending("food_log", {"items": log_items}, update.message.message_id)
        await update.message.reply_text("\n".join(lines))

    else:
        await update.message.reply_text(
            "Я могу помочь с:\n"
            "• Замер тела — отправь скриншот весов\n"
            "• Продукт — напиши название и КБЖУ или фото этикетки\n"
            "• Цели дня — напиши «посчитай цели на сегодня»\n"
            "• Еда — напиши что съел\n"
            "• Голосовые — говори, я пойму"
        )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    image_bytes = bytes(await file.download_as_bytearray())
    await update.message.reply_text("Анализирую изображение...")

    try:
        data = await handle_body_measurement_image(image_bytes)
        if data.get("weight"):
            save_pending("body_measurement", data, update.message.message_id)
            await update.message.reply_text(format_measurement(data))
            return
    except Exception as e:
        logger.error(f"body measurement image error: {e}")

    try:
        data = await handle_product_image(image_bytes)
        if data.get("name") and data.get("calories"):
            save_pending("product", data, update.message.message_id)
            await update.message.reply_text(format_product(data))
            return
    except Exception as e:
        logger.error(f"product image error: {e}")

    await update.message.reply_text("Не смог распознать. Отправь скриншот весов или фото этикетки продукта.")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    voice = update.message.voice
    file = await context.bot.get_file(voice.file_id)

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        await file.download_to_drive(tmp_path)
        text = await transcribe_voice(tmp_path)
    finally:
        os.unlink(tmp_path)

    await update.message.reply_text(f"Распознал: «{text}»\n\nОбрабатываю...")
    await process_text_message(update, context, text)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    await process_text_message(update, context, text)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я твой помощник по здоровью и питанию.\n\n"
        "Что умею:\n"
        "• Читать скриншоты умных весов\n"
        "• Распознавать этикетки продуктов\n"
        "• Считать дневные цели питания\n"
        "• Записывать что ты съел\n"
        "• Понимать голосовые сообщения\n\n"
        "Начни с отправки скриншота весов или напиши что хочешь сделать."
    )


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("Bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
