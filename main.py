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
from tavily import TavilyClient
from openai import AsyncOpenAI
from supabase import create_client, Client
from apscheduler.schedulers.asyncio import AsyncIOScheduler

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Clients ───────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MY_CHAT_ID     = os.getenv("MY_CHAT_ID")
TIMEZONE       = os.getenv("TIMEZONE", "Europe/Kiev")

groq_client   = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
tavily        = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
ai            = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
supabase: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

TZ = pytz.timezone(TIMEZONE)

CONFIRM_WORDS = ["да", "подтверждаю", "сохраняй", "ок", "окей", "yes", "верно", "точно", "давай", "сохрани", "конечно", "го", "ага"]
DENY_WORDS    = ["нет", "неверно", "не то", "отмена", "cancel", "no", "стоп", "не надо", "отмени"]

CURRENCY_MAP = {
    "гривен": "UAH", "гривна": "UAH", "грн": "UAH", "грн.": "UAH", "₴": "UAH",
    "рублей": "RUB", "рубль": "RUB", "рублей": "RUB", "руб": "RUB", "₽": "RUB",
    "долларов": "USD", "доллар": "USD", "баксов": "USD", "$": "USD",
    "евро": "EUR", "€": "EUR",
}

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
        if text.lower().startswith("json"):
            text = text[4:]
    return json.loads(text.strip())

# ── AI calls ─────────────────────────────────────────────────────────

async def transcribe_voice(file_path: str) -> str:
    """Groq Whisper — дёшево ($0.04/час)"""
    with open(file_path, "rb") as f:
        r = await groq_client.audio.transcriptions.create(
            model="whisper-large-v3-turbo", file=f, language="ru"
        )
    return r.text

async def gpt(system: str, user: str, model: str = "gpt-4o-mini") -> str:
    """gpt-4o-mini основная модель для текста"""
    r = await ai.chat.completions.create(
        model=model, timeout=30,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=1500
    )
    return r.choices[0].message.content

async def gpt_vision(image_bytes: bytes, prompt: str) -> str:
    """gpt-4o для анализа изображений"""
    b64 = base64.b64encode(image_bytes).decode()
    r = await ai.chat.completions.create(
        model="gpt-4o", timeout=40,
        messages=[{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            {"type": "text", "text": prompt}
        ]}], max_tokens=1000
    )
    return r.choices[0].message.content

# ── Поиск КБЖУ ───────────────────────────────────────────────────────

async def get_nutrition(product_name: str, grams: float) -> dict:
    """
    1. Ищем в нашей базе
    2. Ищем через Tavily в интернете
    3. Fallback — GPT оценивает по памяти
    """
    # 1. База продуктов
    found = supabase.table("products").select("*").ilike("name", f"%{product_name}%").limit(1).execute()
    if found.data:
        p = found.data[0]
        ratio = grams / 100
        return {
            "product_name": product_name, "grams": grams,
            "calories": round(p["calories"] * ratio, 1),
            "protein":  round(p["protein"]  * ratio, 1),
            "fat":      round(p["fat"]       * ratio, 1),
            "carbs":    round(p["carbs"]     * ratio, 1),
            "product_id": p["id"], "auto_estimated": False,
        }

    # 2. Tavily — сначала ищем на таблице калорийности (украинский/русский сайт)
    macros = None
    NUTRITION_SITES = [
        "tablycjakalorijnosti.com.ua",
        "calorizator.ru",
        "fatsecret.ru",
    ]
    try:
        # Приоритет: украинская таблица калорийности
        result = tavily.search(
            query=f"{product_name} калорийність КБЖУ білки жири вуглеводи на 100 грам",
            search_depth="basic", max_results=5,
            include_domains=NUTRITION_SITES
        )
        content = " ".join([r.get("content", "") for r in result.get("results", [])])[:3000]

        # Если на спец-сайтах не нашли — обычный поиск
        if not content or len(content) < 50:
            result = tavily.search(
                query=f"{product_name} калорийность белки жиры углеводы на 100 грамм КБЖУ",
                search_depth="basic", max_results=3
            )
            content = " ".join([r.get("content", "") for r in result.get("results", [])])[:3000]

        if content and len(content) > 50:
            raw = await gpt(
                "Ты эксперт по питанию. Из текста извлеки КБЖУ на 100г. Верни ТОЛЬКО JSON без лишнего текста: {\"calories\": число, \"protein\": число, \"fat\": число, \"carbs\": число}. Если данных нет — верни {\"calories\": null}.",
                f"Продукт: {product_name}\n\nТекст: {content}"
            )
            parsed = parse_json(raw)
            if parsed.get("calories"):
                macros = parsed
    except Exception as e:
        logger.warning(f"Tavily error for {product_name}: {e}")

    # 3. GPT по памяти
    if not macros:
        raw = await gpt(
            "Ты эксперт по питанию. Укажи КБЖУ продукта на 100г. Верни ТОЛЬКО JSON: {\"calories\": число, \"protein\": число, \"fat\": число, \"carbs\": число}",
            f"Продукт: {product_name}"
        )
        macros = parse_json(raw)

    ratio = grams / 100
    return {
        "product_name": product_name, "grams": grams,
        "calories": round(macros["calories"] * ratio, 1),
        "protein":  round(macros["protein"]  * ratio, 1),
        "fat":      round(macros["fat"]       * ratio, 1),
        "carbs":    round(macros["carbs"]     * ratio, 1),
        "cal100": macros["calories"], "pro100": macros["protein"],
        "fat100": macros["fat"],      "carb100": macros["carbs"],
        "auto_estimated": True,
    }

# ── Классификатор ─────────────────────────────────────────────────────

async def classify(text: str) -> dict:
    now = now_local()
    # Вычисляем полезные даты
    weekday = now.weekday()  # 0=пн, 6=вс
    days_to_sunday = 6 - weekday
    end_of_week = (now + timedelta(days=days_to_sunday)).strftime("%Y-%m-%d")
    next_monday  = (now + timedelta(days=7 - weekday)).strftime("%Y-%m-%d")
    tomorrow     = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    system = f"""Ты классификатор персонального ассистента. Сегодня: {now.strftime('%A %d %B %Y %H:%M')} (timezone: {TIMEZONE}).

Полезные даты:
- Завтра: {tomorrow}
- Конец этой недели (воскресенье): {end_of_week}
- Начало следующей недели (понедельник): {next_monday}

Определи тип сообщения и верни ТОЛЬКО JSON без лишнего текста.

ТИПЫ:

"food_log" — человек говорит что ел/съел/будет есть/скушал/употребил, ИЛИ просит посчитать калории конкретного продукта с граммами
data: [{{"name":"точное название продукта или бренда", "grams": число или 100 если не указано}}]
Примеры:
"съел сникерс" → [{{"name":"Snickers батончик", "grams":50}}]
"съел 200г гречки и курицу 150г" → [{{"name":"гречка", "grams":200}},{{"name":"куриная грудка", "grams":150}}]
"буду кушать милкивей" → [{{"name":"Milky Way батончик", "grams":50}}]
"я съел курицу 600 грамм, посчитай калории" → [{{"name":"куриная грудка", "grams":600}}]
"сколько калорий в 200г гречки" → [{{"name":"гречка", "grams":200}}]
ВАЖНО: если человек называет конкретный продукт + граммы + просит посчитать — это food_log, НЕ query

"food_clarify" — уточнение граммов к предыдущему запросу еды (просто число или "N грамм/г")
data: {{"grams": число}}
Примеры: "100 грамм", "150г", "съел 80г"

"add_product" — добавить продукт с КБЖУ в базу (пользователь явно называет КБЖУ)
data: {{"name":"...", "brand":null, "calories":..., "protein":..., "fat":..., "carbs":...}}

"body_measurement" — замеры тела текстом
data: {{"weight":null,"bmi":null,"fat_percent":null,"muscle_percent":null,...}}

"workout" — тренировка, физическая активность
data: {{"duration_minutes":число или null, "location":"зал/улица/дома", "exercises":["список"], "notes":"...", "calories_burned":null}}

"expense" — трата денег
data: {{"amount":число, "currency":"RUB/UAH/USD/EUR", "category":"еда/кофе/транспорт/...", "description":"...", "store_name":null или "название магазина"}}
Определяй валюту: гривен/грн/₴=UAH, рублей/руб/₽=RUB, долларов/$=USD, евро/€=EUR

"income" — получил деньги
data: {{"amount":число, "currency":"RUB", "category":"зарплата/фриланс/...", "description":"..."}}

"reminder" — напомни в определённое время
data: {{"text":"что именно напомнить", "remind_at":"YYYY-MM-DDTHH:MM:SS"}}
Правила времени:
- Если время не указано → 12:00 текущего дня или указанной даты
- "в среду" → ближайшая среда
- "через неделю" → {(now + timedelta(days=7)).strftime('%Y-%m-%d')}T12:00:00
- "конец недели" → {end_of_week}T12:00:00
- "в 21:00 будет футбол" без времени напоминания → {now.strftime('%Y-%m-%d')}T20:50:00 (за 10 минут)
- "напомни в 20:50 что в 21:00 футбол" → {now.strftime('%Y-%m-%d')}T20:50:00

"daily_goals" — посчитать цели питания на день
data: {{"has_workout": true/false}}

"habit_log" — отметить привычку
data: {{"habit_name":"...", "done":true/false, "note":null}}

"task" — добавить задачу
data: {{"title":"...", "due_date":null, "priority":"normal/high/low"}}

"goal" — записать долгосрочную цель
data: {{"title":"...", "category":"здоровье/финансы/...", "target_date":null}}

"query_today" — общая сводка дня ("что сегодня делал", "итог дня", "как я сегодня")
"query_food" — ТОЛЬКО вопросы о еде ("что съел", "что я ел", "что я уже съел", "сколько калорий съел", "что я кушал")
"query_workout" — вопрос о тренировках (сколько бегал, был ли в зале)
"query_finances" — вопрос о деньгах/тратах
data: {{"period":"today/week/month"}}
"query_weight" — вопрос о весе/замерах
"query_food" — вопрос о питании сегодня
"general_chat" — всё остальное, вопросы, советы

Верни JSON: {{"type":"...", "data":...}}"""

    raw = await gpt(system, text)
    return parse_json(raw)

# ── Форматирование ────────────────────────────────────────────────────

def fmt_food(items: list) -> str:
    lines = ["📝 *Записать приём пищи?*\n"]
    for it in items:
        est = " _(оценка ИИ)_" if it.get("auto_estimated") else ""
        lines.append(f"• {it['product_name']}: {it['grams']} г — {round(it.get('calories') or 0)} ккал{est}")
    total_cal = sum(it.get("calories") or 0 for it in items)
    total_p   = sum(it.get("protein")  or 0 for it in items)
    total_f   = sum(it.get("fat")      or 0 for it in items)
    total_c   = sum(it.get("carbs")    or 0 for it in items)
    lines.append(f"\n📊 {round(total_cal)} ккал | Б {round(total_p)}г | Ж {round(total_f)}г | У {round(total_c)}г")
    lines.append("\nПодтверждаешь?")
    return "\n".join(lines)

def fmt_measurement(d: dict) -> str:
    lines = ["⚖️ *Записать замер тела?*\n"]
    if d.get("weight"):         lines.append(f"Вес: {d['weight']} кг")
    if d.get("bmi"):            lines.append(f"ИМТ: {d['bmi']}")
    if d.get("fat_percent"):    lines.append(f"Жир: {d['fat_percent']}%")
    if d.get("muscle_percent"): lines.append(f"Мышцы: {d['muscle_percent']}%")
    if d.get("water_percent"):  lines.append(f"Вода: {d['water_percent']}%")
    if d.get("visceral_fat"):   lines.append(f"Висцеральный жир: {d['visceral_fat']}")
    if d.get("bmr"):            lines.append(f"Обмен: {d['bmr']} ккал")
    if d.get("fat_mass"):       lines.append(f"Жировая масса: {d['fat_mass']} кг")
    if d.get("lean_mass"):      lines.append(f"Сухая масса: {d['lean_mass']} кг")
    lines.append("\nПодтверждаешь?")
    return "\n".join(lines)

def fmt_expense(d: dict) -> str:
    store = f" в «{d['store_name']}»" if d.get("store_name") else ""
    cat   = f" ({d['category']})" if d.get("category") else ""
    cur   = d.get("currency", "RUB")
    return (f"💸 *Записать расход?*\n\n{d['amount']} {cur}{store}{cat}\n{d.get('description','')}\n\nПодтверждаешь?")

def fmt_income(d: dict) -> str:
    return (f"💰 *Записать доход?*\n\n+{d['amount']} {d.get('currency','RUB')}\n{d.get('description','')}\n\nПодтверждаешь?")

def fmt_workout(d: dict) -> str:
    dur = f"{d['duration_minutes']} мин" if d.get("duration_minutes") else "время не указано"
    loc = f" ({d['location']})" if d.get("location") else ""
    ex  = "\n".join(f"  • {e}" for e in (d.get("exercises") or []))
    parts = [f"💪 *Записать тренировку?*\n", f"Время: {dur}{loc}"]
    if ex: parts.append(ex)
    if d.get("notes"): parts.append(d["notes"])
    parts.append("\nПодтверждаешь?")
    return "\n".join(parts)

def fmt_reminder(d: dict) -> str:
    dt = datetime.fromisoformat(d["remind_at"])
    if dt.tzinfo is None:
        dt = TZ.localize(dt)
    dt_local = dt.astimezone(TZ)
    return (f"⏰ *Поставить напоминание?*\n\n«{d['text']}»\n\n"
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
            if it.get("auto_estimated") and it.get("product_name") and it.get("cal100"):
                ex = supabase.table("products").select("id").ilike("name", f"%{it['product_name']}%").limit(1).execute()
                if not ex.data:
                    supabase.table("products").insert({
                        "name": it["product_name"],
                        "calories": it.get("cal100"), "protein": it.get("pro100"),
                        "fat": it.get("fat100"),      "carbs": it.get("carb100"),
                    }).execute()
        total = round(sum(it.get("calories") or 0 for it in data.get("items", [])))
        return f"✅ Записано! +{total} ккал в дневнике."

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
                ns = supabase.table("stores").insert({"name": data["store_name"]}).execute()
                if ns.data: data["store_id"] = ns.data[0]["id"]
        data["date"] = td; data["type"] = "expense"
        supabase.table("finances").insert(data).execute()
        return f"✅ Расход: -{data['amount']} {data.get('currency','RUB')}"

    if t == "income":
        data["date"] = td; data["type"] = "income"
        supabase.table("finances").insert(data).execute()
        return f"✅ Доход: +{data['amount']} {data.get('currency','RUB')}"

    if t == "workout":
        data["date"] = td
        supabase.table("workouts").insert(data).execute()
        dur = f"{data.get('duration_minutes')} мин" if data.get("duration_minutes") else ""
        return f"✅ Тренировка записана! {dur}"

    if t == "reminder":
        supabase.table("reminders").insert({"text": data["text"], "remind_at": data["remind_at"], "is_sent": False}).execute()
        dt = datetime.fromisoformat(data["remind_at"])
        if dt.tzinfo is None: dt = TZ.localize(dt)
        return f"✅ Напомню {dt.astimezone(TZ).strftime('%d.%m в %H:%M')}: «{data['text']}»"

    if t == "daily_goals":
        bm  = supabase.table("body_measurements").select("bmr").order("date", desc=True).limit(1).execute()
        bmr = bm.data[0]["bmr"] if bm.data and bm.data[0].get("bmr") else 1800
        has_w = data.get("has_workout", False)
        cal   = int(bmr * 1.4 if has_w else bmr * 1.2)
        goals = {"date": td, "calories": cal,
                 "protein": int(cal*0.30/4), "fat": int(cal*0.25/9), "carbs": int(cal*0.45/4),
                 "has_workout": has_w, "bmr_used": bmr}
        supabase.table("daily_goals").upsert(goals, on_conflict="date").execute()
        return (f"✅ Цели дня {'с тренировкой' if has_w else 'без тренировки'}:\n"
                f"🔥 {cal} ккал | Б {goals['protein']}г | Ж {goals['fat']}г | У {goals['carbs']}г")

    if t == "habit_log":
        supabase.table("habit_logs").insert({
            "habit_name": data["habit_name"], "date": td,
            "done": data.get("done", True), "note": data.get("note")
        }).execute()
        return f"{'✅' if data.get('done', True) else '❌'} Привычка «{data['habit_name']}» отмечена!"

    if t == "task":
        supabase.table("tasks").insert({
            "title": data["title"], "due_date": data.get("due_date"), "priority": data.get("priority", "normal")
        }).execute()
        return f"✅ Задача: «{data['title']}»"

    if t == "goal":
        supabase.table("goals").insert({
            "title": data["title"], "category": data.get("category"), "target_date": data.get("target_date")
        }).execute()
        return f"✅ Цель: «{data['title']}»"

    return "✅ Сохранено!"

# ── Статистика ────────────────────────────────────────────────────────

def get_today_stats() -> str:
    td = today_str()
    food    = supabase.table("food_log").select("*").eq("date", td).execute().data or []
    goals_r = supabase.table("daily_goals").select("*").eq("date", td).execute()
    bodies  = supabase.table("body_measurements").select("*").order("date", desc=True).limit(1).execute().data or []
    habits  = supabase.table("habit_logs").select("*").eq("date", td).execute().data or []
    tasks   = supabase.table("tasks").select("*").eq("status", "pending").execute().data or []
    fin     = supabase.table("finances").select("*").eq("date", td).execute().data or []
    workouts= supabase.table("workouts").select("*").eq("date", td).execute().data or []

    total_cal = sum(r.get("calories") or 0 for r in food)
    total_p   = sum(r.get("protein")  or 0 for r in food)
    total_f   = sum(r.get("fat")      or 0 for r in food)
    total_c   = sum(r.get("carbs")    or 0 for r in food)
    income    = sum(r["amount"] for r in fin if r["type"] == "income")
    expense   = sum(r["amount"] for r in fin if r["type"] == "expense")

    g = goals_r.data[0] if goals_r.data else None
    cal_goal = g["calories"] if g else 1856

    lines = [f"📊 *Итог дня — {td}*\n"]

    if food:
        remaining = cal_goal - round(total_cal)
        lines.append(f"🍽 *Питание:* {round(total_cal)}/{cal_goal} ккал (осталось {remaining})")
        lines.append(f"   Б {round(total_p)}г | Ж {round(total_f)}г | У {round(total_c)}г")
        for r in food:
            lines.append(f"   • {r.get('product_name')}: {r.get('grams')}г — {round(r.get('calories') or 0)} ккал")
    else:
        lines.append("🍽 Питание: ничего не записано")

    if workouts:
        w = workouts[0]
        loc = f" ({w.get('location')})" if w.get("location") else ""
        lines.append(f"💪 *Тренировка:* {w.get('duration_minutes', '?')} мин{loc}")
        if w.get("exercises"):
            lines.append(f"   {', '.join(w['exercises'])}")
    else:
        lines.append("💪 Тренировка: не записана")

    if bodies:
        b = bodies[0]
        lines.append(f"⚖️ *Вес:* {b.get('weight')} кг (жир {b.get('fat_percent')}%)")

    if income or expense:
        cur = fin[0].get("currency", "RUB") if fin else "RUB"
        lines.append(f"💰 *Финансы:* +{income} / -{expense} {cur}")

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

    stores: dict = {}
    for r in fin:
        if r["type"] == "expense" and r.get("store_name"):
            stores[r["store_name"]] = stores.get(r["store_name"], 0) + r["amount"]

    cats: dict = {}
    for r in fin:
        if r["type"] == "expense" and r.get("category"):
            cats[r["category"]] = cats.get(r["category"], 0) + r["amount"]

    cur = fin[0].get("currency", "RUB") if fin else "RUB"
    lines = [f"💰 *Финансы за {label}*\n",
             f"Доходы: +{income} {cur}", f"Расходы: -{expense} {cur}", f"Баланс: {income-expense:+.0f} {cur}\n"]

    if stores:
        lines.append("🏪 *По магазинам:*")
        for name, amt in sorted(stores.items(), key=lambda x: -x[1]):
            lines.append(f"  • {name}: {amt} {cur}")

    if cats:
        lines.append("\n📂 *По категориям:*")
        for cat, amt in sorted(cats.items(), key=lambda x: -x[1]):
            lines.append(f"  • {cat}: {amt} {cur}")

    if not fin:
        lines.append("Пока ничего не записано.")

    return "\n".join(lines)

def get_workout_stats() -> str:
    td = today_str()
    workouts = supabase.table("workouts").select("*").eq("date", td).execute().data or []
    if not workouts:
        return "Сегодня тренировок не записано. Скажи мне что делал — запишу!"
    lines = ["💪 *Тренировки сегодня:*\n"]
    for w in workouts:
        loc = f" ({w.get('location')})" if w.get("location") else ""
        lines.append(f"• {w.get('duration_minutes', '?')} мин{loc}")
        if w.get("exercises"):
            for ex in w["exercises"]:
                lines.append(f"  — {ex}")
        if w.get("notes"):
            lines.append(f"  {w['notes']}")
    return "\n".join(lines)

def get_food_stats_today() -> str:
    td = today_str()
    food = supabase.table("food_log").select("*").eq("date", td).execute().data or []
    goals_r = supabase.table("daily_goals").select("*").eq("date", td).execute()

    if not food:
        return "Сегодня ты ещё ничего не записывал 🍽\n\nПросто скажи что ел — я запишу с КБЖУ."

    g = goals_r.data[0] if goals_r.data else None
    cal_goal = g["calories"] if g else 1856
    total_cal = round(sum(r.get("calories") or 0 for r in food))
    total_p   = round(sum(r.get("protein")  or 0 for r in food))
    total_f   = round(sum(r.get("fat")      or 0 for r in food))
    total_c   = round(sum(r.get("carbs")    or 0 for r in food))
    remaining = cal_goal - total_cal

    lines = ["🍽 *Сегодня съел:*\n"]
    for r in food:
        lines.append(f"• {r.get('product_name')}: {r.get('grams')}г — {round(r.get('calories') or 0)} ккал")
    lines.append(f"\n📊 Итого: {total_cal} ккал из {cal_goal}")
    lines.append(f"Б {total_p}г | Ж {total_f}г | У {total_c}г")
    if remaining > 0:
        lines.append(f"Осталось: {remaining} ккал")
    else:
        lines.append(f"Перебор: {abs(remaining)} ккал ⚠️")
    return "\n".join(lines)

# ── Основной обработчик ───────────────────────────────────────────────

async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    pending = get_pending()

    # Подтверждение / отмена
    if pending:
        if is_confirm(text):
            clear_pending(pending["id"])
            msg = await save_action(pending)
            await update.message.reply_text(msg, parse_mode="Markdown")
            return
        elif is_deny(text):
            clear_pending(pending["id"])
            await update.message.reply_text("Отменено.")
            return

    # Классификация
    try:
        c = await classify(text)
        msg_type = c.get("type")
        data     = c.get("data", {})
    except Exception as e:
        logger.error(f"classify error: {e}")
        reply = await gpt(
            "Ты личный ассистент. Ответь кратко на русском. Если не понял — попроси уточнить.",
            text
        )
        await update.message.reply_text(reply)
        return

    # ── food_clarify: уточнение граммов к предыдущему запросу ──
    if msg_type == "food_clarify" and pending and pending["type"] == "food_log":
        new_grams = float(data.get("grams", 100))
        items = pending["data"].get("items", [])
        if items:
            old_name  = items[0]["product_name"]
            old_grams = items[0]["grams"]
            await update.message.reply_text(f"⏳ Пересчитываю на {new_grams}г...")
            # Пересчитываем через коэффициент
            if old_grams and old_grams != new_grams:
                ratio = new_grams / old_grams
                items[0]["grams"]    = new_grams
                items[0]["calories"] = round((items[0].get("calories") or 0) * ratio, 1)
                items[0]["protein"]  = round((items[0].get("protein")  or 0) * ratio, 1)
                items[0]["fat"]      = round((items[0].get("fat")      or 0) * ratio, 1)
                items[0]["carbs"]    = round((items[0].get("carbs")    or 0) * ratio, 1)
            save_pending("food_log", {"items": items}, update.message.message_id)
            await update.message.reply_text(fmt_food(items), parse_mode="Markdown")
            return

    # ── food_log ──
    if msg_type == "food_log":
        raw = data if isinstance(data, list) else []
        if not raw:
            await update.message.reply_text("Не понял что ты съел. Напиши например: «съел 200г гречки и куриную грудку 150г»")
            return
        await update.message.reply_text("⏳ Ищу КБЖУ...")
        items = []
        for item in raw:
            try:
                result = await get_nutrition(item["name"], float(item.get("grams", 100)))
                items.append(result)
            except Exception as e:
                logger.error(f"get_nutrition error for {item}: {e}")
        if not items:
            await update.message.reply_text("Не смог найти КБЖУ. Попробуй ещё раз или уточни название.")
            return
        save_pending("food_log", {"items": items}, update.message.message_id)
        await update.message.reply_text(fmt_food(items), parse_mode="Markdown")

    elif msg_type == "add_product":
        save_pending("add_product", data, update.message.message_id)
        await update.message.reply_text(
            f"📦 *Добавить продукт в базу?*\n\n{data.get('name')}\n"
            f"на 100г: {data.get('calories')} ккал | Б {data.get('protein')}г | Ж {data.get('fat')}г | У {data.get('carbs')}г\n\n"
            f"Подтверждаешь?", parse_mode="Markdown"
        )

    elif msg_type == "body_measurement":
        save_pending("body_measurement", data, update.message.message_id)
        await update.message.reply_text(fmt_measurement(data), parse_mode="Markdown")

    elif msg_type == "expense":
        save_pending("expense", data, update.message.message_id)
        await update.message.reply_text(fmt_expense(data), parse_mode="Markdown")

    elif msg_type == "income":
        save_pending("income", data, update.message.message_id)
        await update.message.reply_text(fmt_income(data), parse_mode="Markdown")

    elif msg_type == "workout":
        save_pending("workout", data, update.message.message_id)
        await update.message.reply_text(fmt_workout(data), parse_mode="Markdown")

    elif msg_type == "reminder":
        save_pending("reminder", data, update.message.message_id)
        await update.message.reply_text(fmt_reminder(data), parse_mode="Markdown")

    elif msg_type == "daily_goals":
        save_pending("daily_goals", data, update.message.message_id)
        hw = data.get("has_workout", False) if isinstance(data, dict) else False
        await update.message.reply_text(
            f"Посчитать цели на сегодня {'с тренировкой 💪' if hw else 'без тренировки 🛋'}?\n\nПодтверждаешь?"
        )

    elif msg_type == "habit_log":
        save_pending("habit_log", data, update.message.message_id)
        status = "✅" if data.get("done", True) else "❌"
        await update.message.reply_text(f"{status} Отметить привычку «{data['habit_name']}»?\n\nПодтверждаешь?")

    elif msg_type == "task":
        save_pending("task", data, update.message.message_id)
        due = f" (до {data['due_date']})" if data.get("due_date") else ""
        await update.message.reply_text(f"📝 Добавить задачу?\n\n«{data['title']}»{due}\n\nПодтверждаешь?")

    elif msg_type == "goal":
        save_pending("goal", data, update.message.message_id)
        await update.message.reply_text(f"🎯 Записать цель?\n\n«{data['title']}»\n\nПодтверждаешь?")

    elif msg_type == "query_today":
        await update.message.reply_text(get_today_stats(), parse_mode="Markdown")

    elif msg_type == "query_workout":
        await update.message.reply_text(get_workout_stats(), parse_mode="Markdown")

    elif msg_type == "query_food":
        await update.message.reply_text(get_food_stats_today(), parse_mode="Markdown")

    elif msg_type == "query_finances":
        period = data.get("period", "month") if isinstance(data, dict) else "month"
        await update.message.reply_text(get_finance_stats(period), parse_mode="Markdown")

    elif msg_type == "query_weight":
        rows = supabase.table("body_measurements").select("date,weight,fat_percent,muscle_percent,bmr").order("date", desc=True).limit(10).execute().data or []
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

    else:
        # general_chat — GPT отвечает как ассистент со знанием контекста
        stats = get_today_stats()
        reply = await gpt(
            f"Ты личный ИИ-ассистент. Отвечай кратко, по делу, на русском языке.\n"
            f"Данные пользователя за сегодня:\n{stats}\n\n"
            f"Отвечай на вопрос прямо и конкретно. Если нет данных — скажи честно.",
            text
        )
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
        raw = await gpt_vision(image_bytes,
            "На изображении скриншот умных весов. Извлеки все показатели. "
            "Верни ТОЛЬКО JSON: {\"weight\":null,\"bmi\":null,\"fat_percent\":null,\"muscle_percent\":null,"
            "\"water_percent\":null,\"visceral_fat\":null,\"bmr\":null,\"fat_mass\":null,\"lean_mass\":null,\"bone_mass\":null}"
        )
        d = parse_json(raw)
        if d.get("weight"):
            save_pending("body_measurement", d, update.message.message_id)
            await update.message.reply_text(fmt_measurement(d), parse_mode="Markdown")
            return
    except Exception as e:
        logger.error(f"scale image: {e}")

    # Пробуем как этикетку
    try:
        raw = await gpt_vision(image_bytes,
            "На изображении этикетка продукта. Извлеки название, бренд и КБЖУ на 100г. "
            "Верни ТОЛЬКО JSON: {\"name\":\"...\",\"brand\":null,\"calories\":0,\"protein\":0,\"fat\":0,\"carbs\":0}"
        )
        d = parse_json(raw)
        if d.get("name") and d.get("calories"):
            save_pending("add_product", d, update.message.message_id)
            brand = f" ({d['brand']})" if d.get("brand") else ""
            await update.message.reply_text(
                f"📦 *{d['name']}{brand}*\n"
                f"на 100г: {d['calories']} ккал | Б {d['protein']}г | Ж {d['fat']}г | У {d['carbs']}г\n\n"
                f"Добавить в базу продуктов?\nПодтверждаешь?", parse_mode="Markdown"
            )
            return
    except Exception as e:
        logger.error(f"label image: {e}")

    await update.message.reply_text("Не смог распознать. Отправь скриншот весов или фото этикетки.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await process_message(update, context, update.message.text.strip())

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Запоминаем chat_id если MY_CHAT_ID не задан
    logger.info(f"Chat ID: {update.effective_chat.id}")
    await update.message.reply_text(
        "👋 Привет! Я твой личный ассистент.\n\n"
        "Просто говори что происходит:\n\n"
        "🍽 *Питание* — «съел сникерс» / «200г гречки и курица»\n"
        "⚖️ *Замеры* — скриншот умных весов\n"
        "💪 *Тренировка* — «был в зале 1.5 часа»\n"
        "💰 *Финансы* — «потратил 500 в Ашане» / «получил 80к»\n"
        "⏰ *Напоминания* — «напомни в среду в 9 поздравить брата»\n"
        "✅ *Задачи* — «добавь задачу купить лекарства»\n"
        "📊 *Статистика* — «что я съел» / «траты за неделю»\n\n"
        "Понимаю голосовые 🎙",
        parse_mode="Markdown"
    )

# ── Планировщик ───────────────────────────────────────────────────────

async def check_reminders(bot: Bot):
    now = now_local()
    rows = supabase.table("reminders").select("*").eq("is_sent", False).lte("remind_at", now.isoformat()).execute().data or []
    for r in rows:
        try:
            chat_id = MY_CHAT_ID or os.getenv("MY_CHAT_ID")
            if chat_id:
                await bot.send_message(chat_id=chat_id, text=f"⏰ *Напоминание:*\n\n{r['text']}", parse_mode="Markdown")
            supabase.table("reminders").update({"is_sent": True}).eq("id", r["id"]).execute()
        except Exception as e:
            logger.error(f"reminder send error: {e}")

async def send_daily_summary(bot: Bot):
    chat_id = MY_CHAT_ID or os.getenv("MY_CHAT_ID")
    if not chat_id:
        return
    try:
        summary = get_today_stats()
        await bot.send_message(chat_id=chat_id, text=f"🌙 *Вечерняя сводка*\n\n{summary}", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"daily summary error: {e}")

# ── Main ─────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(check_reminders, "interval", minutes=1, args=[app.bot])
    scheduler.add_job(send_daily_summary, "cron", hour=21, minute=0, args=[app.bot])
    scheduler.start()

    logger.info("Bot started")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
