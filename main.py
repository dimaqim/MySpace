import os
import json
import base64
import logging
import tempfile
import pytz
from datetime import date, datetime, timedelta
from dotenv import load_dotenv

from telegram import Update, Bot
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler

from groq import AsyncGroq
import google.generativeai as genai
from tavily import TavilyClient
from openai import AsyncOpenAI
from supabase import create_client, Client

from apscheduler.schedulers.asyncio import AsyncIOScheduler

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Clients ───────────────────────────────────────────────────────────
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN")
MY_CHAT_ID      = os.getenv("MY_CHAT_ID")          # твой Telegram chat_id для сводки
TIMEZONE        = os.getenv("TIMEZONE", "Europe/Kiev")

groq_client    = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
gemini         = genai.GenerativeModel("gemini-2.5-flash-lite-preview-06-17")
tavily         = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
openai_client  = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
supabase: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

TZ = pytz.timezone(TIMEZONE)

CONFIRM_WORDS = ["да", "подтверждаю", "сохраняй", "ок", "окей", "yes", "верно", "точно", "давай", "сохрани", "конечно"]
DENY_WORDS    = ["нет", "неверно", "не то", "отмена", "cancel", "no", "стоп", "не надо", "отмени"]

# ── Helpers ───────────────────────────────────────────────────────────

def today_str():
    return date.today().isoformat()

def now_local():
    return datetime.now(TZ)

def is_confirm(text: str) -> bool:
    t = text.lower().strip()
    return any(t == w or t.startswith(w + " ") for w in CONFIRM_WORDS)

def is_deny(text: str) -> bool:
    t = text.lower().strip()
    return any(t == w or t.startswith(w + " ") for w in DENY_WORDS)

def get_pending():
    r = supabase.table("pending_actions").select("*").eq("status", "pending").order("created_at", desc=True).limit(1).execute()
    return r.data[0] if r.data else None

def clear_pending(pid: str):
    supabase.table("pending_actions").update({"status": "done"}).eq("id", pid).execute()

def save_pending(action_type: str, data: dict, msg_id=None):
    supabase.table("pending_actions").update({"status": "expired"}).eq("status", "pending").execute()
    supabase.table("pending_actions").insert({
        "type": action_type, "data": data, "status": "pending",
        "telegram_message_id": str(msg_id) if msg_id else None
    }).execute()

def parse_json(text: str) -> dict:
    text = text.strip()
    if "```" in text:
        parts = text.split("```")
        text = parts[1] if len(parts) >= 2 else text
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())

# ── AI calls ─────────────────────────────────────────────────────────

async def transcribe_voice(file_path: str) -> str:
    """Groq Whisper — дёшево и быстро"""
    with open(file_path, "rb") as f:
        r = await groq_client.audio.transcriptions.create(
            model="whisper-large-v3-turbo", file=f, language="ru"
        )
    return r.text

async def gemini_text(prompt: str) -> str:
    """Gemini Flash-Lite — основная модель для текста"""
    try:
        r = await gemini.generate_content_async(prompt)
        return r.text
    except Exception as e:
        logger.warning(f"Gemini error, fallback to OpenAI: {e}")
        return await openai_fallback(prompt)

async def gemini_image(image_bytes: bytes, prompt: str) -> str:
    """Gemini Flash-Lite — анализ изображений"""
    try:
        import PIL.Image
        import io
        img = PIL.Image.open(io.BytesIO(image_bytes))
        r = await gemini.generate_content_async([prompt, img])
        return r.text
    except Exception as e:
        logger.warning(f"Gemini vision error, fallback to OpenAI: {e}")
        b64 = base64.b64encode(image_bytes).decode()
        r = await openai_client.chat.completions.create(
            model="gpt-4o", timeout=30,
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                {"type": "text", "text": prompt}
            ]}], max_tokens=1000
        )
        return r.choices[0].message.content

async def openai_fallback(prompt: str) -> str:
    """OpenAI — только когда Gemini не справился"""
    r = await openai_client.chat.completions.create(
        model="gpt-4o-mini", timeout=30,
        messages=[{"role": "user", "content": prompt}], max_tokens=1000
    )
    return r.choices[0].message.content

async def search_product_nutrition(product_name: str) -> dict | None:
    """Tavily — поиск КБЖУ в интернете"""
    try:
        result = tavily.search(
            query=f"{product_name} калорийность КБЖУ на 100 грамм белки жиры углеводы",
            search_depth="basic", max_results=3
        )
        content = "\n".join([r.get("content", "") for r in result.get("results", [])])
        if not content:
            return None
        prompt = f"""Из этого текста извлеки КБЖУ продукта "{product_name}" на 100г.
Верни ТОЛЬКО JSON:
{{"calories": число, "protein": число, "fat": число, "carbs": число}}
Если данных нет — верни null.

Текст:
{content[:2000]}"""
        raw = await gemini_text(prompt)
        if "null" in raw.lower():
            return None
        return parse_json(raw)
    except Exception as e:
        logger.error(f"Tavily search error: {e}")
        return None

async def estimate_nutrition_gpt(product_name: str) -> dict:
    """Fallback: GPT оценивает КБЖУ если интернет не помог"""
    prompt = f"""Ты эксперт по питанию. Укажи среднее КБЖУ для "{product_name}" на 100г.
Верни ТОЛЬКО JSON: {{"calories": число, "protein": число, "fat": число, "carbs": число}}"""
    raw = await gemini_text(prompt)
    return parse_json(raw)

# ── Классификатор ─────────────────────────────────────────────────────

async def classify(text: str) -> dict:
    now = now_local()
    prompt = f"""Ты классификатор персонального ассистента. Сегодня: {now.strftime('%A %d %B %Y %H:%M')} (timezone: {TIMEZONE}).
Определи тип сообщения и верни ТОЛЬКО JSON.

Типы и формат data:
- "food_log": [{{"name":"...", "grams": число}}]
- "add_product": {{"name":"...", "brand":null, "calories":..., "protein":..., "fat":..., "carbs":...}}
- "body_measurement": {{"weight":..., "fat_percent":..., "muscle_percent":..., ...}}
- "workout": {{"duration_minutes":..., "location":"...", "exercises":["..."], "notes":"...", "calories_burned":...}}
- "expense": {{"amount":..., "currency":"RUB", "category":"...", "description":"...", "store_name":null}}
- "income": {{"amount":..., "currency":"RUB", "category":"...", "description":"..."}}
- "reminder": {{"text":"...", "remind_at":"ISO datetime", "smart_offset_minutes": число или null}}
  ВАЖНО для remind_at: если не указано время — ставь 12:00 местного времени. Если событие в 21:00 и нет уточнения — напомни за 10 минут (20:50). Если сказано "через неделю" — от сегодня +7 дней.
- "daily_goals": {{"has_workout": true/false}}
- "habit_log": {{"habit_name":"...", "done": true/false, "note":null}}
- "task": {{"title":"...", "due_date":null, "priority":"normal"}}
- "goal": {{"title":"...", "category":"...", "target_date":null}}
- "query_today": {{}}
- "query_finances": {{"period":"today/week/month"}}
- "query_weight": {{}}
- "general_chat": {{"message":"..."}}

Сообщение пользователя: "{text}"

Верни JSON: {{"type":"...", "data":...}}"""

    raw = await gemini_text(prompt)
    return parse_json(raw)

# ── Форматирование подтверждений ──────────────────────────────────────

def fmt_food(items: list) -> str:
    lines = ["📝 Записать приём пищи?\n"]
    for it in items:
        cal = it.get('calories')
        est = " *(оценка ИИ)*" if it.get('auto_estimated') else ""
        lines.append(f"• {it['product_name']}: {it['grams']} г — {round(cal or 0)} ккал{est}")
    total_cal = sum(it.get('calories') or 0 for it in items)
    total_p   = sum(it.get('protein') or 0 for it in items)
    total_f   = sum(it.get('fat') or 0 for it in items)
    total_c   = sum(it.get('carbs') or 0 for it in items)
    lines.append(f"\n📊 {round(total_cal)} ккал | Б {round(total_p)}г | Ж {round(total_f)}г | У {round(total_c)}г")
    lines.append("\nПодтверждаешь?")
    return "\n".join(lines)

def fmt_measurement(d: dict) -> str:
    lines = ["⚖️ Записать замер тела?\n"]
    if d.get("weight"):        lines.append(f"Вес: {d['weight']} кг")
    if d.get("bmi"):           lines.append(f"ИМТ: {d['bmi']}")
    if d.get("fat_percent"):   lines.append(f"Жир: {d['fat_percent']}%")
    if d.get("muscle_percent"):lines.append(f"Мышцы: {d['muscle_percent']}%")
    if d.get("water_percent"): lines.append(f"Вода: {d['water_percent']}%")
    if d.get("visceral_fat"):  lines.append(f"Висцеральный жир: {d['visceral_fat']}")
    if d.get("bmr"):           lines.append(f"Обмен: {d['bmr']} ккал")
    lines.append("\nПодтверждаешь?")
    return "\n".join(lines)

def fmt_expense(d: dict) -> str:
    store = f" в «{d['store_name']}»" if d.get("store_name") else ""
    cat   = f" ({d['category']})" if d.get("category") else ""
    return (f"💸 Записать расход?\n\n"
            f"{d['amount']} {d.get('currency','RUB')}{store}{cat}\n"
            f"{d.get('description','')}\n\nПодтверждаешь?")

def fmt_income(d: dict) -> str:
    return (f"💰 Записать доход?\n\n"
            f"+{d['amount']} {d.get('currency','RUB')}\n"
            f"{d.get('description','')}\n\nПодтверждаешь?")

def fmt_workout(d: dict) -> str:
    dur = f"{d['duration_minutes']} мин" if d.get("duration_minutes") else ""
    ex  = ", ".join(d.get("exercises") or [])
    return (f"💪 Записать тренировку?\n\n"
            f"{dur}\n{ex}\n{d.get('notes','')}\n\nПодтверждаешь?")

def fmt_reminder(d: dict) -> str:
    dt = datetime.fromisoformat(d["remind_at"])
    if dt.tzinfo is None:
        dt = TZ.localize(dt)
    dt_local = dt.astimezone(TZ)
    return (f"⏰ Поставить напоминание?\n\n"
            f"«{d['text']}»\n\n"
            f"Напомню: {dt_local.strftime('%d.%m.%Y в %H:%M')}\n\nПодтверждаешь?")

# ── Сохранение ────────────────────────────────────────────────────────

async def save_action(pending: dict) -> str:
    t    = pending["type"]
    data = pending["data"]
    td   = today_str()

    if t == "food_log":
        for it in data.get("items", []):
            row = {k: v for k, v in it.items()
                   if k in ("product_name","grams","calories","protein","fat","carbs","product_id","meal_number","note")}
            row["date"] = td
            supabase.table("food_log").insert(row).execute()
            # автосохраняем продукт если его не было в базе
            if it.get("auto_estimated") and it.get("product_name"):
                ex = supabase.table("products").select("id").ilike("name", f"%{it['product_name']}%").limit(1).execute()
                if not ex.data:
                    supabase.table("products").insert({
                        "name": it["product_name"],
                        "calories": it.get("cal100"), "protein": it.get("pro100"),
                        "fat": it.get("fat100"), "carbs": it.get("carb100"),
                    }).execute()
        return "✅ Еда записана в дневник!"

    if t == "body_measurement":
        data["date"] = td
        supabase.table("body_measurements").upsert(data, on_conflict="date").execute()
        return "✅ Замер сохранён!"

    if t == "expense":
        if data.get("store_name"):
            ex = supabase.table("stores").select("id").ilike("name", data["store_name"]).limit(1).execute()
            if ex.data:
                data["store_id"] = ex.data[0]["id"]
            else:
                new_store = supabase.table("stores").insert({"name": data["store_name"]}).execute()
                if new_store.data:
                    data["store_id"] = new_store.data[0]["id"]
        data["date"] = td
        data["type"] = "expense"
        supabase.table("finances").insert(data).execute()
        return f"✅ Расход записан: {data['amount']} {data.get('currency','RUB')}"

    if t == "income":
        data["date"] = td
        data["type"] = "income"
        supabase.table("finances").insert(data).execute()
        return f"✅ Доход записан: +{data['amount']} {data.get('currency','RUB')}"

    if t == "workout":
        data["date"] = td
        supabase.table("workouts").insert(data).execute()
        return "✅ Тренировка записана!"

    if t == "reminder":
        supabase.table("reminders").insert({
            "text": data["text"],
            "remind_at": data["remind_at"],
            "is_sent": False
        }).execute()
        return "✅ Напоминание поставлено!"

    if t == "daily_goals":
        bm = supabase.table("body_measurements").select("bmr").order("date", desc=True).limit(1).execute()
        bmr = (bm.data[0]["bmr"] if bm.data and bm.data[0].get("bmr") else 1800)
        has_w = data.get("has_workout", False)
        cal   = int(bmr * 1.4 if has_w else bmr * 1.2)
        goals = {"date": td, "calories": cal,
                 "protein": int(cal*0.30/4), "fat": int(cal*0.25/9), "carbs": int(cal*0.45/4),
                 "has_workout": has_w, "bmr_used": bmr}
        supabase.table("daily_goals").upsert(goals, on_conflict="date").execute()
        return f"✅ Цели дня: {cal} ккал ({'с тренировкой' if has_w else 'без тренировки'})"

    if t == "habit_log":
        supabase.table("habit_logs").insert({
            "habit_name": data["habit_name"], "date": td,
            "done": data.get("done", True), "note": data.get("note")
        }).execute()
        status = "✅" if data.get("done", True) else "❌"
        return f"{status} Привычка «{data['habit_name']}» отмечена!"

    if t == "task":
        supabase.table("tasks").insert({
            "title": data["title"], "due_date": data.get("due_date"),
            "priority": data.get("priority", "normal")
        }).execute()
        return f"✅ Задача добавлена: «{data['title']}»"

    if t == "goal":
        supabase.table("goals").insert({
            "title": data["title"], "category": data.get("category"),
            "target_date": data.get("target_date")
        }).execute()
        return f"✅ Цель записана: «{data['title']}»"

    return "✅ Сохранено!"

# ── Статистика ────────────────────────────────────────────────────────

def get_today_stats() -> str:
    td = today_str()
    food   = supabase.table("food_log").select("*").eq("date", td).execute().data or []
    goals  = supabase.table("daily_goals").select("*").eq("date", td).execute()
    bodies = supabase.table("body_measurements").select("*").order("date", desc=True).limit(1).execute().data or []
    habits = supabase.table("habit_logs").select("*").eq("date", td).execute().data or []
    tasks  = supabase.table("tasks").select("*").eq("status", "pending").execute().data or []
    fin    = supabase.table("finances").select("*").eq("date", td).execute().data or []

    total_cal = sum(r.get("calories") or 0 for r in food)
    total_p   = sum(r.get("protein")  or 0 for r in food)
    total_f   = sum(r.get("fat")      or 0 for r in food)
    total_c   = sum(r.get("carbs")    or 0 for r in food)

    income  = sum(r["amount"] for r in fin if r["type"] == "income")
    expense = sum(r["amount"] for r in fin if r["type"] == "expense")

    g = goals.data[0] if goals.data else None
    cal_goal = g["calories"] if g else 1856

    lines = [f"📊 *Итог дня — {td}*\n"]

    if food:
        lines.append(f"🍽 *Питание:* {round(total_cal)}/{cal_goal} ккал")
        lines.append(f"   Б {round(total_p)}г | Ж {round(total_f)}г | У {round(total_c)}г")
    else:
        lines.append("🍽 Питание: ничего не записано")

    workouts_today = supabase.table("workouts").select("*").eq("date", td).execute().data or []
    if workouts_today:
        w = workouts_today[0]
        lines.append(f"💪 *Тренировка:* {w.get('duration_minutes', '?')} мин — {w.get('location', '')}")
    else:
        lines.append("💪 Тренировка: не записано")

    if bodies:
        b = bodies[0]
        lines.append(f"⚖️ *Вес:* {b.get('weight')} кг (жир {b.get('fat_percent')}%)")

    if income or expense:
        lines.append(f"💰 *Финансы:* +{income} / -{expense} {fin[0].get('currency','RUB') if fin else 'RUB'}")

    if habits:
        done_h = [h for h in habits if h["done"]]
        lines.append(f"✅ *Привычки:* {len(done_h)}/{len(habits)}")

    if tasks:
        lines.append(f"📝 *Задачи в работе:* {len(tasks)}")

    return "\n".join(lines)

def get_finance_stats(period: str) -> str:
    td = date.today()
    if period == "week":
        from_date = (td - timedelta(days=td.weekday())).isoformat()
        label = "эту неделю"
    elif period == "month":
        from_date = td.replace(day=1).isoformat()
        label = "этот месяц"
    else:
        from_date = td.isoformat()
        label = "сегодня"

    fin = supabase.table("finances").select("*").gte("date", from_date).execute().data or []

    income  = sum(r["amount"] for r in fin if r["type"] == "income")
    expense = sum(r["amount"] for r in fin if r["type"] == "expense")

    # По магазинам
    stores: dict = {}
    for r in fin:
        if r["type"] == "expense" and r.get("store_name"):
            stores[r["store_name"]] = stores.get(r["store_name"], 0) + r["amount"]

    # По категориям
    cats: dict = {}
    for r in fin:
        if r["type"] == "expense" and r.get("category"):
            cats[r["category"]] = cats.get(r["category"], 0) + r["amount"]

    lines = [f"💰 *Финансы за {label}*\n",
             f"Доходы: +{income}",
             f"Расходы: -{expense}",
             f"Баланс: {income - expense:+.0f}\n"]

    if stores:
        lines.append("🏪 *По магазинам:*")
        for name, amt in sorted(stores.items(), key=lambda x: -x[1]):
            lines.append(f"  • {name}: {amt}")

    if cats:
        lines.append("\n📂 *По категориям:*")
        for cat, amt in sorted(cats.items(), key=lambda x: -x[1]):
            lines.append(f"  • {cat}: {amt}")

    return "\n".join(lines)

# ── Обработка еды ─────────────────────────────────────────────────────

async def process_food_items(raw_items: list) -> list:
    log_items = []
    for item in raw_items:
        name  = item.get("name", "")
        grams = float(item.get("grams", 100))

        # 1. Ищем в нашей базе
        found = supabase.table("products").select("*").ilike("name", f"%{name}%").limit(1).execute()
        if found.data:
            p = found.data[0]; ratio = grams / 100
            log_items.append({
                "product_name": name, "grams": grams,
                "calories": round(p["calories"] * ratio, 1),
                "protein":  round(p["protein"]  * ratio, 1),
                "fat":      round(p["fat"]       * ratio, 1),
                "carbs":    round(p["carbs"]     * ratio, 1),
                "product_id": p["id"], "auto_estimated": False,
            })
            continue

        # 2. Ищем в интернете через Tavily
        macros = await search_product_nutrition(name)

        # 3. Fallback — GPT оценивает
        if not macros:
            macros = await estimate_nutrition_gpt(name)

        ratio = grams / 100
        log_items.append({
            "product_name": name, "grams": grams,
            "calories": round(macros["calories"] * ratio, 1),
            "protein":  round(macros["protein"]  * ratio, 1),
            "fat":      round(macros["fat"]       * ratio, 1),
            "carbs":    round(macros["carbs"]     * ratio, 1),
            "cal100": macros["calories"], "pro100": macros["protein"],
            "fat100": macros["fat"],      "carb100": macros["carbs"],
            "auto_estimated": True,
        })
    return log_items

# ── Обработка изображений ─────────────────────────────────────────────

async def analyze_scale_image(image_bytes: bytes) -> dict:
    prompt = """На этом изображении — скриншот приложения умных весов. Извлеки все числовые показатели.
Верни ТОЛЬКО JSON:
{"weight":null,"bmi":null,"fat_percent":null,"muscle_percent":null,"water_percent":null,"visceral_fat":null,"bmr":null,"fat_mass":null,"lean_mass":null,"bone_mass":null}"""
    raw = await gemini_image(image_bytes, prompt)
    return parse_json(raw)

async def analyze_label_image(image_bytes: bytes) -> dict:
    prompt = """На этом изображении — этикетка продукта. Извлеки название, бренд и КБЖУ на 100г.
Верни ТОЛЬКО JSON:
{"name":"...","brand":null,"calories":0,"protein":0,"fat":0,"carbs":0}"""
    raw = await gemini_image(image_bytes, prompt)
    return parse_json(raw)

# ── Основной обработчик текста ────────────────────────────────────────

async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    pending = get_pending()

    # Проверяем подтверждение
    if pending:
        if is_confirm(text):
            clear_pending(pending["id"])
            msg = await save_action(pending)
            await update.message.reply_text(msg)
            return
        elif is_deny(text):
            clear_pending(pending["id"])
            await update.message.reply_text("Отменено.")
            return

    # Классифицируем
    try:
        classified = await classify(text)
        msg_type = classified.get("type")
        data     = classified.get("data", {})
    except Exception as e:
        logger.error(f"classify error: {e}")
        await update.message.reply_text("Не смог понять запрос, попробуй переформулировать.")
        return

    # ── food_log ──
    if msg_type == "food_log":
        raw = data if isinstance(data, list) else data.get("items", [])
        if not raw:
            await update.message.reply_text("Не понял что ты съел. Напиши например: «съел 200г гречки и куриную грудку 150г»")
            return
        await update.message.reply_text("⏳ Ищу КБЖУ...")
        items = await process_food_items(raw)
        save_pending("food_log", {"items": items}, update.message.message_id)
        await update.message.reply_text(fmt_food(items), parse_mode="Markdown")

    # ── body_measurement ──
    elif msg_type == "body_measurement":
        save_pending("body_measurement", data, update.message.message_id)
        await update.message.reply_text(fmt_measurement(data))

    # ── expense ──
    elif msg_type == "expense":
        save_pending("expense", data, update.message.message_id)
        await update.message.reply_text(fmt_expense(data))

    # ── income ──
    elif msg_type == "income":
        save_pending("income", data, update.message.message_id)
        await update.message.reply_text(fmt_income(data))

    # ── workout ──
    elif msg_type == "workout":
        save_pending("workout", data, update.message.message_id)
        await update.message.reply_text(fmt_workout(data))

    # ── reminder ──
    elif msg_type == "reminder":
        save_pending("reminder", data, update.message.message_id)
        await update.message.reply_text(fmt_reminder(data))

    # ── daily_goals ──
    elif msg_type == "daily_goals":
        save_pending("daily_goals", data, update.message.message_id)
        hw = data.get("has_workout", False)
        await update.message.reply_text(
            f"Посчитать цели на сегодня {'с тренировкой' if hw else 'без тренировки'}?\n\nПодтверждаешь?"
        )

    # ── habit_log ──
    elif msg_type == "habit_log":
        save_pending("habit_log", data, update.message.message_id)
        status = "✅" if data.get("done", True) else "❌"
        await update.message.reply_text(f"{status} Отметить привычку «{data['habit_name']}»?\n\nПодтверждаешь?")

    # ── task ──
    elif msg_type == "task":
        save_pending("task", data, update.message.message_id)
        due = f" (до {data['due_date']})" if data.get("due_date") else ""
        await update.message.reply_text(f"📝 Добавить задачу?\n\n«{data['title']}»{due}\n\nПодтверждаешь?")

    # ── goal ──
    elif msg_type == "goal":
        save_pending("goal", data, update.message.message_id)
        await update.message.reply_text(f"🎯 Записать цель?\n\n«{data['title']}»\n\nПодтверждаешь?")

    # ── query_today ──
    elif msg_type == "query_today":
        await update.message.reply_text(get_today_stats(), parse_mode="Markdown")

    # ── query_finances ──
    elif msg_type == "query_finances":
        period = data.get("period", "month") if isinstance(data, dict) else "month"
        await update.message.reply_text(get_finance_stats(period), parse_mode="Markdown")

    # ── query_weight ──
    elif msg_type == "query_weight":
        rows = supabase.table("body_measurements").select("date,weight,fat_percent,muscle_percent").order("date", desc=True).limit(10).execute().data or []
        if not rows:
            await update.message.reply_text("Нет замеров. Отправь скриншот умных весов!")
            return
        lines = ["📊 *Динамика веса:*\n"]
        for r in rows:
            fat = f", жир {r['fat_percent']}%" if r.get("fat_percent") else ""
            lines.append(f"• {r['date']}: {r['weight']} кг{fat}")
        if len(rows) >= 2:
            diff = round(rows[0]["weight"] - rows[-1]["weight"], 1)
            lines.append(f"\nИзменение: {diff:+} кг за {len(rows)} замеров")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    # ── general_chat ──
    else:
        stats = get_today_stats()
        prompt = f"""Ты персональный ИИ-ассистент. Отвечай кратко и по делу, на русском.
Данные пользователя за сегодня:
{stats}

Сообщение: {text}"""
        reply = await gemini_text(prompt)
        await update.message.reply_text(reply)

# ── Handlers ─────────────────────────────────────────────────────────

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = await context.bot.get_file(update.message.voice.file_id)
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        await file.download_to_drive(tmp_path)
        text = await transcribe_voice(tmp_path)
    finally:
        os.unlink(tmp_path)
    await update.message.reply_text(f"🎙 «{text}»")
    await process_message(update, context, text)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1]
    file  = await context.bot.get_file(photo.file_id)
    image_bytes = bytes(await file.download_as_bytearray())
    await update.message.reply_text("🔍 Анализирую...")

    # Пробуем как весы
    try:
        d = await analyze_scale_image(image_bytes)
        if d.get("weight"):
            save_pending("body_measurement", d, update.message.message_id)
            await update.message.reply_text(fmt_measurement(d))
            return
    except Exception as e:
        logger.error(f"scale image: {e}")

    # Пробуем как этикетку
    try:
        d = await analyze_label_image(image_bytes)
        if d.get("name") and d.get("calories"):
            save_pending("add_product", d, update.message.message_id)
            brand = f" ({d['brand']})" if d.get("brand") else ""
            await update.message.reply_text(
                f"📦 Нашёл продукт: {d['name']}{brand}\n"
                f"на 100г: {d['calories']} ккал | Б {d['protein']}г | Ж {d['fat']}г | У {d['carbs']}г\n\n"
                f"Добавить в базу продуктов?\nПодтверждаешь?"
            )
            return
    except Exception as e:
        logger.error(f"label image: {e}")

    await update.message.reply_text("Не смог распознать. Отправь скриншот весов или фото этикетки.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await process_message(update, context, update.message.text.strip())

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я твой личный ассистент.\n\n"
        "Просто говори или пиши что происходит:\n\n"
        "🍽 *Питание* — «съел сникерс» / «200г гречки и курица»\n"
        "⚖️ *Замеры* — отправь скриншот умных весов\n"
        "💪 *Тренировка* — «был в зале 1.5 часа»\n"
        "💰 *Финансы* — «потратил 500 в Ашане» / «получил зарплату 80к»\n"
        "⏰ *Напоминания* — «напомни в среду в 9 поздравить брата»\n"
        "✅ *Задачи* — «добавь задачу купить корм коту»\n"
        "📊 *Статистика* — «что я съел сегодня» / «траты за неделю»\n\n"
        "Каждый вечер в 21:00 буду присылать сводку дня 📋\n\n"
        "Понимаю голосовые сообщения 🎙",
        parse_mode="Markdown"
    )

# ── Планировщик ───────────────────────────────────────────────────────

async def check_reminders(bot: Bot):
    """Проверяет напоминания каждую минуту"""
    now = now_local()
    rows = supabase.table("reminders").select("*").eq("is_sent", False).lte("remind_at", now.isoformat()).execute().data or []
    for r in rows:
        try:
            if MY_CHAT_ID:
                await bot.send_message(chat_id=MY_CHAT_ID, text=f"⏰ *Напоминание:*\n\n{r['text']}", parse_mode="Markdown")
            supabase.table("reminders").update({"is_sent": True}).eq("id", r["id"]).execute()
        except Exception as e:
            logger.error(f"reminder send error: {e}")

async def send_daily_summary(bot: Bot):
    """Вечерняя сводка в 21:00"""
    if not MY_CHAT_ID:
        return
    try:
        summary = get_today_stats()
        await bot.send_message(chat_id=MY_CHAT_ID, text=f"🌙 *Вечерняя сводка*\n\n{summary}", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"daily summary error: {e}")

# ── Main ─────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Планировщик
    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(check_reminders, "interval", minutes=1, args=[app.bot])
    scheduler.add_job(send_daily_summary, "cron", hour=21, minute=0, args=[app.bot])
    scheduler.start()

    logger.info("Bot started with scheduler")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
