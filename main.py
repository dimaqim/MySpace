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

import anthropic
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

groq_client    = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
tavily         = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
ai             = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
claude         = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
supabase: Client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

CLAUDE_MODEL = "claude-sonnet-4-6"

TZ = pytz.timezone(TIMEZONE)

# ── Кэш продуктов ─────────────────────────────────────────────────────
_products_cache: list = []

def load_products_cache():
    global _products_cache
    try:
        _products_cache = supabase.table("products").select("*").execute().data or []
        logger.info(f"Продукты загружены в кэш: {len(_products_cache)} шт.")
    except Exception as e:
        logger.error(f"Ошибка загрузки кэша продуктов: {e}")

def refresh_products_cache():
    """Вызывается после добавления нового продукта в БД."""
    load_products_cache()

CONFIRM_WORDS  = ["да", "подтверждаю", "сохраняй", "ок", "окей", "yes", "верно", "точно", "давай", "сохрани", "конечно", "го", "ага"]
DENY_WORDS     = ["нет", "неверно", "не то", "отмена", "cancel", "no", "стоп", "не надо", "отмени"]
MORE_WORDS     = ["нет", "не всё", "ещё", "еще", "добавлю", "подожди", "буду добавлять"]
DONE_WORDS     = ["да", "всё", "все", "это всё", "это все", "готово", "хватит", "достаточно", "записывай", "сохраняй"]

# Ответы на запрос «искать в интернете» / «добавлю сам»
SEARCH_WEB_WORDS = [
    "поищи", "найди", "ищи", "поиск", "интернет", "в интернете", "в сети",
    "найди в сети", "поищи в интернете", "да поищи", "да найди", "ищи сам",
]
ADD_MANUAL_WORDS = [
    "добавлю сам", "скажу сам", "введу сам", "вручную", "сам добавлю",
    "добавить вручную", "скажу кбжу", "укажу сам", "сам укажу", "добавлю вручную",
]

# Фразы для ответов на утренний запрос взвешивания
WEIGH_SKIP_WORDS = [
    "без взвешивания", "без скриншота", "не взвешивался", "не взвешивалась",
    "сегодня не взвеш", "не буду взвешивать", "пропущу взвешивание",
    "без замера", "не замерялся", "нет взвешивания",
]
WEIGH_LATER_WORDS = [
    "скину позже", "скину потом", "отправлю позже", "отправлю потом",
    "скину взвешивание", "скину скриншот", "позже скину", "потом скину",
    "ночевал не дома", "не дома", "не могу взвеситься", "взвешусь позже",
    "взвешусь потом", "отправлю в течение", "пришлю позже", "пришлю потом",
]

MEAL_TYPE_MAP  = {
    "завтрак": "завтрак", "breakfast": "завтрак",
    "обед": "обед", "lunch": "обед",
    "ужин": "ужин", "dinner": "ужин",
    "перекус": "перекус", "snack": "перекус",
    "полдник": "перекус",
}

CURRENCY_MAP = {
    "гривен": "UAH", "гривна": "UAH", "грн": "UAH", "грн.": "UAH", "₴": "UAH",
    "рублей": "RUB", "рубль": "RUB", "рублей": "RUB", "руб": "RUB", "₽": "RUB",
    "долларов": "USD", "доллар": "USD", "баксов": "USD", "$": "USD",
    "евро": "EUR", "€": "EUR",
}

# ── Helpers ───────────────────────────────────────────────────────────

def today_str():
    return now_local().strftime("%Y-%m-%d")

def now_local():
    return datetime.now(TZ)

def infer_meal_by_time() -> str:
    """Определяет приём пищи по текущему времени, если не указан явно."""
    h = now_local().hour
    if 5 <= h < 11:
        return "завтрак"
    if 11 <= h < 16:
        return "обед"
    if 16 <= h < 23:
        return "ужин"
    return "перекус"  # ночь

# ── Память контекста (последние сообщения) ────────────────────────────
from collections import deque
_chat_history: dict = {}

def remember_msg(chat_id, role: str, text: str):
    if chat_id not in _chat_history:
        _chat_history[chat_id] = deque(maxlen=6)
    _chat_history[chat_id].append((role, text))

def recent_context(chat_id) -> str:
    """Последние сообщения как текстовый контекст для классификатора."""
    hist = _chat_history.get(chat_id)
    if not hist:
        return ""
    lines = []
    for role, txt in hist:
        who = "Пользователь" if role == "user" else "Бот"
        lines.append(f"{who}: {txt[:150]}")
    return "\n".join(lines)

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

def get_weigh_pending():
    """Проверяем — ждём ли сегодня скриншот взвешивания."""
    today = today_str()
    r = supabase.table("pending_actions").select("*") \
        .eq("type", "weigh_morning").eq("status", "pending") \
        .gte("created_at", f"{today}T00:00:00").limit(1).execute()
    return r.data[0] if r.data else None

def clear_weigh_pending(status: str = "done"):
    today = today_str()
    supabase.table("pending_actions").update({"status": status}) \
        .eq("type", "weigh_morning").eq("status", "pending") \
        .gte("created_at", f"{today}T00:00:00").execute()

def is_weigh_skip(text: str) -> bool:
    t = text.lower().strip()
    return any(w in t for w in WEIGH_SKIP_WORDS)

def is_weigh_later(text: str) -> bool:
    t = text.lower().strip()
    return any(w in t for w in WEIGH_LATER_WORDS)

def save_pending(action_type: str, data: dict, msg_id=None):
    supabase.table("pending_actions").update({"status": "expired"}).eq("status", "pending").execute()
    supabase.table("pending_actions").insert({
        "type": action_type, "data": data, "status": "pending",
        "telegram_message_id": str(msg_id) if msg_id else None
    }).execute()

async def notify_error(bot, text: str):
    """Шлёт сообщение об ошибке в Telegram."""
    try:
        chat_id = MY_CHAT_ID or os.getenv("MY_CHAT_ID")
        if chat_id:
            await bot.send_message(chat_id=chat_id, text=f"⚠️ *Ошибка бота:*\n`{text[:400]}`", parse_mode="Markdown")
    except Exception:
        pass

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

async def gpt(system: str, user: str, model: str = CLAUDE_MODEL) -> str:
    """Claude — основная модель для текста и классификации"""
    import asyncio
    r = await asyncio.wait_for(
        claude.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1500,
            system=system,
            messages=[{"role": "user", "content": user}]
        ),
        timeout=40
    )
    return r.content[0].text

async def gpt_vision(image_bytes: bytes, prompt: str) -> str:
    """Claude Vision — анализ изображений"""
    import asyncio
    b64 = base64.b64encode(image_bytes).decode()
    r = await asyncio.wait_for(
        claude.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1000,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": prompt}
            ]}]
        ),
        timeout=40
    )
    return r.content[0].text

# ── Поиск КБЖУ ───────────────────────────────────────────────────────

# Синонимы: слово пользователя → основа для поиска в базе
PRODUCT_SYNONYMS = {
    "курица": "кури", "куриная": "кури", "куриное": "кури", "курочка": "кури",
    "грудка": "грудк", "филе": "филе",
    "помидор": "помидор", "томат": "помидор", "помидорка": "помидор",
    "огурчик": "огур", "огурец": "огур",
    "картошка": "картоф", "картофель": "картоф", "картоха": "картоф",
    "гречка": "гречк", "гречу": "гречк",
    "творожок": "творог", "творог": "творог",
}

_STOP_WORDS = {"сырая", "сырое", "сырой", "свежий", "свежая", "сухой", "сухая",
               "армянский", "белый", "по-корейски", "замороженные", "глазурованный"}

def _stem(word: str) -> str:
    """Грубая основа русского слова — отрезаем окончание."""
    w = word.lower().strip(".,!?;:()")
    if len(w) <= 4:
        return w
    return w[:max(4, len(w) - 2)]

def _match_score(query: str, product_name: str) -> int:
    """Оценка совпадения запроса с названием продукта."""
    q = query.lower().strip()
    pn = (product_name or "").lower()
    # Прямое вхождение — самый сильный сигнал
    if q in pn:
        return 100
    q_words = [w for w in q.split() if len(w) >= 3 and w not in _STOP_WORDS]
    p_words = [w for w in pn.split() if w not in _STOP_WORDS]
    score = 0
    for qw in q_words:
        stem_q = PRODUCT_SYNONYMS.get(qw, _stem(qw))
        for pw in p_words:
            stem_p = PRODUCT_SYNONYMS.get(pw, _stem(pw))
            if stem_q == stem_p or stem_q in pw or stem_p in qw:
                score += 10
                break
    return score

def find_in_db(product_name: str, brand: str = None) -> dict | None:
    """Ищет продукт сначала в кэше (умное сопоставление), потом в Supabase."""
    name_lower = product_name.lower()

    # Поиск в кэше с оценкой совпадения
    if _products_cache:
        scored = []
        for p in _products_cache:
            s = _match_score(product_name, p.get("name") or "")
            if brand and p.get("brand") and brand.lower() in (p["brand"] or "").lower():
                s += 50
            if s > 0:
                # При равном счёте — короче название = ближе к общему продукту
                scored.append((s, -len(p.get("name") or ""), p))
        if scored:
            scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
            return scored[0][2]

    # Fallback в Supabase (если кэш пуст)
    try:
        if brand:
            rows = supabase.table("products").select("*").ilike("name", f"%{product_name}%").limit(10).execute().data or []
            for p in rows:
                if p.get("brand") and brand.lower() in (p["brand"] or "").lower():
                    return p
        r = supabase.table("products").select("*").ilike("name", f"%{product_name}%").limit(1).execute()
        return r.data[0] if r.data else None
    except Exception as e:
        logger.error(f"find_in_db fallback error: {e}")
        return None

def infer_category(name: str, unit: str = "г") -> str:
    """Определяет категорию продукта по единице измерения и имени."""
    if unit == "мл":
        return "напиток"
    drink_keywords = ["сок", "juice", "cola", "кола", "вода", "water", "milk", "молоко",
                      "кефир", "ряженка", "смузи", "энергетик", "red bull", "monster",
                      "чай", "tea", "кофе", "coffee", "лимонад", "компот", "морс"]
    name_l = name.lower()
    if any(k in name_l for k in drink_keywords):
        return "напиток"
    meat_kw = ["курица", "грудка", "говядина", "свинина", "индейка", "рыба", "тунец", "salmon", "лосось"]
    if any(k in name_l for k in meat_kw):
        return "мясо"
    grain_kw = ["гречка", "рис", "овсянка", "паста", "макарон", "лаваш", "хлеб", "батон", "булка"]
    if any(k in name_l for k in grain_kw):
        return "крупы"
    veg_kw = ["помидор", "огурец", "капуста", "морковь", "перец", "лук", "салат", "томат"]
    if any(k in name_l for k in veg_kw):
        return "овощи"
    fruit_kw = ["яблок", "банан", "апельсин", "груша", "виноград", "клубника", "черника"]
    if any(k in name_l for k in fruit_kw):
        return "фрукты"
    dairy_kw = ["творог", "йогурт", "сыр", "кефир", "ряженка", "молоко", "сметана"]
    if any(k in name_l for k in dairy_kw):
        return "молочные"
    sweet_kw = ["шоколад", "конфет", "торт", "пирог", "сникерс", "kit kat", "lion", "mars", "twix", "bounty"]
    if any(k in name_l for k in sweet_kw):
        return "сладости"
    return "еда"

def save_product_to_db(name: str, brand: str | None, cal100: float,
                       pro100: float, fat100: float, carb100: float,
                       category: str = None, unit: str = "г",
                       source: str = None) -> str | None:
    """Сохраняет продукт в Supabase, возвращает id."""
    cat = category or infer_category(name, unit)
    row = {
        "name": name, "calories": cal100, "protein": pro100,
        "fat": fat100, "carbs": carb100,
        "category": cat, "default_unit": unit,
    }
    if brand:       row["brand"] = brand
    if source:      row["data_source"] = source
    res = supabase.table("products").insert(row).execute()
    refresh_products_cache()
    return res.data[0]["id"] if res.data else None

def calc_from_macros(pro100, fat100, carb100) -> float:
    """Калории из макронутриентов если не указаны."""
    return round((pro100 or 0) * 4 + (fat100 or 0) * 9 + (carb100 or 0) * 4, 1)

async def search_nutrition_online(product_name: str) -> dict | None:
    """Tavily + прямой скрапинг приоритетных сайтов. Возвращает макросы на 100г или None."""
    import aiohttp, ssl, re

    async def fetch_page_text(url: str) -> str:
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=8),
                                 headers={"User-Agent": "Mozilla/5.0"}, ssl=ctx) as resp:
                    html = await resp.text(errors="ignore")
                    text = re.sub(r'<[^>]+>', ' ', html)
                    return re.sub(r'\s+', ' ', text)[:4000]
        except Exception as e:
            logger.warning(f"fetch {url}: {e}")
            return ""

    async def macros_from_text(text: str) -> dict | None:
        if len(text) < 30:
            return None
        try:
            raw = await gpt(
                "Из текста извлеки КБЖУ продукта на 100г. Верни ТОЛЬКО JSON: "
                "{\"calories\": число, \"protein\": число, \"fat\": число, \"carbs\": число}. "
                "Если данных нет — верни {\"calories\": null}.",
                f"Продукт: {product_name}\n\nТекст:\n{text}"
            )
            d = parse_json(raw)
            return d if d.get("calories") else None
        except:
            return None

    PRIORITY_SITES = ["tablycjakalorijnosti.com.ua", "calorizator.ru", "fatsecret.ru"]
    macros = None

    try:
        queries = [
            f"{product_name} калорійність білки жири вуглеводи на 100 грам",
            f"{product_name} калорийность белки жиры углеводы на 100г КБЖУ",
            f"{product_name} calories protein fat carbs per 100g nutrition",
        ]
        for query in queries:
            result = tavily.search(query=query, search_depth="advanced", max_results=5)
            results_list = result.get("results", [])

            for r in results_list:
                url = r.get("url", "")
                if any(site in url for site in PRIORITY_SITES):
                    page_text = await fetch_page_text(url)
                    macros = await macros_from_text(page_text)
                    if macros:
                        logger.info(f"Nutrition from {url}")
                        break
            if macros:
                break

            content = " ".join([r.get("content", "") for r in results_list])[:3000]
            macros = await macros_from_text(content)
            if macros:
                break
    except Exception as e:
        logger.warning(f"Tavily error for {product_name}: {e}")

    return macros

async def get_nutrition(product_name: str, grams: float, brand: str = None, unit: str = "г") -> dict:
    """
    Полный поиск КБЖУ: база → интернет (Tavily) → GPT-оценка.
    Найденные данные сохраняет в базу для следующего раза.
    """
    # 1. Supabase
    p = find_in_db(product_name, brand)
    if p:
        # Используем default_unit из базы если unit не переопределён
        eff_unit = unit if unit != "г" else (p.get("default_unit") or unit)
        ratio = grams / 100
        return {
            "product_name": product_name, "grams": grams, "unit": eff_unit,
            "category": p.get("category") or infer_category(product_name, eff_unit),
            "calories": round(p["calories"] * ratio, 1),
            "protein":  round(p["protein"]  * ratio, 1),
            "fat":      round(p["fat"]       * ratio, 1),
            "carbs":    round(p["carbs"]     * ratio, 1),
            "cal100": p["calories"], "pro100": p["protein"],
            "fat100": p["fat"], "carb100": p["carbs"],
            "product_id": p["id"], "auto_estimated": False,
        }

    # 2. Tavily
    macros = await search_nutrition_online(product_name)

    # 3. GPT по памяти
    if not macros:
        raw = await gpt(
            "Ты эксперт по питанию. Укажи КБЖУ продукта на 100г. "
            "Верни ТОЛЬКО JSON: {\"calories\": число, \"protein\": число, \"fat\": число, \"carbs\": число}",
            f"Продукт: {product_name}"
        )
        macros = parse_json(raw)
        macros["auto_estimated"] = True
    else:
        macros["auto_estimated"] = False

    # Сохраняем в базу для будущих запросов
    cat = infer_category(product_name, unit)
    pid = save_product_to_db(product_name, brand, macros["calories"], macros["protein"],
                             macros["fat"], macros["carbs"], category=cat, unit=unit)

    ratio = grams / 100
    return {
        "product_name": product_name, "grams": grams, "unit": unit, "category": cat,
        "calories": round(macros["calories"] * ratio, 1),
        "protein":  round(macros["protein"]  * ratio, 1),
        "fat":      round(macros["fat"]       * ratio, 1),
        "carbs":    round(macros["carbs"]     * ratio, 1),
        "cal100": macros["calories"], "pro100": macros["protein"],
        "fat100": macros["fat"], "carb100": macros["carbs"],
        "product_id": pid, "auto_estimated": macros.get("auto_estimated", True),
    }

async def _resolve_item(item: dict, auto_search: bool = True) -> dict:
    """
    Разрешает один продукт по приоритету:
    1. Если пользователь указал КБЖУ на 100г → сохраняем в базу + считаем
    2. Ищем в Supabase по имени (и бренду)
    3. auto_search=True → Tavily + GPT (для meal_session)
    4. auto_search=False → возвращаем {"not_found": True, ...} (для food_log, чтобы спросить пользователя)
    """
    name  = item.get("name", "")
    unit  = item.get("unit", "г")
    brand = item.get("brand") or None

    # Если граммы не указаны — проверяем default_grams в базе
    raw_grams = item.get("grams")
    if raw_grams is None or float(raw_grams) == 100:
        _db_check = find_in_db(name, brand)
        if _db_check and _db_check.get("default_grams"):
            raw_grams = float(_db_check["default_grams"])
    grams = float(raw_grams) if raw_grams is not None else 100.0

    # Восстанавливаем cal100 из макросов если не указан явно
    pro100  = item.get("pro100")
    fat100  = item.get("fat100")
    carb100 = item.get("carb100")
    cal100  = item.get("cal100")
    if cal100 is None and any(v is not None for v in [pro100, fat100, carb100]):
        cal100 = calc_from_macros(pro100 or 0, fat100 or 0, carb100 or 0)

    # Пользователь указал КБЖУ на 100г — сохраняем в базу и считаем
    if cal100 is not None:
        cat      = infer_category(name, unit)
        existing = find_in_db(name, brand)
        if existing:
            pid = existing["id"]
            cat = existing.get("category") or cat
        else:
            pid = save_product_to_db(name, brand, cal100, pro100 or 0, fat100 or 0, carb100 or 0,
                                     category=cat, unit=unit)
        ratio = grams / 100
        return {
            "product_name": name, "grams": grams, "unit": unit, "brand": brand, "category": cat,
            "calories": round(cal100 * ratio, 1),
            "protein":  round((pro100 or 0) * ratio, 1),
            "fat":      round((fat100 or 0) * ratio, 1),
            "carbs":    round((carb100 or 0) * ratio, 1),
            "cal100": cal100, "pro100": pro100, "fat100": fat100, "carb100": carb100,
            "product_id": pid, "auto_estimated": False,
        }

    # Проверяем базу данных
    p = find_in_db(name, brand)
    if p:
        ratio         = grams / 100
        brand_display = p.get("brand") or brand
        # Приоритет unit: от пользователя > default_unit из БД
        eff_unit = unit if unit == "мл" else (p.get("default_unit") or unit)
        # Категория: из БД > по infer_category (unit="мл" всегда даёт "напиток")
        cat = p.get("category") or infer_category(name, eff_unit)
        # Защита: если default_unit в БД = "мл", это напиток в любом случае
        if p.get("default_unit") == "мл":
            cat = "напиток"
            eff_unit = "мл"
        return {
            "product_name": name, "grams": grams, "unit": eff_unit, "brand": brand_display, "category": cat,
            "calories": round(p["calories"] * ratio, 1),
            "protein":  round(p["protein"]  * ratio, 1),
            "fat":      round(p["fat"]       * ratio, 1),
            "carbs":    round(p["carbs"]     * ratio, 1),
            "cal100": p["calories"], "pro100": p["protein"],
            "fat100": p["fat"], "carb100": p["carbs"],
            "product_id": p["id"], "auto_estimated": False,
        }

    # Продукт не найден в базе
    if not auto_search:
        return {"not_found": True, "name": name, "brand": brand, "grams": grams, "unit": unit}

    # auto_search=True: Tavily + GPT (для meal_session)
    return await get_nutrition(name, grams, brand=brand, unit=unit)

# ── Классификатор ─────────────────────────────────────────────────────

async def classify(text: str, context_str: str = "") -> dict:
    now = now_local()
    # Вычисляем полезные даты
    weekday        = now.weekday()  # 0=пн, 6=вс
    days_to_sunday = 6 - weekday
    yesterday      = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    tomorrow       = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    end_of_week    = (now + timedelta(days=days_to_sunday)).strftime("%Y-%m-%d")
    next_monday    = (now + timedelta(days=7 - weekday)).strftime("%Y-%m-%d")

    today   = now.strftime('%Y-%m-%d')
    system = f"""Ты классификатор сообщений для личного трекера здоровья и финансов. Сегодня {today}, вчера {yesterday}.
Верни ТОЛЬКО JSON: {{"type":"...", "data":...}}

═══ ПРАВИЛО №1 — ЕДА И ПИТЬЁ (ВЫСШИЙ ПРИОРИТЕТ) ═══
Если в сообщении есть слова: съел, выпил, поел, попил, скушал, скушала, перекусил, употребил, выпила, съела, поела —
это ВСЕГДА один из типов еды (food_log / meal_session / multi_meal). НИКОГДА general_chat.

Как выбрать тип еды:
• 1-2 продукта в одном приёме → "food_log"
• 3+ продукта в одном приёме пищи → "meal_session"
• Несколько приёмов сразу (завтрак + обед, обед + ужин и т.д.) → "multi_meal"

═══ ФОРМАТЫ ═══

"food_log" data: {{"date":"{today}","meal_type":null,"items":[{{"name":"...","brand":null,"grams":число,"unit":"г или мл","cal100":null,"pro100":null,"fat100":null,"carb100":null}}]}}
• meal_type: "завтрак/обед/ужин/перекус" если упомянут, иначе null
• ВАЖНО: "на завтрак", "на завтра" (опечатка завтрак), "за завтраком" → meal_type="завтрак"
• "на обед/за обедом"→"обед", "на ужин/за ужином"→"ужин", "на перекус"→"перекус"
• "вчера на завтрак выпил Red Bull" → date={yesterday}, meal_type="завтрак"
• "вчера на завтра выпил банку ред булла" → date={yesterday}, meal_type="завтрак"
• "выпил Red Bull" (без приёма) → meal_type=null
• unit: "мл" для любых напитков, "г" для еды
• grams если не указано: яблоко=150г, банан=120г, сникерс=55г, стакан=250мл, кружка=300мл
• Red Bull всегда 1 банка = 250мл:
  - "выпил Red Bull" / "банку Red Bull Zero" / "Red Bull Zero" → name="Red Bull Zero", grams=250, unit="мл"
  - "Red Bull арбуз" / "Red Bull Watermelon" → name="Red Bull Watermelon", grams=250, unit="мл"
  - "Red Bull тропик" / "Red Bull Tropical" → name="Red Bull Tropical", grams=250, unit="мл"
  - "2 банки Red Bull Zero" → grams=500
• Примеры: "съел яблоко"→яблоко 150г, "выпил кофе"→кофе 250мл, "выпил одну банку Red Bull Zero"→Red Bull Zero 250мл

"meal_session" data: {{"meal_type":"завтрак/обед/ужин/перекус","date":"{today}","items":[{{"name":"...","brand":null,"grams":число,"unit":"г или мл","cal100":null,"pro100":null,"fat100":null,"carb100":null}}]}}
• date: сегодня={today}, вчера={yesterday}
• Примеры: "на завтрак: лаваш 80г, помидор 300г, огурец 100г" → meal_session завтрак

"multi_meal" data: {{"meals":[{{"meal_type":"завтрак/обед/ужин/перекус","date":"{today}","items":[...]}}]}}
• Только приёмы с продуктами (пропусти "ужин — ничего не ел")
• Примеры: "на завтрак лаваш 80г, на обед рис 200г и курица 300г" → multi_meal

"food_log_known_macros" data: {{"name":"...","brand":null,"grams":число,"calories":число,"protein":число,"fat":число,"carbs":число}}
• Когда КБЖУ дан для конкретного кол-ва (не на 100г)
• "съел батончик 45г, 210 ккал, белков 3г, жиров 9г, углеводов 28г" → food_log_known_macros

"food_clarify" data: {{"grams":число}}
• Просто число/граммы в ответ на вопрос бота: "150г", "200 грамм", "съел 80"

"add_product" data: {{"name":"...","brand":null,"calories":число,"protein":число,"fat":число,"carbs":число}}
• "добавь в базу творог Простоквашино: 100г — 100 ккал, белок 18г, жир 5г, углеводы 3г"

"body_measurement" data: {{"weight":null,"fat_percent":null,"muscle_percent":null,"waist":null}}
• "вешу 78кг", "жира 18%", "талия 82см"

"workout" data: {{"duration_minutes":null,"location":"зал/улица/дома","exercises":[],"notes":"","calories_burned":null}}

"expense" data: {{"amount":число,"currency":"UAH/RUB/USD/EUR","category":"еда/транспорт/...","description":"...","store_name":null}}
• Валюта: грн/гривен/₴=UAH, руб/рублей/₽=RUB, $/долларов=USD, €/евро=EUR

"income" data: {{"amount":число,"currency":"UAH","category":"зарплата/фриланс/...","description":"..."}}

"reminder" data: {{"text":"...","remind_at":"YYYY-MM-DDTHH:MM:SS"}}
• Без времени → 12:00 текущего дня
• "через час" → {(now + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%S')}
• "в 21:00 будет футбол" → remind_at={now.strftime('%Y-%m-%d')}T20:50:00 (за 10 мин)

"daily_goals" data: {{"has_workout":true/false}}

"habit_log" data: {{"habit_name":"...","done":true,"note":null}}

"task" data: {{"title":"...","due_date":null,"priority":"normal/high/low"}}

"query_today" data: {{}}
• "итог дня", "как я сегодня", "что сегодня делал", "дай сводку за сегодня"

"query_food" data: {{"date":null,"from_date":null,"to_date":null,"meal_type":null}}
• "что я сегодня ел" → date={today}
• "что ел вчера на ужин" → date={yesterday}, meal_type="ужин"
• "что ел на этой неделе" → from_date={(now - timedelta(days=now.weekday())).strftime('%Y-%m-%d')}, to_date={today}

"query_finances" data: {{"from_date":"...","to_date":"...","subtype":"all/income/expense"}}
• "сколько потратил за неделю" → from_date={(now - timedelta(days=now.weekday())).strftime('%Y-%m-%d')}, to_date={today}, subtype="expense"
• "доходы за месяц" → from_date={now.strftime('%Y-%m-01')}, to_date={today}, subtype="income"

"query_weight" data: {{"from_date":null}}

"delete_food_log" data: {{"date":"{today}","meal_type":null,"product_name":null}}
• Триггеры: "удали", "сотри", "убери", "отмени запись", "стёр", "убери запись"
• date: сегодня={today}, вчера={yesterday}. Слова "вчера"→{yesterday}, "сегодня"→{today}
• meal_type: "завтрак/обед/ужин/перекус" если упомянут, иначе null
• product_name: название продукта если упомянут, иначе null
• "удали последнюю запись" → {{"date":"{today}","meal_type":null,"product_name":null}}
• "удали завтрак" → {{"date":"{today}","meal_type":"завтрак","product_name":null}}
• "удали вчерашний ужин" → {{"date":"{yesterday}","meal_type":"ужин","product_name":null}}
• "удали то что я вчера пил" → {{"date":"{yesterday}","meal_type":null,"product_name":null}}
• "удали вчерашний Red Bull" → {{"date":"{yesterday}","meal_type":null,"product_name":"Red Bull"}}
• "удали то что я вчера пил банку редбула" → {{"date":"{yesterday}","meal_type":null,"product_name":"Red Bull"}}
• "удали курицу из обеда" → {{"date":"{today}","meal_type":"обед","product_name":"курица"}}

"edit_food_log" data: {{"target":"last/product","product_name":null,"new_grams":null,"new_meal_type":null}}
• Правка ПОСЛЕДНЕЙ записи еды (не удаление, не новая запись).
• Триггеры: "поправь", "исправь", "измени", "не 300 а 200", "это был обед а не завтрак", "переправь"
• "поправь последнюю на 250г" → {{"target":"last","new_grams":250}}
• "не 300 грамм а 200" → {{"target":"last","new_grams":200}}
• "это был обед а не завтрак" → {{"target":"last","new_meal_type":"обед"}}
• "исправь курицу на 200г" → {{"target":"product","product_name":"курица","new_grams":200}}
• ВАЖНО: если в КОНТЕКСТЕ выше бот только что записал еду и пользователь поправляет количество — это edit_food_log

"general_chat" data: {{}}
• ТОЛЬКО если ничего выше не подходит (вопросы не про еду/здоровье/финансы)"""

    user_content = text
    if context_str:
        user_content = (
            f"=== КОНТЕКСТ (последние сообщения, для понимания ссылок типа «а нет, 200г») ===\n"
            f"{context_str}\n=== КОНЕЦ КОНТЕКСТА ===\n\n"
            f"Новое сообщение пользователя: {text}"
        )

    import asyncio
    r = await asyncio.wait_for(
        claude.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1500,
            system=system,
            messages=[{"role": "user", "content": user_content}]
        ),
        timeout=40
    )
    raw = r.content[0].text.strip()
    logger.info(f"classify raw: {raw[:200]}")

    # Вытаскиваем JSON из ответа — поддерживаем разные форматы
    # 1. Чистый JSON
    # 2. ```json ... ```
    # 3. JSON внутри текста
    import re as _re
    # Убираем markdown блок
    match = _re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw, _re.DOTALL)
    if match:
        raw = match.group(1)
    else:
        # Берём первый {...} блок
        match = _re.search(r'\{.*\}', raw, _re.DOTALL)
        if match:
            raw = match.group(0)

    return json.loads(raw)

# ── Форматирование ────────────────────────────────────────────────────

def _split_food_drink(items: list) -> tuple[list, list]:
    """Разделяет список продуктов на еду и напитки."""
    food   = [it for it in items if (it.get("category") or infer_category(it.get("product_name",""), it.get("unit","г"))) == "напиток"]
    drinks = [it for it in items if it not in food]
    # food = еда, drinks = напитки (переименуем правильно)
    return (
        [it for it in items if (it.get("category") or infer_category(it.get("product_name",""), it.get("unit","г"))) != "напиток"],
        [it for it in items if (it.get("category") or infer_category(it.get("product_name",""), it.get("unit","г"))) == "напиток"],
    )

def fmt_items_section(items: list, emoji: str, label: str) -> list[str]:
    lines = [f"{emoji} *{label}:*"]
    for it in items:
        est  = " _(ИИ)_" if it.get("auto_estimated") else ""
        unit = it.get("unit", "г")
        cal  = round(it.get("calories") or 0)
        lines.append(f"  • {it['product_name']}: {it['grams']}{unit} — {cal} ккал{est}")
    cal_s = round(sum(it.get("calories") or 0 for it in items))
    p_s   = round(sum(it.get("protein")  or 0 for it in items))
    f_s   = round(sum(it.get("fat")      or 0 for it in items))
    c_s   = round(sum(it.get("carbs")    or 0 for it in items))
    lines.append(f"  → {cal_s} ккал | Б {p_s}г | Ж {f_s}г | У {c_s}г")
    return lines

def fmt_food(items: list, log_date: str = None, meal_type: str = None) -> str:
    td = today_str()
    date_label = ""
    if log_date and log_date != td:
        try:
            from datetime import datetime as _dt
            d = _dt.strptime(log_date, "%Y-%m-%d")
            date_label = f" за {d.strftime('%d.%m')}"
        except:
            date_label = f" за {log_date}"
    meal_label = f" ({meal_type})" if meal_type else ""
    lines = [f"📝 *Записать{meal_label}{date_label}?*\n"]

    food_items, drink_items = _split_food_drink(items)
    if food_items:
        lines += fmt_items_section(food_items, "🍽", "Еда")
    if drink_items:
        if food_items: lines.append("")
        lines += fmt_items_section(drink_items, "🥤", "Напитки")

    total_cal = round(sum(it.get("calories") or 0 for it in items))
    total_p   = round(sum(it.get("protein")  or 0 for it in items))
    total_f   = round(sum(it.get("fat")      or 0 for it in items))
    total_c   = round(sum(it.get("carbs")    or 0 for it in items))
    lines.append(f"\n📊 *Итого:* {total_cal} ккал | Б {total_p}г | Ж {total_f}г | У {total_c}г")
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

def fmt_meal_session(data: dict, show_footer: bool = True) -> str:
    items     = data.get("items", [])
    meal_type = data.get("meal_type", "приём пищи")
    log_date  = data.get("date", today_str())
    now_t     = now_local().strftime("%H:%M")

    total_cal = sum(it.get("calories") or 0 for it in items)
    total_p   = sum(it.get("protein")  or 0 for it in items)
    total_f   = sum(it.get("fat")      or 0 for it in items)
    total_c   = sum(it.get("carbs")    or 0 for it in items)

    td = today_str()
    date_label = ""
    if log_date and log_date != td:
        try:
            from datetime import datetime as _dt
            d = _dt.strptime(log_date, "%Y-%m-%d")
            date_label = f" — {d.strftime('%d.%m')}"
        except:
            date_label = f" — {log_date}"

    lines = [f"🍽 *{meal_type.capitalize()}{date_label}*\n"]

    food_items, drink_items = _split_food_drink(items)
    if food_items:
        lines += fmt_items_section(food_items, "🍽", "Еда")
    if drink_items:
        if food_items: lines.append("")
        lines += fmt_items_section(drink_items, "🥤", "Напитки")

    lines.append(f"\n📊 *Итого:* {round(total_cal)} ккал | Б {round(total_p)}г | Ж {round(total_f)}г | У {round(total_c)}г")
    if show_footer:
        lines.append("\nЭто всё или добавишь ещё?")
        lines.append("_(«да» — записываю / «нет» — жду ещё продукты)_")
    return "\n".join(lines)

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

    if t in ("food_log", "meal_session"):
        items     = data.get("items", [])
        meal_type = data.get("meal_type")
        log_date  = data.get("date", td)   # поддержка задних чисел
        logged_at = now_local().isoformat()

        for it in items:
            row = {
                "date": log_date, "logged_at": logged_at,
                "product_name": it.get("product_name"),
                "grams":    it.get("grams"),
                "calories": it.get("calories"),
                "protein":  it.get("protein"),
                "fat":      it.get("fat"),
                "carbs":    it.get("carbs"),
                "meal_type": meal_type,
                "unit":     it.get("unit", "г"),
            }
            if it.get("product_id"):
                row["product_id"] = it["product_id"]
            supabase.table("food_log").insert(row).execute()

            # Сохраняем продукт в базу если его ещё нет
            if it.get("product_name") and it.get("cal100"):
                ex = supabase.table("products").select("id").ilike("name", f"%{it['product_name']}%").limit(1).execute()
                if not ex.data:
                    supabase.table("products").insert({
                        "name": it["product_name"],
                        "brand": it.get("brand"),
                        "calories": it.get("cal100"),
                        "protein":  it.get("pro100"),
                        "fat":      it.get("fat100"),
                        "carbs":    it.get("carb100"),
                    }).execute()
                    refresh_products_cache()

        total_cal = round(sum(it.get("calories") or 0 for it in items))
        total_p   = round(sum(it.get("protein")  or 0 for it in items))
        total_f   = round(sum(it.get("fat")      or 0 for it in items))
        total_c   = round(sum(it.get("carbs")    or 0 for it in items))
        meal_label = f" {meal_type}" if meal_type else ""
        date_label = f" за {log_date}" if log_date != td else ""
        return (f"✅{meal_label}{date_label} записан!\n"
                f"{total_cal} ккал | Б {total_p}г | Ж {total_f}г | У {total_c}г")

    if t == "multi_meal":
        meals = data.get("meals", [])
        logged_at = now_local().isoformat()
        total_cal = total_p = total_f = total_c = 0
        saved_meals = []
        for meal in meals:
            meal_type = meal.get("meal_type")
            log_date  = meal.get("date", td)
            items     = meal.get("items", [])
            for it in items:
                row = {
                    "date": log_date, "logged_at": logged_at,
                    "product_name": it.get("product_name"),
                    "grams":    it.get("grams"),
                    "calories": it.get("calories"),
                    "protein":  it.get("protein"),
                    "fat":      it.get("fat"),
                    "carbs":    it.get("carbs"),
                    "meal_type": meal_type,
                    "unit":     it.get("unit", "г"),
                }
                if it.get("product_id"):
                    row["product_id"] = it["product_id"]
                supabase.table("food_log").insert(row).execute()
            meal_cal = round(sum(it.get("calories") or 0 for it in items))
            total_cal += meal_cal
            total_p   += sum(it.get("protein") or 0 for it in items)
            total_f   += sum(it.get("fat")     or 0 for it in items)
            total_c   += sum(it.get("carbs")   or 0 for it in items)
            saved_meals.append(f"✅ {meal_type.capitalize()} — {meal_cal} ккал")
        result = "\n".join(saved_meals)
        result += f"\n\n📊 Итого: {round(total_cal)} ккал | Б {round(total_p)}г | Ж {round(total_f)}г | У {round(total_c)}г"
        return result

    if t == "delete_food_log":
        ids = data.get("ids", [])
        if not ids:
            return "Нечего удалять."
        for fid in ids:
            supabase.table("food_log").delete().eq("id", fid).execute()
        cal = data.get("total_cal", 0)
        return f"🗑 Удалено {len(ids)} запис{'ь' if len(ids)==1 else 'и' if len(ids)<5 else 'ей'} ({round(cal)} ккал)"

    if t == "add_product":
        supabase.table("products").insert({
            "name":     data.get("name"),
            "brand":    data.get("brand"),
            "calories": data.get("calories"),
            "protein":  data.get("protein"),
            "fat":      data.get("fat"),
            "carbs":    data.get("carbs"),
        }).execute()
        refresh_products_cache()
        return f"✅ Продукт «{data.get('name')}» добавлен в базу!"

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
        food_rows  = [r for r in food if (r.get("category") or infer_category(r.get("product_name",""), r.get("unit","г"))) != "напиток"]
        drink_rows = [r for r in food if (r.get("category") or infer_category(r.get("product_name",""), r.get("unit","г"))) == "напиток"]
        for r in food_rows:
            unit = r.get("unit", "г")
            lines.append(f"   🍽 {r.get('product_name')}: {r.get('grams')}{unit} — {round(r.get('calories') or 0)} ккал")
        for r in drink_rows:
            unit = r.get("unit", "мл")
            lines.append(f"   🥤 {r.get('product_name')}: {r.get('grams')}{unit} — {round(r.get('calories') or 0)} ккал")
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

    food_rows  = [r for r in food if (r.get("category") or infer_category(r.get("product_name",""), r.get("unit","г"))) != "напиток"]
    drink_rows = [r for r in food if (r.get("category") or infer_category(r.get("product_name",""), r.get("unit","г"))) == "напиток"]

    lines = ["🍽 *Сегодня:*\n"]
    if food_rows:
        lines.append("🍽 *Еда:*")
        for r in food_rows:
            unit = r.get("unit", "г")
            lines.append(f"  • {r.get('product_name')}: {r.get('grams')}{unit} — {round(r.get('calories') or 0)} ккал")
    if drink_rows:
        if food_rows: lines.append("")
        lines.append("🥤 *Напитки:*")
        for r in drink_rows:
            unit = r.get("unit", "мл")
            lines.append(f"  • {r.get('product_name')}: {r.get('grams')}{unit} — {round(r.get('calories') or 0)} ккал")

    lines.append(f"\n📊 *Итого:* {total_cal} ккал из {cal_goal}")
    lines.append(f"Б {total_p}г | Ж {total_f}г | У {total_c}г")
    if remaining > 0:
        lines.append(f"Осталось: {remaining} ккал")
    else:
        lines.append(f"Перебор: {abs(remaining)} ккал ⚠️")
    return "\n".join(lines)

# ── Основной обработчик ───────────────────────────────────────────────

async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    pending = get_pending()
    t_low   = text.lower().strip()
    chat_id = update.effective_chat.id
    # Контекст последних сообщений (до записи текущего)
    ctx_str = recent_context(chat_id)
    remember_msg(chat_id, "user", text)

    # ── weigh_morning: ответы на утренний запрос взвешивания ──
    # Не блокирует другие действия — просто перехватываем целевые фразы
    weigh = get_weigh_pending()
    if weigh:
        if is_weigh_skip(t_low):
            clear_weigh_pending("skipped")
            await update.message.reply_text(
                "Хорошо, сегодня без взвешивания 👌\n"
                "Если передумаешь — просто пришли скриншот в любое время."
            )
            return
        if is_weigh_later(t_low):
            await update.message.reply_text(
                "Хорошо! Буду ждать скриншот взвешивания в течение дня 📱\n"
                "Как взвесишься — просто пришли фото."
            )
            return

    # ── meal_session: ВСЕГДА проверяем первым ──
    # чтобы «нет» = «жду ещё продукты», а не «отмена»
    if pending and pending["type"] == "meal_session":

        if any(t_low == w or t_low.startswith(w) for w in DONE_WORDS):
            clear_pending(pending["id"])
            msg = await save_action(pending)
            await update.message.reply_text(msg, parse_mode="Markdown")
            return

        if any(t_low == w or t_low.startswith(w) for w in MORE_WORDS):
            await update.message.reply_text("Хорошо, жду следующие продукты 👂")
            return

        # Классифицируем чтобы понять — новые продукты или что-то другое
        try:
            c = await classify(text)
            msg_type = c.get("type")
            data     = c.get("data", {})
        except Exception as e:
            logger.error(f"classify error in meal_session: {e}")
            await update.message.reply_text("Не понял. Добавь продукты или напиши «да» чтобы сохранить ужин.")
            return

        # Если пришёл новый полноценный приём пищи — сбрасываем старый pending
        if msg_type == "multi_meal":
            clear_pending(pending["id"])
            # передаём управление дальше — обработчик multi_meal ниже
        elif msg_type in ("food_log", "meal_session"):
            new_raw = data if isinstance(data, list) else data.get("items", [])
            if new_raw:
                await update.message.reply_text("⏳ Ищу КБЖУ для новых продуктов...")
                existing_items = pending["data"].get("items", [])
                for item in new_raw:
                    try:
                        existing_items.append(await _resolve_item(item))
                    except Exception as e:
                        logger.error(f"resolve item error: {e}")
                pending["data"]["items"] = existing_items
                save_pending("meal_session", pending["data"], update.message.message_id)
                await update.message.reply_text(fmt_meal_session(pending["data"]), parse_mode="Markdown")
                return
        else:
            # Если написал что-то не связанное с едой — уточняем
            await update.message.reply_text(
                "У тебя открыт приём пищи.\n\n"
                "Напиши продукты чтобы добавить, или:\n"
                "• *«да»* — сохранить как есть\n"
                "• *«отмена»* — отменить",
                parse_mode="Markdown"
            )
            return

    # ── product_lookup_choice: ожидаем ответ — искать онлайн или добавить вручную ──
    if pending and pending["type"] == "product_lookup_choice":
        pdata        = pending["data"]
        p_name       = pdata["product_name"]
        p_brand      = pdata.get("brand")
        p_grams      = float(pdata["grams"])
        p_unit       = pdata.get("unit", "г")
        done_items   = pdata.get("resolved_items", [])
        p_log_date   = pdata.get("log_date", today_str())
        p_meal_type  = pdata.get("meal_type")
        manual_mode  = pdata.get("manual_mode", False)

        brand_str = f" ({p_brand})" if p_brand else ""

        # В режиме ожидания КБЖУ от пользователя (после «добавлю сам»)
        # или пользователь сразу дал КБЖУ в ответ
        try:
            c_inner = await classify(text)
            inner_type = c_inner.get("type")
            inner_data = c_inner.get("data", {})
        except:
            inner_type = None
            inner_data = {}

        # Пользователь предоставил КБЖУ (на 100г или на факт. граммы)
        if inner_type == "add_product" or manual_mode:
            if inner_type == "food_log_known_macros":
                g = float(inner_data.get("grams", p_grams))
                r = 100 / g if g else 1
                cal100  = round(float(inner_data.get("calories", 0)) * r, 1)
                pro100  = round(float(inner_data.get("protein",  0)) * r, 1)
                fat100  = round(float(inner_data.get("fat",      0)) * r, 1)
                carb100 = round(float(inner_data.get("carbs",    0)) * r, 1)
            elif inner_type == "add_product":
                cal100  = float(inner_data.get("calories", 0))
                pro100  = float(inner_data.get("protein",  0))
                fat100  = float(inner_data.get("fat",      0))
                carb100 = float(inner_data.get("carbs",    0))
                p_brand = inner_data.get("brand") or p_brand
            elif manual_mode:
                # Пробуем распарсить числа из сообщения через GPT
                raw_m = await gpt(
                    "Пользователь называет КБЖУ на 100г продукта. Верни ТОЛЬКО JSON: "
                    "{\"calories\": число, \"protein\": число, \"fat\": число, \"carbs\": число, \"brand\": null}. "
                    "Если данных нет — верни {\"calories\": null}.",
                    f"Продукт: {p_name}\nСообщение: {text}"
                )
                try:
                    d_m = parse_json(raw_m)
                    if not d_m.get("calories"):
                        await update.message.reply_text(
                            f"Не смог распознать КБЖУ. Напиши, например:\n"
                            f"«на 100г: 250 ккал, белки 8г, жиры 3г, углеводы 45г»"
                        )
                        return
                    cal100  = float(d_m["calories"])
                    pro100  = float(d_m.get("protein", 0))
                    fat100  = float(d_m.get("fat", 0))
                    carb100 = float(d_m.get("carbs", 0))
                    p_brand = d_m.get("brand") or p_brand
                except:
                    await update.message.reply_text("Не смог распознать. Напиши КБЖУ на 100г числами.")
                    return
            else:
                await update.message.reply_text(
                    f"Напиши КБЖУ на 100г для «{p_name}».\n"
                    f"Например: «на 100г: 250 ккал, белки 8г, жиры 3г, углеводы 45г»"
                )
                return

            clear_pending(pending["id"])
            pid = save_product_to_db(p_name, p_brand, cal100, pro100, fat100, carb100)
            ratio = p_grams / 100
            new_item = {
                "product_name": p_name, "grams": p_grams, "unit": p_unit, "brand": p_brand,
                "calories": round(cal100 * ratio, 1), "protein": round(pro100 * ratio, 1),
                "fat": round(fat100 * ratio, 1),      "carbs": round(carb100 * ratio, 1),
                "cal100": cal100, "pro100": pro100, "fat100": fat100, "carb100": carb100,
                "product_id": pid, "auto_estimated": False,
            }
            all_items = done_items + [new_item]
            fl_pending = {"items": all_items, "date": p_log_date, "meal_type": p_meal_type}
            save_pending("food_log", fl_pending, update.message.message_id)
            await update.message.reply_text(
                f"✅ Продукт «{p_name}{brand_str}» сохранён в базу!\n\n" + fmt_food(all_items, p_log_date, p_meal_type),
                parse_mode="Markdown"
            )
            return

        # Пользователь хочет поискать в интернете
        if any(w in t_low for w in SEARCH_WEB_WORDS):
            clear_pending(pending["id"])
            await update.message.reply_text(f"⏳ Ищу «{p_name}» в интернете...")
            try:
                result = await get_nutrition(p_name, p_grams, brand=p_brand, unit=p_unit)
                all_items = done_items + [result]
                fl_pending = {"items": all_items, "date": p_log_date, "meal_type": p_meal_type}
                save_pending("food_log", fl_pending, update.message.message_id)
                await update.message.reply_text(fmt_food(all_items, p_log_date, p_meal_type), parse_mode="Markdown")
            except Exception as e:
                logger.error(f"online search error: {e}")
                await update.message.reply_text("Не смог найти в интернете. Напиши КБЖУ сам.")
            return

        # Пользователь хочет добавить вручную
        if any(w in t_low for w in ADD_MANUAL_WORDS):
            # Обновляем флаг manual_mode и просим КБЖУ
            pdata["manual_mode"] = True
            save_pending("product_lookup_choice", pdata, update.message.message_id)
            await update.message.reply_text(
                f"Хорошо! Напиши КБЖУ на 100г для «{p_name}{brand_str}».\n\n"
                f"Например: «на 100г: 250 ккал, белки 8г, жиры 3г, углеводы 45г»"
            )
            return

        # Не поняли ответ
        await update.message.reply_text(
            f"Продукт «{p_name}{brand_str}» не найден в базе.\n\n"
            f"• Напиши *«поищи»* — найду в интернете\n"
            f"• Напиши *«добавлю сам»* — укажи КБЖУ на 100г\n"
            f"• Или сразу: «на 100г: 250 ккал, белки 8г, жиры 3г, углеводы 45г»",
            parse_mode="Markdown"
        )
        return

    # ── Общий confirm/deny для остальных pending ──
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

    # Классификация (с контекстом последних сообщений)
    try:
        c = await classify(text, context_str=ctx_str)
        msg_type = c.get("type")
        data     = c.get("data", {})
    except Exception as e:
        logger.error(f"classify error: {e}")
        await notify_error(context.bot, f"classify: {e}\nТекст: {text[:200]}")
        await update.message.reply_text("⚠️ Не понял запрос. Попробуй ещё раз.")
        return

    # Если пришёл новый multi_meal при открытом pending — сбрасываем старый
    if msg_type == "multi_meal" and pending and pending["type"] in ("food_log", "multi_meal", "meal_session"):
        clear_pending(pending["id"])
        pending = None

    # ── food_clarify: уточнение граммов ──
    if msg_type == "food_clarify" and pending and pending["type"] == "food_log":
        new_grams = float(data.get("grams", 100))
        items = pending["data"].get("items", [])
        if items:
            old_grams = items[0]["grams"]
            if old_grams and old_grams != new_grams:
                unit_lbl = items[0].get("unit", "г")
                await update.message.reply_text(f"⏳ Пересчитываю на {new_grams}{unit_lbl}...")
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
        # Поддерживаем оба формата: список (старый) и dict с date/meal_type (новый)
        if isinstance(data, dict):
            raw          = data.get("items", [])
            log_date     = data.get("date", today_str())
            meal_type_fl = data.get("meal_type")
        else:
            raw          = data if isinstance(data, list) else []
            log_date     = today_str()
            meal_type_fl = None

        # Если приём пищи не указан и запись за сегодня — определяем по времени
        if not meal_type_fl and log_date == today_str():
            meal_type_fl = infer_meal_by_time()

        if not raw:
            await update.message.reply_text("Не понял что ты съел. Напиши например: «съел 200г гречки и куриную грудку 150г»")
            return

        await update.message.reply_text("⏳ Проверяю базу продуктов...")
        resolved  = []
        not_found = None

        for item in raw:
            try:
                result = await _resolve_item(item, auto_search=False)
                if result.get("not_found"):
                    not_found = result
                    break
                resolved.append(result)
            except Exception as e:
                logger.error(f"resolve error for {item}: {e}")

        if not_found:
            brand     = not_found.get("brand")
            brand_str = f" ({brand})" if brand else ""
            save_pending("product_lookup_choice", {
                "product_name":   not_found["name"],
                "brand":          brand,
                "grams":          not_found["grams"],
                "unit":           not_found.get("unit", "г"),
                "resolved_items": resolved,
                "log_date":       log_date,
                "meal_type":      meal_type_fl,
                "manual_mode":    False,
            }, update.message.message_id)
            await update.message.reply_text(
                f"🔍 Продукта «{not_found['name']}{brand_str}» нет в моей базе.\n\n"
                f"Что делаем?\n"
                f"• Напиши *«поищи»* — найду данные в интернете\n"
                f"• Напиши *«добавлю сам»* — укажи бренд и КБЖУ на 100г\n"
                f"• Или сразу напиши КБЖУ: «на 100г: 250 ккал, белки 8г, жиры 3г, углеводы 45г»",
                parse_mode="Markdown"
            )
            return

        pending_data = {"items": resolved, "date": log_date, "meal_type": meal_type_fl}
        save_pending("food_log", pending_data, update.message.message_id)
        await update.message.reply_text(fmt_food(resolved, log_date, meal_type_fl), parse_mode="Markdown")
        _summary = ", ".join(f"{it['product_name']} {it['grams']}{it.get('unit','г')}" for it in resolved)
        remember_msg(chat_id, "bot", f"показал к записи ({meal_type_fl or 'без приёма'}): {_summary}")

    # ── food_log_known_macros: съел X г + КБЖУ указан для этого количества ──
    elif msg_type == "food_log_known_macros":
        grams    = float(data.get("grams", 100))
        calories = float(data.get("calories", 0))
        protein  = float(data.get("protein", 0))
        fat      = float(data.get("fat", 0))
        carbs    = float(data.get("carbs", 0))
        name     = data.get("name", "продукт")
        brand    = data.get("brand")
        unit     = data.get("unit", "г")

        ratio100 = 100 / grams if grams else 1
        cal100   = round(calories * ratio100, 1)
        pro100   = round(protein  * ratio100, 1)
        fat100   = round(fat      * ratio100, 1)
        carb100  = round(carbs    * ratio100, 1)

        # Сохраняем продукт в базу если его нет
        existing = find_in_db(name, brand)
        if not existing:
            save_product_to_db(name, brand, cal100, pro100, fat100, carb100)

        item = {
            "product_name": name, "grams": grams, "unit": unit, "brand": brand,
            "calories": calories, "protein": protein, "fat": fat, "carbs": carbs,
            "cal100": cal100, "pro100": pro100, "fat100": fat100, "carb100": carb100,
            "auto_estimated": False,
        }
        save_pending("food_log", {"items": [item]}, update.message.message_id)
        await update.message.reply_text(
            f"📝 *Записать в дневник?*\n\n"
            f"• {name}: {grams}{unit} — {round(calories)} ккал\n"
            f"  Б {round(protein)}г | Ж {round(fat)}г | У {round(carbs)}г\n\n"
            f"_(сохраню в базу на 100г: {cal100} ккал | Б {pro100}г | Ж {fat100}г | У {carb100}г)_\n\n"
            f"Подтверждаешь?", parse_mode="Markdown"
        )

    # ── meal_session: новый приём пищи с несколькими продуктами ──
    elif msg_type == "multi_meal":
        meals = data.get("meals", [])
        meals = [m for m in meals if m.get("items")]  # убираем пустые (ужин без продуктов)
        if not meals:
            await update.message.reply_text("Не понял состав. Назови продукты и граммы.")
            return
        await update.message.reply_text("⏳ Ищу КБЖУ для всех продуктов...")
        resolved_meals = []
        for meal in meals:
            resolved_items = []
            for item in meal.get("items", []):
                try:
                    resolved_items.append(await _resolve_item(item))
                except Exception as e:
                    logger.error(f"resolve {item}: {e}")
            if resolved_items:
                resolved_meals.append({
                    "meal_type": meal.get("meal_type", "приём пищи"),
                    "date": meal.get("date", today_str()),
                    "items": resolved_items,
                })
        if not resolved_meals:
            await update.message.reply_text("Не смог найти КБЖУ. Попробуй ещё раз.")
            return
        # Показываем все приёмы пищи
        lines = []
        total_cal = total_p = total_f = total_c = 0
        for meal in resolved_meals:
            lines.append(fmt_meal_session({"meal_type": meal["meal_type"], "items": meal["items"]}, show_footer=False))
            total_cal += sum(it.get("calories", 0) for it in meal["items"])
            total_p   += sum(it.get("protein",  0) for it in meal["items"])
            total_f   += sum(it.get("fat",      0) for it in meal["items"])
            total_c   += sum(it.get("carbs",    0) for it in meal["items"])
        lines.append(f"\n📊 *Итого за день:* {round(total_cal)} ккал | Б {round(total_p)}г | Ж {round(total_f)}г | У {round(total_c)}г")
        lines.append("\nВсё верно? Сохраняю?\n*(«да» — записываю / «нет» — отмена)*")
        save_pending("multi_meal", {"meals": resolved_meals}, update.message.message_id)
        await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")

    elif msg_type == "meal_session":
        raw_items = data.get("items", [])
        meal_type = data.get("meal_type", "приём пищи")
        if not raw_items:
            await update.message.reply_text("Не понял состав. Назови продукты и граммы.")
            return
        await update.message.reply_text(f"⏳ Ищу КБЖУ для всех продуктов...")
        resolved = []
        for item in raw_items:
            try:
                resolved.append(await _resolve_item(item))
            except Exception as e:
                logger.error(f"resolve {item}: {e}")
        if not resolved:
            await update.message.reply_text("Не смог найти КБЖУ. Попробуй ещё раз.")
            return
        session_data = {"meal_type": meal_type, "items": resolved}
        save_pending("meal_session", session_data, update.message.message_id)
        await update.message.reply_text(fmt_meal_session(session_data), parse_mode="Markdown")

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
        d = data if isinstance(data, dict) else {}
        q_date     = d.get("date")
        from_date  = d.get("from_date")
        to_date    = d.get("to_date")
        q_meal     = d.get("meal_type")
        if from_date and to_date:
            await update.message.reply_text(get_food_period(from_date, to_date), parse_mode="Markdown")
        elif q_date:
            await update.message.reply_text(get_food_history(q_date, q_meal), parse_mode="Markdown")
        else:
            await update.message.reply_text(get_food_stats_today(), parse_mode="Markdown")

    elif msg_type == "query_finances":
        d = data if isinstance(data, dict) else {}
        from_date = d.get("from_date", today_str())
        to_date   = d.get("to_date",   today_str())
        subtype   = d.get("subtype", "all")
        if d.get("period") == "week":
            from_date = (date.today() - timedelta(days=date.today().weekday())).isoformat()
        elif d.get("period") == "month":
            from_date = date.today().replace(day=1).isoformat()
        await update.message.reply_text(get_finance_history(from_date, to_date, subtype), parse_mode="Markdown")

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

    elif msg_type == "edit_food_log":
        target       = data.get("target", "last")
        edit_product = data.get("product_name")
        new_grams    = data.get("new_grams")
        new_meal     = data.get("new_meal_type")
        td           = today_str()

        # Находим запись для правки: по продукту или последнюю за сегодня
        if target == "product" and edit_product:
            rows = supabase.table("food_log").select("*").eq("date", td) \
                .ilike("product_name", f"%{edit_product}%").order("created_at", desc=True).limit(1).execute().data or []
        else:
            rows = supabase.table("food_log").select("*").eq("date", td) \
                .order("created_at", desc=True).limit(1).execute().data or []

        if not rows:
            await update.message.reply_text("Нет записи для исправления. Сначала запиши еду.")
            return

        row = rows[0]
        updates = {}
        changes = []

        if new_grams is not None:
            old_g = float(row.get("grams") or 100)
            ng    = float(new_grams)
            ratio = ng / old_g if old_g else 1
            updates["grams"]    = ng
            updates["calories"] = round((row.get("calories") or 0) * ratio, 1)
            updates["protein"]  = round((row.get("protein")  or 0) * ratio, 1)
            updates["fat"]      = round((row.get("fat")      or 0) * ratio, 1)
            updates["carbs"]    = round((row.get("carbs")    or 0) * ratio, 1)
            unit = row.get("unit", "г")
            changes.append(f"{old_g:g}{unit} → {ng:g}{unit}")

        if new_meal:
            updates["meal_type"] = new_meal
            changes.append(f"приём → {new_meal}")

        if not updates:
            await update.message.reply_text("Не понял что исправить. Напиши например «поправь последнюю на 250г».")
            return

        supabase.table("food_log").update(updates).eq("id", row["id"]).execute()
        new_cal = round(updates.get("calories", row.get("calories") or 0))
        reply = f"✏️ Исправлено: *{row['product_name']}*\n" + "\n".join(f"• {c}" for c in changes) + f"\n→ {new_cal} ккал"
        await update.message.reply_text(reply, parse_mode="Markdown")
        remember_msg(chat_id, "bot", reply)

    elif msg_type == "delete_food_log":
        del_date     = data.get("date") or today_str()
        del_meal     = data.get("meal_type")
        del_product  = data.get("product_name")
        td           = today_str()

        date_label = "вчера" if del_date != td else "сегодня"

        # Берём ВСЕ записи за этот день
        all_day = supabase.table("food_log").select("*").eq("date", del_date).execute().data or []

        if not all_day:
            await update.message.reply_text(f"За {date_label} ({del_date}) вообще нет записей о еде.")
            return

        # Фильтруем по приёму пищи и продукту, но мягко
        rows = list(all_day)
        used_meal_filter = False
        used_prod_filter = False
        if del_meal:
            filtered = [r for r in rows if (r.get("meal_type") or "").lower() == del_meal.lower()]
            if filtered:
                rows = filtered
                used_meal_filter = True
        if del_product:
            filtered = [r for r in rows if del_product.lower() in (r.get("product_name") or "").lower()]
            if filtered:
                rows = filtered
                used_prod_filter = True

        # Поясняем если точного совпадения не было, но что-то за день есть
        note = ""
        if del_meal and not used_meal_filter:
            note = f"_(записи «{del_meal}» нет, но за {date_label} есть это:)_\n\n"
        elif del_product and not used_prod_filter:
            note = f"_(«{del_product}» не нашёл, но за {date_label} есть это:)_\n\n"

        lines = [f"🗑 *Удалить за {date_label}?*\n", note] if note else [f"🗑 *Удалить за {date_label}?*\n"]
        for r in rows:
            unit = r.get("unit", "г")
            mt   = f"[{r.get('meal_type')}] " if r.get("meal_type") else ""
            lines.append(f"• {mt}{r['product_name']}: {r['grams']}{unit} — {round(r.get('calories') or 0)} ккал")
        total = round(sum(r.get("calories") or 0 for r in rows))
        lines.append(f"\n*Итого: {total} ккал*")
        lines.append("\n*«да»* — удаляю | *«нет»* — отмена")

        ids = [r["id"] for r in rows]
        save_pending("delete_food_log", {"ids": ids, "total_cal": total}, update.message.message_id)
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    else:
        # general_chat
        stats = get_today_stats()
        reply = await gpt(
            f"Ты личный ассистент-трекер. Отвечаешь ТОЛЬКО по темам: еда, калории, вес, тренировки, финансы.\n"
            f"Данные пользователя сегодня:\n{stats}\n\n"
            f"СТРОГИЕ ПРАВИЛА ОТВЕТА:\n"
            f"1. Максимум 3-5 строк. Никаких длинных текстов.\n"
            f"2. ЗАПРЕЩЕНО использовать: ### заголовки, ## заголовки, таблицы (|), горизонтальные линии (---)\n"
            f"3. Можно использовать только: *жирный*, обычный текст, bullet points (•)\n"
            f"4. Если вопрос не по теме (новости, погода, политика и т.д.) — ответь одной строкой: Я твой личный трекер. Спроси меня про еду, вес или финансы.\n"
            f"5. Никаких ссылок, никаких смайликов",
            text
        )
        # Чистим markdown который Telegram не поддерживает
        import re
        reply = re.sub(r'#{1,6}\s*', '', reply)       # убираем ## заголовки
        reply = re.sub(r'\|.*\|', '', reply)           # убираем таблицы
        reply = re.sub(r'\n-{3,}\n', '\n', reply)      # убираем ---
        reply = re.sub(r'\n{3,}', '\n\n', reply)       # убираем лишние пустые строки
        reply = reply.strip()
        await update.message.reply_text(reply, parse_mode="Markdown")

# ── Handlers ─────────────────────────────────────────────────────────

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = await context.bot.get_file(update.message.voice.file_id)
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        await file.download_to_drive(tmp_path)
        text = await transcribe_voice(tmp_path)
    except Exception as e:
        logger.error(f"voice transcribe error: {e}")
        await notify_error(context.bot, f"voice: {e}")
        await update.message.reply_text("⚠️ Не смог распознать голос. Попробуй ещё раз или напиши текстом.")
        return
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    if not text or not text.strip():
        await update.message.reply_text("🎙 Не расслышал. Скажи ещё раз чуть чётче.")
        return

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
            # Закрываем утренний запрос взвешивания
            clear_weigh_pending("done")
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

    # Пробуем как фото еды на тарелке
    try:
        raw = await gpt_vision(image_bytes,
            "На изображении блюдо/еда на тарелке. Определи каждый продукт, прикинь вес порции в граммах "
            "и КБЖУ для этой порции (не на 100г, а для показанного количества). "
            "Верни ТОЛЬКО JSON: {\"items\":[{\"name\":\"...\",\"grams\":число,\"calories\":число,\"protein\":число,\"fat\":число,\"carbs\":число}]}"
        )
        d = parse_json(raw)
        items = d.get("items") or []
        if items:
            resolved = []
            for it in items:
                resolved.append({
                    "product_name": it.get("name", "продукт"),
                    "grams":    float(it.get("grams") or 100),
                    "unit":     "г",
                    "category": infer_category(it.get("name",""), "г"),
                    "calories": round(float(it.get("calories") or 0), 1),
                    "protein":  round(float(it.get("protein")  or 0), 1),
                    "fat":      round(float(it.get("fat")      or 0), 1),
                    "carbs":    round(float(it.get("carbs")    or 0), 1),
                    "auto_estimated": True,
                })
            save_pending("food_log", {"items": resolved, "date": today_str(), "meal_type": None},
                         update.message.message_id)
            await update.message.reply_text(
                "📷 *Распознал по фото* _(оценка ИИ, можешь поправить граммовку)_\n\n"
                + fmt_food(resolved), parse_mode="Markdown")
            return
    except Exception as e:
        logger.error(f"food image: {e}")

    await update.message.reply_text("Не смог распознать. Отправь скриншот весов, фото этикетки или фото еды.")

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

async def send_morning_message(bot: Bot):
    chat_id = MY_CHAT_ID or os.getenv("MY_CHAT_ID")
    if not chat_id:
        return

    today = today_str()

    # Не отправляем если уже слали сегодня
    existing = supabase.table("pending_actions").select("id") \
        .eq("type", "weigh_morning") \
        .gte("created_at", f"{today}T00:00:00").execute()
    if existing.data:
        return

    now = now_local()
    day_ru = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"][now.weekday()]

    try:
        text = await gpt(
            "Ты личный ИИ-ассистент. Напиши утреннее сообщение для пользователя по имени Дима. "
            "Сообщение должно: 1) пожелать доброго утра тепло и по-дружески, "
            "2) дать короткую мотивацию на продуктивный день (каждый раз разную — цитата, мысль или просто слова), "
            "3) напомнить что бот ждёт задачи на сегодня, записи продуктов, расходов и доходов, "
            "4) попросить прислать скриншот взвешивания. "
            "Пиши на русском, без звёздочек и форматирования. 4-6 предложений. "
            "Каждый раз формулировки должны быть немного разными.",
            f"Сегодня {day_ru}, {now.strftime('%d.%m.%Y')}. Напиши утреннее сообщение для Димы."
        )
    except Exception as e:
        logger.error(f"morning message gpt error: {e}")
        text = (
            f"Доброе утро, Дима! Желаю тебе продуктивного {day_ru}а и хорошего настроения.\n\n"
            "Жду от тебя задачи на сегодня, записи продуктов, расходов и доходов.\n\n"
            "Также жду скриншот взвешивания — просто пришли его сюда 📊"
        )

    await bot.send_message(chat_id=chat_id, text=f"🌅 {text}")

    # Запоминаем что ждём скриншот
    supabase.table("pending_actions").insert({
        "type": "weigh_morning",
        "data": {"date": today},
        "status": "pending",
    }).execute()

def _fmt_food_drink_day(rows: list, header: str) -> str:
    """Форматирует список продуктов дня с разделением еда/напитки."""
    if not rows:
        return f"{header}\nНичего не записано."

    food_rows  = [r for r in rows if (r.get("category") or infer_category(r.get("product_name",""), r.get("unit","г"))) != "напиток"]
    drink_rows = [r for r in rows if (r.get("category") or infer_category(r.get("product_name",""), r.get("unit","г"))) == "напиток"]

    lines = [header, ""]
    if food_rows:
        lines.append("🍽 *Еда:*")
        for r in food_rows:
            unit = r.get("unit", "г")
            lines.append(f"  • {r.get('product_name')}: {r.get('grams')}{unit} — {round(r.get('calories') or 0)} ккал")
        fc = round(sum(r.get("calories") or 0 for r in food_rows))
        fp = round(sum(r.get("protein")  or 0 for r in food_rows))
        ff = round(sum(r.get("fat")      or 0 for r in food_rows))
        fcarb = round(sum(r.get("carbs") or 0 for r in food_rows))
        lines.append(f"  → {fc} ккал | Б {fp}г | Ж {ff}г | У {fcarb}г")
    if drink_rows:
        if food_rows: lines.append("")
        lines.append("🥤 *Напитки:*")
        for r in drink_rows:
            unit = r.get("unit", "мл")
            lines.append(f"  • {r.get('product_name')}: {r.get('grams')}{unit} — {round(r.get('calories') or 0)} ккал")
        dc = round(sum(r.get("calories") or 0 for r in drink_rows))
        lines.append(f"  → {dc} ккал")

    total_cal = round(sum(r.get("calories") or 0 for r in rows))
    total_p   = round(sum(r.get("protein")  or 0 for r in rows))
    total_f   = round(sum(r.get("fat")      or 0 for r in rows))
    total_c   = round(sum(r.get("carbs")    or 0 for r in rows))
    lines.append(f"\n📊 *Итого:* {total_cal} ккал | Б {total_p}г | Ж {total_f}г | У {total_c}г")
    return "\n".join(lines)

def get_food_history(query_date: str, meal_type: str = None) -> str:
    """Питание за конкретный день (с фильтром по приёму пищи)."""
    all_food = supabase.table("food_log").select("*").eq("date", query_date).execute().data or []
    food = all_food
    fallback_note = ""
    if meal_type:
        filtered = [r for r in all_food if (r.get("meal_type") or "").lower() == meal_type.lower()]
        if filtered:
            food = filtered
        elif all_food:
            # Нет записей с этим приёмом, но за день что-то есть — показываем всё
            food = all_food
            meal_type = None
            fallback_note = "_(отдельной записи на этот приём нет, вот всё за день:)_\n"

    try:
        from datetime import datetime as _dt
        d_label = _dt.strptime(query_date, "%Y-%m-%d").strftime("%d.%m.%Y")
    except:
        d_label = query_date

    mt_label = f" — {meal_type}" if meal_type else ""
    header = f"🍽 *Питание за {d_label}{mt_label}*"
    if fallback_note:
        header += f"\n{fallback_note}"

    if not meal_type:
        # Группируем по приёму пищи
        by_meal: dict = {}
        for r in food:
            mt = r.get("meal_type") or "без приёма"
            by_meal.setdefault(mt, []).append(r)
        if not food:
            return f"{header}\nНичего не записано."
        lines = [header, ""]
        for mt, items in by_meal.items():
            lines.append(f"*{mt.capitalize()}:*")
            food_i  = [r for r in items if (r.get("category") or infer_category(r.get("product_name",""), r.get("unit","г"))) != "напиток"]
            drink_i = [r for r in items if (r.get("category") or infer_category(r.get("product_name",""), r.get("unit","г"))) == "напиток"]
            for r in food_i:
                unit = r.get("unit", "г")
                lines.append(f"  🍽 {r.get('product_name')}: {r.get('grams')}{unit} — {round(r.get('calories') or 0)} ккал")
            for r in drink_i:
                unit = r.get("unit", "мл")
                lines.append(f"  🥤 {r.get('product_name')}: {r.get('grams')}{unit} — {round(r.get('calories') or 0)} ккал")
            sub = round(sum(r.get("calories") or 0 for r in items))
            lines.append(f"  → {sub} ккал\n")
        total_cal = round(sum(r.get("calories") or 0 for r in food))
        total_p   = round(sum(r.get("protein")  or 0 for r in food))
        total_f   = round(sum(r.get("fat")      or 0 for r in food))
        total_c   = round(sum(r.get("carbs")    or 0 for r in food))
        lines.append(f"📊 *Итого за день:* {total_cal} ккал | Б {total_p}г | Ж {total_f}г | У {total_c}г")
        return "\n".join(lines)

    return _fmt_food_drink_day(food, header)

def get_food_period(from_date: str, to_date: str) -> str:
    """Питание за период."""
    food = supabase.table("food_log").select("*") \
        .gte("date", from_date).lte("date", to_date).execute().data or []
    if not food:
        return f"За период {from_date} — {to_date} ничего не записано."

    total_cal = sum(r.get("calories") or 0 for r in food)
    total_p   = sum(r.get("protein")  or 0 for r in food)
    total_f   = sum(r.get("fat")      or 0 for r in food)
    total_c   = sum(r.get("carbs")    or 0 for r in food)
    days_set  = set(r["date"] for r in food)
    n_days    = len(days_set)

    lines = [f"🍽 *Питание {from_date} — {to_date}*\n",
             f"Дней с записями: {n_days}",
             f"Итого: {round(total_cal)} ккал | Б {round(total_p)}г | Ж {round(total_f)}г | У {round(total_c)}г",
             f"В среднем/день: {round(total_cal/n_days)} ккал | Б {round(total_p/n_days)}г | Ж {round(total_f/n_days)}г | У {round(total_c/n_days)}г"]
    return "\n".join(lines)

def get_finance_history(from_date: str, to_date: str, subtype: str = "all") -> str:
    """Финансы за период с фильтром по типу (income/expense/all)."""
    fin = supabase.table("finances").select("*") \
        .gte("date", from_date).lte("date", to_date).execute().data or []
    if subtype == "income":
        fin = [r for r in fin if r["type"] == "income"]
    elif subtype == "expense":
        fin = [r for r in fin if r["type"] == "expense"]

    try:
        from datetime import datetime as _dt
        fd = _dt.strptime(from_date, "%Y-%m-%d").strftime("%d.%m")
        td = _dt.strptime(to_date,   "%Y-%m-%d").strftime("%d.%m")
        period_label = fd if from_date == to_date else f"{fd} — {td}"
    except:
        period_label = f"{from_date} — {to_date}"

    if not fin:
        sub_label = {"income": "доходов", "expense": "расходов"}.get(subtype, "транзакций")
        return f"За {period_label} нет {sub_label}."

    income  = sum(r["amount"] for r in fin if r["type"] == "income")
    expense = sum(r["amount"] for r in fin if r["type"] == "expense")
    cur     = fin[0].get("currency", "UAH")

    lines = [f"💰 *Финансы за {period_label}*\n"]
    if subtype in ("all", "income") and income:
        lines.append(f"Доходы: +{income} {cur}")
        for r in [x for x in fin if x["type"] == "income"]:
            lines.append(f"  • {r.get('description', r.get('category',''))} — +{r['amount']} {cur}")
    if subtype in ("all", "expense") and expense:
        lines.append(f"Расходы: -{expense} {cur}")
        cats: dict = {}
        for r in [x for x in fin if x["type"] == "expense"]:
            cat = r.get("store_name") or r.get("category") or "прочее"
            cats[cat] = cats.get(cat, 0) + r["amount"]
        for cat, amt in sorted(cats.items(), key=lambda x: -x[1]):
            lines.append(f"  • {cat}: {amt} {cur}")
    if subtype == "all":
        lines.append(f"\nБаланс: {income - expense:+.0f} {cur}")
    return "\n".join(lines)

async def send_daily_summary(bot: Bot):
    chat_id = MY_CHAT_ID or os.getenv("MY_CHAT_ID")
    if not chat_id:
        return
    try:
        summary = get_today_stats()
        await bot.send_message(chat_id=chat_id, text=f"🌙 *Вечерняя сводка*\n\n{summary}", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"daily summary error: {e}")

async def send_weekly_summary(bot: Bot):
    """Воскресенье 22:00 — сводка за неделю."""
    chat_id = MY_CHAT_ID or os.getenv("MY_CHAT_ID")
    if not chat_id:
        return
    try:
        now  = now_local()
        week_end   = now.strftime("%Y-%m-%d")
        week_start = (now - timedelta(days=6)).strftime("%Y-%m-%d")

        food   = supabase.table("food_log").select("*").gte("date", week_start).lte("date", week_end).execute().data or []
        bodies = supabase.table("body_measurements").select("date,weight").gte("date", week_start).order("date").execute().data or []
        fin    = supabase.table("finances").select("*").gte("date", week_start).lte("date", week_end).execute().data or []
        works  = supabase.table("workouts").select("*").gte("date", week_start).execute().data or []

        total_cal = sum(r.get("calories") or 0 for r in food)
        total_p   = sum(r.get("protein")  or 0 for r in food)
        total_f   = sum(r.get("fat")      or 0 for r in food)
        total_c   = sum(r.get("carbs")    or 0 for r in food)
        n_days    = max(len(set(r["date"] for r in food)), 1)
        income    = sum(r["amount"] for r in fin if r["type"] == "income")
        expense   = sum(r["amount"] for r in fin if r["type"] == "expense")
        cur       = fin[0].get("currency", "UAH") if fin else "UAH"

        lines = [f"📊 *Сводка за неделю ({week_start} — {week_end})*\n"]
        lines.append(f"🍽 *Питание* ({n_days} дней с записями):")
        lines.append(f"   Итого: {round(total_cal)} ккал | Б {round(total_p)}г | Ж {round(total_f)}г | У {round(total_c)}г")
        lines.append(f"   Среднее/день: {round(total_cal/n_days)} ккал | Б {round(total_p/n_days)}г | Ж {round(total_f/n_days)}г | У {round(total_c/n_days)}г")

        if bodies:
            w_start = bodies[0]["weight"]
            w_end   = bodies[-1]["weight"]
            diff    = round(w_end - w_start, 1)
            arrow   = "↑" if diff > 0 else "↓" if diff < 0 else "→"
            lines.append(f"⚖️ *Вес:* {w_start} → {w_end} кг ({arrow}{abs(diff)} кг)")

        if works:
            lines.append(f"💪 *Тренировок:* {len(works)} за неделю")

        if income or expense:
            lines.append(f"💰 *Финансы:* +{income} / -{expense} {cur} (баланс {income-expense:+.0f})")

        stats_text = "\n".join(lines)
        try:
            conclusion = await gpt(
                "Ты личный ИИ-ассистент. Дай короткий (2-3 предложения) вывод за неделю. "
                "Отметь успехи и дай один совет. Тепло, на русском.",
                stats_text
            )
            lines.append(f"\n💬 {conclusion}")
        except:
            pass

        await bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"weekly summary error: {e}")

async def send_monthly_summary(bot: Bot):
    """1-е число 15:00 — сводка за прошлый месяц."""
    chat_id = MY_CHAT_ID or os.getenv("MY_CHAT_ID")
    if not chat_id:
        return
    try:
        now       = now_local()
        # Прошлый месяц
        first_this = now.replace(day=1)
        last_prev  = first_this - timedelta(days=1)
        month_end  = last_prev.strftime("%Y-%m-%d")
        month_start = last_prev.replace(day=1).strftime("%Y-%m-%d")
        month_name  = last_prev.strftime("%B %Y")

        food   = supabase.table("food_log").select("*").gte("date", month_start).lte("date", month_end).execute().data or []
        bodies = supabase.table("body_measurements").select("date,weight,fat_percent").gte("date", month_start).order("date").execute().data or []
        fin    = supabase.table("finances").select("*").gte("date", month_start).lte("date", month_end).execute().data or []
        works  = supabase.table("workouts").select("*").gte("date", month_start).execute().data or []

        total_cal = sum(r.get("calories") or 0 for r in food)
        total_p   = sum(r.get("protein")  or 0 for r in food)
        total_f   = sum(r.get("fat")      or 0 for r in food)
        total_c   = sum(r.get("carbs")    or 0 for r in food)
        n_days    = max(len(set(r["date"] for r in food)), 1)
        income    = sum(r["amount"] for r in fin if r["type"] == "income")
        expense   = sum(r["amount"] for r in fin if r["type"] == "expense")
        cur       = fin[0].get("currency", "UAH") if fin else "UAH"

        lines = [f"📅 *Итог месяца — {month_name}*\n"]
        lines.append(f"🍽 *Питание* ({n_days} дней):")
        lines.append(f"   Итого: {round(total_cal)} ккал | Б {round(total_p)}г | Ж {round(total_f)}г | У {round(total_c)}г")
        lines.append(f"   Среднее/день: {round(total_cal/n_days)} ккал | Б {round(total_p/n_days)}г | Ж {round(total_f/n_days)}г | У {round(total_c/n_days)}г")

        if bodies:
            w_start = bodies[0]["weight"]
            w_end   = bodies[-1]["weight"]
            diff    = round(w_end - w_start, 1)
            arrow   = "↑" if diff > 0 else "↓" if diff < 0 else "→"
            f_start = bodies[0].get("fat_percent", "?")
            f_end   = bodies[-1].get("fat_percent", "?")
            lines.append(f"⚖️ *Вес:* {w_start} → {w_end} кг ({arrow}{abs(diff)} кг)")
            lines.append(f"   Жир: {f_start}% → {f_end}%")

        if works:
            lines.append(f"💪 *Тренировок:* {len(works)} за месяц")

        if income or expense:
            lines.append(f"💰 *Финансы:* +{income} / -{expense} {cur} (баланс {income-expense:+.0f})")

        stats_text = "\n".join(lines)
        try:
            conclusion = await gpt(
                "Ты личный ИИ-ассистент. Дай развёрнутый (3-5 предложений) вывод за месяц. "
                "Оцени прогресс, отметь достижения и дай конкретный совет на следующий месяц. "
                "Тепло и конструктивно, на русском.",
                stats_text
            )
            lines.append(f"\n💬 {conclusion}")
        except:
            pass

        await bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"monthly summary error: {e}")

# ── Main ─────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    scheduler = AsyncIOScheduler(timezone=TZ)
    scheduler.add_job(check_reminders,      "interval", minutes=1,                          args=[app.bot])
    scheduler.add_job(send_morning_message, "cron", hour=10, minute=0,                      args=[app.bot])
    scheduler.add_job(send_daily_summary,   "cron", hour=23, minute=55,                     args=[app.bot])
    scheduler.add_job(send_weekly_summary,  "cron", day_of_week="sun", hour=22, minute=0,   args=[app.bot])
    scheduler.add_job(send_monthly_summary, "cron", day=1,              hour=15, minute=0,   args=[app.bot])
    scheduler.start()

    # Загружаем кэш продуктов при старте
    load_products_cache()

    logger.info("Bot started")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
