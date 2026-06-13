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

CONFIRM_WORDS = ["да", "подтверждаю", "сохраняй", "ок", "окей", "yes", "верно", "точно", "всё верно", "все верно", "сохрани", "давай"]
DENY_WORDS = ["нет", "неверно", "не то", "отмена", "cancel", "no", "стоп", "не надо"]


def is_confirm(text: str) -> bool:
    t = text.lower().strip()
    return any(t == w or t.startswith(w) for w in CONFIRM_WORDS)


def is_deny(text: str) -> bool:
    t = text.lower().strip()
    return any(t == w or t.startswith(w) for w in DENY_WORDS)


def get_pending():
    result = supabase.table("pending_actions").select("*").eq("status", "pending").order("created_at", desc=True).limit(1).execute()
    if result.data:
        return result.data[0]
    return None


def clear_pending(pending_id: str):
    supabase.table("pending_actions").update({"status": "done"}).eq("id", pending_id).execute()


def save_pending(action_type: str, data: dict, telegram_message_id: str = None):
    supabase.table("pending_actions").update({"status": "expired"}).eq("status", "pending").execute()
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


async def ask_ai(system: str, user_message: str, model: str = "gpt-4o-mini") -> str:
    response = await openai_client.chat.completions.create(
        model=model,
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


async def estimate_macros(product_name: str, grams: float) -> dict:
    """Ask GPT to estimate macros for food not in DB."""
    system = """Ты эксперт по питанию. Оцени КБЖУ продукта на 100г.
Верни ТОЛЬКО JSON без лишнего текста:
{"calories": число, "protein": число, "fat": число, "carbs": число}
Числа - реальные средние значения для этого продукта."""
    result = await ask_ai(system, f"Продукт: {product_name}")
    data = parse_json_response(result)
    ratio = grams / 100
    return {
        "calories": round(data["calories"] * ratio, 1),
        "protein": round(data["protein"] * ratio, 1),
        "fat": round(data["fat"] * ratio, 1),
        "carbs": round(data["carbs"] * ratio, 1),
        "cal100": data["calories"],
        "pro100": data["protein"],
        "fat100": data["fat"],
        "carb100": data["carbs"],
    }


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
    system = """Ты классификатор для персонального ассистента по здоровью и питанию.
Определи тип сообщения и верни JSON:
{
  "type": один из типов ниже,
  "data": извлечённые данные
}

Типы:
- "body_measurement_text" - пользователь описывает замеры тела (вес, жир, мышцы и тд)
- "add_product" - добавить продукт с КБЖУ в базу (когда явно указаны калории/белки/жиры/углеводы на 100г)
- "daily_goals" - запрос целей питания на день / упоминание тренировки
- "food_log" - записать что съел (просто еда и граммы, без КБЖУ)
- "query_today" - вопрос о том что съел сегодня / сколько калорий / остаток КБЖУ
- "query_weight" - вопрос о динамике веса / замерах тела
- "general_chat" - общий вопрос, совет, мотивация, вопрос о питании/здоровье

Для "food_log" извлеки: [{"name": "продукт", "grams": число}, ...]
Для "add_product" извлеки: {"name": "...", "brand": null, "calories": ..., "protein": ..., "fat": ..., "carbs": ...}
Для "workout_update" извлеки: {"has_workout": true/false}
Для "daily_goals" извлеки: {"has_workout": true или false}

Верни ТОЛЬКО JSON."""

    result = await ask_ai(system, text)
    return parse_json_response(result)


def get_today_stats() -> dict:
    today = date.today().isoformat()
    food = supabase.table("food_log").select("*").eq("date", today).execute()
    goals = supabase.table("daily_goals").select("*").eq("date", today).single().execute()

    total_cal = total_p = total_f = total_c = 0
    items = []
    for row in (food.data or []):
        total_cal += row.get("calories") or 0
        total_p += row.get("protein") or 0
        total_f += row.get("fat") or 0
        total_c += row.get("carbs") or 0
        items.append(f"• {row.get('product_name', '?')}: {row.get('grams', 0)} г ({round(row.get('calories') or 0)} ккал)")

    return {
        "items": items,
        "total_cal": round(total_cal),
        "total_p": round(total_p),
        "total_f": round(total_f),
        "total_c": round(total_c),
        "goals": goals.data,
    }


def get_weight_history() -> list:
    rows = supabase.table("body_measurements").select("date,weight,fat_percent,muscle_percent").order("date", desc=True).limit(10).execute()
    return rows.data or []


def format_measurement(data: dict) -> str:
    lines = ["Я распознал замер:\n"]
    if data.get("weight"): lines.append(f"⚖️ Вес: {data['weight']} кг")
    if data.get("bmi"): lines.append(f"📊 ИМТ: {data['bmi']}")
    if data.get("fat_percent"): lines.append(f"🟡 Жир: {data['fat_percent']}%")
    if data.get("muscle_percent"): lines.append(f"💪 Мышцы: {data['muscle_percent']}%")
    if data.get("water_percent"): lines.append(f"💧 Вода: {data['water_percent']}%")
    if data.get("visceral_fat"): lines.append(f"🔴 Висцеральный жир: {data['visceral_fat']}")
    if data.get("bmr"): lines.append(f"🔥 Базовый обмен: {data['bmr']} ккал")
    if data.get("fat_mass"): lines.append(f"Жировая масса: {data['fat_mass']} кг")
    if data.get("lean_mass"): lines.append(f"Сухая масса: {data['lean_mass']} кг")
    lines.append("\nПодтверждаешь сохранение?")
    return "\n".join(lines)


def format_product(data: dict) -> str:
    brand = f" ({data['brand']})" if data.get("brand") else ""
    return (
        f"📦 {data['name']}{brand}\n"
        f"на 100 г:\n"
        f"🔥 {data['calories']} ккал\n"
        f"🥩 Белки: {data['protein']} г\n"
        f"🧈 Жиры: {data['fat']} г\n"
        f"🌾 Углеводы: {data['carbs']} г\n\n"
        f"Добавить в базу продуктов?"
    )


async def save_confirmed_action(pending: dict) -> str:
    action_type = pending["type"]
    data = pending["data"]
    today = date.today().isoformat()

    if action_type == "body_measurement":
        data["date"] = today
        supabase.table("body_measurements").upsert(data, on_conflict="date").execute()
        return "✅ Замер сохранён!"

    elif action_type == "product":
        supabase.table("products").insert(data).execute()
        return f"✅ Продукт «{data['name']}» добавлен в базу!"

    elif action_type == "food_log":
        for item in data.get("items", []):
            item["date"] = today
            row = {k: v for k, v in item.items() if k != "cal100" and k != "pro100" and k != "fat100" and k != "carb100"}
            supabase.table("food_log").insert(row).execute()
            # Also auto-save product to DB if not exists
            if item.get("auto_estimated") and item.get("product_name"):
                existing = supabase.table("products").select("id").ilike("name", f"%{item['product_name']}%").limit(1).execute()
                if not existing.data:
                    supabase.table("products").insert({
                        "name": item["product_name"],
                        "calories": item.get("cal100"),
                        "protein": item.get("pro100"),
                        "fat": item.get("fat100"),
                        "carbs": item.get("carb100"),
                    }).execute()
        return "✅ Еда записана в дневник!"

    elif action_type == "daily_goals":
        data["date"] = today
        supabase.table("daily_goals").upsert(data, on_conflict="date").execute()
        return "✅ Цели дня сохранены!"

    return "✅ Сохранено!"


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
            await update.message.reply_text(result_msg)
            return
        elif is_deny(text):
            clear_pending(pending["id"])
            await update.message.reply_text("Отменено.")
            return

    try:
        classified = await classify_message(text)
        msg_type = classified.get("type")
        data = classified.get("data", {})
    except Exception as e:
        logger.error(f"classify error: {e}")
        await update.message.reply_text("Не смог разобрать запрос. Попробуй переформулировать.")
        return

    if msg_type == "body_measurement_text":
        save_pending("body_measurement", data, update.message.message_id)
        await update.message.reply_text(format_measurement(data))

    elif msg_type == "add_product":
        save_pending("product", data, update.message.message_id)
        await update.message.reply_text(format_product(data))

    elif msg_type in ("daily_goals", "workout_update"):
        has_workout = data.get("has_workout", False) if isinstance(data, dict) else False
        measurement = supabase.table("body_measurements").select("bmr").order("date", desc=True).limit(1).execute()
        bmr = 1800
        if measurement.data and measurement.data[0].get("bmr"):
            bmr = measurement.data[0]["bmr"]

        goals = await calculate_daily_goals(bmr, has_workout)
        workout_text = "💪 С тренировкой" if has_workout else "🛋 Без тренировки"
        msg = (
            f"{workout_text}\n\n"
            f"🎯 Цели на сегодня:\n"
            f"🔥 {goals['calories']} ккал\n"
            f"🥩 Белки: {goals['protein']} г\n"
            f"🧈 Жиры: {goals['fat']} г\n"
            f"🌾 Углеводы: {goals['carbs']} г\n\n"
            f"Подтверждаешь?"
        )
        save_pending("daily_goals", goals, update.message.message_id)
        await update.message.reply_text(msg)

    elif msg_type == "food_log":
        items = data if isinstance(data, list) else data.get("items", data if isinstance(data, list) else [])
        if not items:
            await update.message.reply_text("Не понял что ты съел. Напиши например: «съел 200г гречки и 150г куриной грудки»")
            return

        await update.message.reply_text("⏳ Считаю КБЖУ...")

        total_cal = total_p = total_f = total_c = 0
        log_items = []

        for item in items:
            product_name = item.get("name", "")
            grams = float(item.get("grams", 100))

            # Try to find in DB first
            product = supabase.table("products").select("*").ilike("name", f"%{product_name}%").limit(1).execute()

            if product.data:
                p = product.data[0]
                ratio = grams / 100
                cal = round(p["calories"] * ratio, 1)
                prot = round(p["protein"] * ratio, 1)
                fat_v = round(p["fat"] * ratio, 1)
                carb = round(p["carbs"] * ratio, 1)
                log_items.append({
                    "product_name": product_name,
                    "grams": grams,
                    "calories": cal,
                    "protein": prot,
                    "fat": fat_v,
                    "carbs": carb,
                    "product_id": p["id"],
                    "auto_estimated": False,
                })
            else:
                # Estimate via GPT
                try:
                    macros = await estimate_macros(product_name, grams)
                    log_items.append({
                        "product_name": product_name,
                        "grams": grams,
                        "calories": macros["calories"],
                        "protein": macros["protein"],
                        "fat": macros["fat"],
                        "carbs": macros["carbs"],
                        "cal100": macros["cal100"],
                        "pro100": macros["pro100"],
                        "fat100": macros["fat100"],
                        "carb100": macros["carb100"],
                        "auto_estimated": True,
                    })
                except Exception as e:
                    logger.error(f"estimate_macros error: {e}")
                    log_items.append({
                        "product_name": product_name,
                        "grams": grams,
                        "calories": 0,
                        "protein": 0,
                        "fat": 0,
                        "carbs": 0,
                        "auto_estimated": False,
                    })

            total_cal += log_items[-1].get("calories") or 0
            total_p += log_items[-1].get("protein") or 0
            total_f += log_items[-1].get("fat") or 0
            total_c += log_items[-1].get("carbs") or 0

        lines = ["📝 Записать как съеденное?\n"]
        for item in log_items:
            est = " (~оценка ИИ)" if item.get("auto_estimated") else ""
            lines.append(f"• {item['product_name']}: {item['grams']} г — {round(item.get('calories') or 0)} ккал{est}")

        lines.append(f"\n📊 Итого: {round(total_cal)} ккал")
        lines.append(f"🥩 Б: {round(total_p)} г  🧈 Ж: {round(total_f)} г  🌾 У: {round(total_c)} г")
        lines.append("\nПодтверждаешь?")

        save_pending("food_log", {"items": log_items}, update.message.message_id)
        await update.message.reply_text("\n".join(lines))

    elif msg_type == "query_today":
        stats = get_today_stats()
        goals = stats.get("goals")

        if not stats["items"]:
            await update.message.reply_text("Сегодня ещё ничего не записано 🍽")
            return

        lines = [f"📅 Сегодня съел:\n"]
        lines.extend(stats["items"])
        lines.append(f"\n📊 Итого:")
        lines.append(f"🔥 {stats['total_cal']} ккал")
        lines.append(f"🥩 Белки: {stats['total_p']} г")
        lines.append(f"🧈 Жиры: {stats['total_f']} г")
        lines.append(f"🌾 Углеводы: {stats['total_c']} г")

        if goals:
            remaining_cal = goals["calories"] - stats["total_cal"]
            lines.append(f"\n🎯 Осталось до цели: {remaining_cal} ккал")

        await update.message.reply_text("\n".join(lines))

    elif msg_type == "query_weight":
        history = get_weight_history()
        if not history:
            await update.message.reply_text("Нет данных о замерах. Отправь скриншот умных весов!")
            return

        lines = ["📊 Последние замеры:\n"]
        for row in history:
            fat = f", жир {row['fat_percent']}%" if row.get("fat_percent") else ""
            muscle = f", мышцы {row['muscle_percent']}%" if row.get("muscle_percent") else ""
            lines.append(f"• {row['date']}: {row['weight']} кг{fat}{muscle}")

        if len(history) >= 2:
            first = history[-1]["weight"]
            last = history[0]["weight"]
            diff = round(last - first, 1)
            sign = "+" if diff > 0 else ""
            lines.append(f"\nДинамика: {sign}{diff} кг за {len(history)} замеров")

        await update.message.reply_text("\n".join(lines))

    elif msg_type == "general_chat":
        # Answer as a health/nutrition assistant
        stats = get_today_stats()
        context_info = f"Сегодня пользователь съел: {stats['total_cal']} ккал, белки {stats['total_p']}г, жиры {stats['total_f']}г, углеводы {stats['total_c']}г."

        system = f"""Ты персональный ассистент по здоровью, питанию и телесной рекомпозиции (снижение жира + сохранение мышц).
Отвечай кратко, по делу, на русском языке. Ты знаешь данные пользователя:
{context_info}
Давай практичные советы. Будь поддерживающим и мотивирующим."""

        reply = await ask_ai(system, text, model="gpt-4o-mini")
        await update.message.reply_text(reply)

    else:
        # Try to answer as assistant anyway
        system = """Ты персональный ассистент по здоровью и питанию. Отвечай кратко на русском.
Если не понял запрос, скажи что умеешь:
- Запись еды: напиши что съел и граммы
- Замер тела: отправь скриншот умных весов
- Цели дня: напиши «есть тренировка» или «без тренировки»
- Статистика: напиши «что я съел сегодня»
- Добавить продукт: отправь фото этикетки"""
        reply = await ask_ai(system, text, model="gpt-4o-mini")
        await update.message.reply_text(reply)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    image_bytes = bytes(await file.download_as_bytearray())
    await update.message.reply_text("🔍 Анализирую изображение...")

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

    await update.message.reply_text("Не смог распознать. Отправь скриншот умных весов или фото этикетки продукта.")


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

    await update.message.reply_text(f"🎙 «{text}»")
    await process_text_message(update, context, text)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    await process_text_message(update, context, text)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я твой персональный ассистент.\n\n"
        "🍽 Питание:\n"
        "• Напиши что съел → запишу с КБЖУ\n"
        "• Фото этикетки → добавлю продукт\n"
        "• «Что я съел сегодня?» → покажу статистику\n\n"
        "💪 Здоровье:\n"
        "• Скриншот умных весов → сохраню замер\n"
        "• «Есть тренировка» → посчитаю цели дня\n\n"
        "🎙 Понимаю голосовые сообщения\n\n"
        "Что сделаем первым?"
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
