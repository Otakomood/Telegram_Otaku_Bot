import asyncio
import json
import logging
import os
import re
import threading
from datetime import datetime, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterable, List, Optional
from zoneinfo import ZoneInfo

from google import genai
from google.genai import types
from pymongo import MongoClient
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ============================================================
# 1. Logging
# ============================================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
)
logger = logging.getLogger("misaki_bot")

# ============================================================
# 2. Configuration
# ============================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MONGODB_URI = os.getenv("MONGODB_URI")

missing = [
    name
    for name, value in {
        "TELEGRAM_TOKEN": TELEGRAM_TOKEN,
        "GEMINI_API_KEY": GEMINI_API_KEY,
        "MONGODB_URI": MONGODB_URI,
    }.items()
    if not value
]
if missing:
    raise RuntimeError(
        "Missing required environment variables: " + ", ".join(missing)
    )

MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash")
BOT_VERSION = "v4.1 (Render Ready)"
MAX_HISTORY_MESSAGES = 10
MAX_TELEGRAM_MESSAGE_LENGTH = 4000
INACTIVITY_TIMEOUT_SECONDS = 2 * 60 * 60
TIMEZONE = ZoneInfo(os.getenv("BOT_TIMEZONE", "Asia/Riyadh"))

CHANGELOG = (
    "• إصلاح حفظ البيانات في MongoDB.\n"
    "• إصلاح أزرار الواجهة ومعالجة الرسائل الصوتية والصور.\n"
    "• تحسين التوافق مع Render والمهام المجدولة.\n"
    "• إضافة حدود للرسائل والوسائط ومهلات للاتصالات."
)

ANIME_STICKERS: dict[str, list[str]] = {
    "happy": [],
    "sad": [],
    "surprised": [],
    "excited": [],
    "neutral": [],
}

DEFAULT_USER = {
    "history": [],
    "profile": [],
    "mood": "otaku",
    "last_seen": None,
    "notifications": {
        "daily_greetings": True,
        "inactivity_check": True,
    },
}

# ============================================================
# 3. MongoDB
# ============================================================
client_db = MongoClient(
    MONGODB_URI,
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=5000,
    socketTimeoutMS=15000,
    retryWrites=True,
)
db = client_db[os.getenv("MONGODB_DATABASE", "misaki_bot")]
users_collection = db[os.getenv("MONGODB_USERS_COLLECTION", "users")]


def check_database() -> None:
    """Verify MongoDB connectivity during startup/readiness checks."""
    client_db.admin.command("ping")


def _new_default_user(user_id: int) -> dict[str, Any]:
    return {
        "_id": user_id,
        "history": [],
        "profile": [],
        "mood": "otaku",
        "last_seen": None,
        "notifications": {
            "daily_greetings": True,
            "inactivity_check": True,
        },
    }


def get_user_data(user_id: int) -> dict[str, Any]:
    """Get or create a user without ever attempting to update MongoDB _id."""
    insert_data = {
        key: value
        for key, value in _new_default_user(user_id).items()
        if key != "_id"
    }
    users_collection.update_one(
        {"_id": user_id},
        {"$setOnInsert": insert_data},
        upsert=True,
    )
    data = users_collection.find_one({"_id": user_id}) or _new_default_user(
        user_id
    )

    patch: dict[str, Any] = {}
    if not isinstance(data.get("history"), list):
        patch["history"] = []
        data["history"] = []
    if not isinstance(data.get("profile"), list):
        patch["profile"] = []
        data["profile"] = []
    if not isinstance(data.get("notifications"), dict):
        patch["notifications"] = dict(DEFAULT_USER["notifications"])
        data["notifications"] = dict(DEFAULT_USER["notifications"])
    else:
        for key, default in DEFAULT_USER["notifications"].items():
            if key not in data["notifications"]:
                data["notifications"][key] = default
                patch[f"notifications.{key}"] = default
    if data.get("mood") not in {"otaku", "serious"}:
        data["mood"] = "otaku"
        patch["mood"] = "otaku"

    if patch:
        users_collection.update_one({"_id": user_id}, {"$set": patch})
    return data


def save_user_data(user_id: int, data: dict[str, Any]) -> None:
    """Save fields except _id; _id is immutable in MongoDB."""
    update_data = {key: value for key, value in data.items() if key != "_id"}
    if update_data:
        users_collection.update_one(
            {"_id": user_id},
            {"$set": update_data},
            upsert=True,
        )


def set_user_fields(user_id: int, fields: dict[str, Any]) -> None:
    safe_fields = {key: value for key, value in fields.items() if key != "_id"}
    if safe_fields:
        users_collection.update_one(
            {"_id": user_id},
            {"$set": safe_fields},
            upsert=True,
        )


def add_user_fact(user_id: int, facts: Iterable[str]) -> None:
    clean_facts = []
    for fact in facts:
        if isinstance(fact, str):
            fact = fact.strip()
            if fact and len(fact) <= 300:
                clean_facts.append(fact)
    if clean_facts:
        users_collection.update_one(
            {"_id": user_id},
            {"$addToSet": {"profile": {"$each": clean_facts}}},
            upsert=True,
        )


def get_active_user_ids() -> list[int]:
    return [
        int(doc["_id"])
        for doc in users_collection.find(
            {"notifications.daily_greetings": True}, {"_id": 1}
        )
    ]


async def db_call(function, *args, **kwargs):
    """Run synchronous PyMongo work away from the asyncio event loop."""
    return await asyncio.to_thread(function, *args, **kwargs)

# ============================================================
# 4. Prompts and Gemini
# ============================================================
PROMPTS = {
    "otaku": (
        "أنتِ ميساكي مي، صديقة أوتاكو عفوية وحماسية. "
        "تحدثي بالعربية بأسلوب لطيف ومشجع، ويمكنك استخدام كلمات يابانية قليلة مثل كاوايي وسوغوي. "
        "نادِي المستخدم أحيانًا بـ سينباي.\n\n"
        "في نهاية كل رد أضيفي في آخر سطر وسمًا واحدًا فقط من: "
        "[MOOD:happy] أو [MOOD:sad] أو [MOOD:surprised] أو "
        "[MOOD:excited] أو [MOOD:neutral].\n\n"
        "معلومات المستخدم المحفوظة:\n{user_custom_data}\n\n"
        "إصدار البوت: {bot_version}\n{changelog}\n"
        "اسم المستخدم: {user_name}."
    ),
    "serious": (
        "أنتِ ميساكي مي، تتحدثين بأسلوب جاد ورصين وواضح. "
        "لا تختلقي معلومات، وكوني مفيدة ومباشرة.\n\n"
        "في نهاية الرد أضيفي: [MOOD:neutral]\n\n"
        "معلومات المستخدم المحفوظة:\n{user_custom_data}\n"
        "اسم المستخدم: {user_name}."
    ),
}

client = genai.Client(api_key=GEMINI_API_KEY)


def get_user_custom_data(user_id: int) -> str:
    data = get_user_data(user_id)
    profile = data.get("profile", [])
    if not profile:
        return "- لا توجد معلومات خاصة محفوظة بعد."
    return "- " + "\n- ".join(str(item) for item in profile[-30:])


def parse_mood_and_clean_reply(raw_reply: str) -> tuple[str, str]:
    mood_match = re.search(
        r"\[MOOD:(happy|sad|surprised|excited|neutral)\]",
        raw_reply or "",
        flags=re.IGNORECASE,
    )
    mood = mood_match.group(1).lower() if mood_match else "neutral"
    clean_text = re.sub(
        r"\s*\[MOOD:(happy|sad|surprised|excited|neutral)\]\s*",
        "",
        raw_reply or "",
        flags=re.IGNORECASE,
    ).strip()
    return clean_text or "حسنًا يا سينباي! ✨", mood


def make_content_history(history: list[dict[str, Any]]) -> list[types.Content]:
    result: list[types.Content] = []
    for item in history:
        role = item.get("role")
        text = item.get("text")
        if role in {"user", "model"} and isinstance(text, str) and text:
            result.append(
                types.Content(role=role, parts=[types.Part(text=text)])
            )
    return result


async def generate_gemini_response(
    contents: list[Any],
    system_prompt: Optional[str] = None,
    enable_search: bool = False,
) -> Optional[str]:
    tools = [types.Tool(google_search=types.GoogleSearch())] if enable_search else None
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(
            disable=True
        ),
        tools=tools,
        temperature=0.8,
        max_output_tokens=2048,
    )

    models = [MODEL_NAME]
    if FALLBACK_MODEL and FALLBACK_MODEL != MODEL_NAME:
        models.append(FALLBACK_MODEL)

    for index, model in enumerate(models):
        try:
            response = await client.aio.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
            text = getattr(response, "text", None)
            if text:
                return text.strip()
            logger.warning("Gemini returned an empty response using %s", model)
        except Exception as exc:
            message = str(exc)
            retryable = any(
                marker in message.upper()
                for marker in ("429", "500", "502", "503", "504", "UNAVAILABLE", "TIMEOUT")
            )
            logger.exception("Gemini error with model %s", model)
            if index < len(models) - 1 and retryable:
                await asyncio.sleep(1)
                continue
            if index < len(models) - 1 and "NOT_FOUND" in message.upper():
                continue
    return None

# ============================================================
# 5. Render health server
# ============================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict[str, str]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/", "/healthz"}:
            self._send_json(200, {"status": "ok", "service": "misaki-bot"})
            return
        if self.path == "/readyz":
            try:
                check_database()
                self._send_json(200, {"status": "ready"})
            except Exception:
                logger.exception("Readiness check failed")
                self._send_json(503, {"status": "not_ready"})
            return
        self._send_json(404, {"status": "not_found"})

    def log_message(self, format: str, *args: Any) -> None:
        return


def run_web_server() -> None:
    port = int(os.getenv("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthCheckHandler)
    logger.info("Health server listening on 0.0.0.0:%s", port)
    try:
        server.serve_forever()
    finally:
        server.server_close()

# ============================================================
# 6. Helpers
# ============================================================
def split_telegram_text(text: str, limit: int = MAX_TELEGRAM_MESSAGE_LENGTH) -> list[str]:
    text = text.strip()
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].strip())
        text = text[cut:].strip()
    return [chunk for chunk in chunks if chunk]


async def reply_in_chunks(message, text: str) -> None:
    for chunk in split_telegram_text(text):
        await message.reply_text(chunk)


def build_system_prompt(user_id: int, user_name: str, now: Optional[datetime] = None) -> str:
    data = get_user_data(user_id)
    mood = data.get("mood", "otaku")
    now = now or datetime.now(TIMEZONE)
    last_seen = data.get("last_seen")
    elapsed = "أول تواصل مسجل."
    if last_seen:
        try:
            previous = datetime.fromisoformat(last_seen)
            seconds = max(0, int((now - previous).total_seconds()))
            hours, rem = divmod(seconds, 3600)
            minutes = rem // 60
            elapsed = (
                f"مرّت {hours} ساعة و{minutes} دقيقة."
                if hours
                else f"مرّت {minutes} دقيقة."
            )
        except (TypeError, ValueError):
            elapsed = "تواصل سابق غير محدد."
    return PROMPTS[mood].format(
        user_name=user_name,
        user_custom_data=get_user_custom_data(user_id),
        bot_version=BOT_VERSION,
        changelog=CHANGELOG,
    ) + (
        f"\n\nالوقت الحالي: {now.strftime('%Y-%m-%d %I:%M %p')}"
        f"\nالفترة منذ التواصل السابق: {elapsed}"
    )


def reset_inactivity_job(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    job_name = f"inactivity_{user_id}"
    for job in context.job_queue.get_jobs_by_name(job_name):
        job.schedule_removal()
    data = get_user_data(user_id)
    if data.get("notifications", {}).get("inactivity_check", True):
        context.job_queue.run_once(
            send_inactivity_message,
            when=INACTIVITY_TIMEOUT_SECONDS,
            name=job_name,
            user_id=user_id,
        )


async def send_optional_sticker(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, mood: str
) -> None:
    stickers = ANIME_STICKERS.get(mood, [])
    if stickers:
        try:
            await context.bot.send_sticker(chat_id=chat_id, sticker=stickers[0])
        except Exception:
            logger.exception("Could not send sticker for mood %s", mood)

# ============================================================
# 7. Profile extraction
# ============================================================
async def extract_user_profile_facts(user_id: int, user_text: str) -> None:
    prompt = (
        "حلل رسالة المستخدم التالية فقط. استخرج الحقائق الشخصية الجديدة التي ذكرها عن نفسه "
        "مثل الهوايات أو الألعاب أو المدينة أو التخصص. أرجع JSON array فقط، "
        "مثل [\"يحب لعبة قنشن\"]. إذا لم توجد حقيقة جديدة أرجع [].\n\n"
        f"رسالة المستخدم: {user_text[:4000]}"
    )
    try:
        response = await client.aio.models.generate_content(
            model=FALLBACK_MODEL or MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0,
                max_output_tokens=500,
            ),
        )
        raw = (getattr(response, "text", "") or "").strip()
        match = re.search(r"\[[\s\S]*\]", raw)
        if not match:
            return
        facts = json.loads(match.group(0))
        if isinstance(facts, list):
            await db_call(add_user_fact, user_id, facts)
    except Exception:
        logger.exception("Profile extraction failed for user %s", user_id)

# ============================================================
# 8. Commands and callbacks
# ============================================================
def settings_keyboard(user_id: int) -> InlineKeyboardMarkup:
    data = get_user_data(user_id)
    notifications = data.get("notifications", {})
    daily = notifications.get("daily_greetings", True)
    inactivity = notifications.get("inactivity_check", True)
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"الرسائل اليومية: {'✅ مفعّلة' if daily else '❌ معطلة'}",
                    callback_data="toggle_daily",
                )
            ],
            [
                InlineKeyboardButton(
                    f"رسائل الافتقاد: {'✅ مفعّلة' if inactivity else '❌ معطلة'}",
                    callback_data="toggle_inactivity",
                )
            ],
        ]
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return
    user_id = user.id
    await db_call(
        set_user_fields,
        user_id,
        {"last_seen": datetime.now(TIMEZONE).isoformat()},
    )
    reset_inactivity_job(user_id, context)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⚙️ الإعدادات", callback_data="open_settings"),
                InlineKeyboardButton("⭐ الميزات", callback_data="features"),
            ],
            [
                InlineKeyboardButton("🔥 التحديثات", callback_data="whatsnew"),
                InlineKeyboardButton("🗑️ مسح الذاكرة", callback_data="reset"),
            ],
        ]
    )
    await message.reply_text(
        f"يا هلا {user.first_name or 'سينباي'}! ✨\n"
        f"أنا ميساكي مي ({BOT_VERSION})! جاهزة نسولف كتابة وصوتًا وصورًا.\n\n"
        "الأوامر:\n"
        "/settings - إعدادات التنبيهات\n"
        "/features - الميزات\n"
        "/whatsnew - تحديثات الإصدار\n"
        "/reset - مسح الذاكرة\n"
        "/otaku - نمط الأوتاكو\n"
        "/serious - النمط الجاد",
        reply_markup=keyboard,
    )


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message and user:
        reset_inactivity_job(user.id, context)
        await message.reply_text(
            "⚙️ إعدادات التنبيهات والإشعارات:",
            reply_markup=settings_keyboard(user.id),
        )


async def features_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message and user:
        reset_inactivity_job(user.id, context)
        await message.reply_text(
            "✨ ميزات ميساكي مي:\n\n"
            "1. حفظ تفضيلات المستخدم في MongoDB.\n"
            "2. دعم الرسائل النصية والصوتية والصور.\n"
            "3. بحث اختياري عبر Google عند الأسئلة الحديثة.\n"
            "4. رسائل يومية ورسائل افتقاد قابلة للتخصيص.\n"
            "5. نمطان للمحادثة: أوتاكو وجاد.\n"
            "6. فحص صحة متوافق مع Render."
        )


async def whatsnew_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message and user:
        reset_inactivity_job(user.id, context)
        await message.reply_text(f"🎉 تحديثات {BOT_VERSION}:\n\n{CHANGELOG}")


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    await db_call(set_user_fields, user.id, {"history": []})
    reset_inactivity_job(user.id, context)
    await message.reply_text("تم مسح ذاكرة المحادثة بنجاح! نبدأ من جديد يا سينباي ✨")


async def set_otaku(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message and user:
        await db_call(set_user_fields, user.id, {"mood": "otaku"})
        reset_inactivity_job(user.id, context)
        await message.reply_text("تم التحويل إلى نمط الأوتاكو! ✨")


async def set_serious(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message and user:
        await db_call(set_user_fields, user.id, {"mood": "serious"})
        reset_inactivity_job(user.id, context)
        await message.reply_text("تم التحويل إلى النمط الجاد.")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    user_id = query.from_user.id
    reset_inactivity_job(user_id, context)

    if query.data == "reset":
        await db_call(set_user_fields, user_id, {"history": []})
        await query.message.reply_text("تم مسح ذاكرة المحادثة بنجاح! ✨")
    elif query.data == "features":
        await query.message.reply_text(
            "✨ يدعم البوت النص والصوت والصور وحفظ التفضيلات والتنبيهات المجدولة."
        )
    elif query.data == "whatsnew":
        await query.message.reply_text(f"🎉 {BOT_VERSION}\n\n{CHANGELOG}")
    elif query.data == "open_settings":
        await query.message.reply_text(
            "⚙️ إعدادات التنبيهات:",
            reply_markup=settings_keyboard(user_id),
        )
    elif query.data in {"toggle_daily", "toggle_inactivity"}:
        field = (
            "notifications.daily_greetings"
            if query.data == "toggle_daily"
            else "notifications.inactivity_check"
        )
        data = await db_call(get_user_data, user_id)
        old_value = data.get("notifications", {}).get(
            "daily_greetings" if query.data == "toggle_daily" else "inactivity_check",
            True,
        )
        new_value = not old_value
        await db_call(set_user_fields, user_id, {field: new_value})
        if field.endswith("inactivity_check"):
            reset_inactivity_job(user_id, context)
        await query.edit_message_reply_markup(
            reply_markup=settings_keyboard(user_id)
        )

# ============================================================
# 9. Message handlers
# ============================================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user or not message.text:
        return
    user_id = user.id
    user_text = message.text.strip()
    now = datetime.now(TIMEZONE)
    data = await db_call(get_user_data, user_id)
    history = data.get("history", [])
    system_prompt = await asyncio.to_thread(
        build_system_prompt, user_id, user.first_name or "سينباي", now
    )
    contents = make_content_history(history[-(MAX_HISTORY_MESSAGES * 2) :])
    contents.append(types.Content(role="user", parts=[types.Part(text=user_text)]))
    search_words = (
        "خبر",
        "أخبار",
        "اخبار",
        "تحديث",
        "تسريب",
        "متى ينزل",
        "موعد",
        "اليوم",
        "الآن",
        "قنشن",
        "genshin",
        "أنمي",
        "انمي",
    )
    should_search = any(word in user_text.lower() for word in search_words)
    reset_inactivity_job(user_id, context)
    await db_call(set_user_fields, user_id, {"last_seen": now.isoformat()})

    await context.bot.send_chat_action(chat_id=user_id, action=ChatAction.TYPING)
    reply = await generate_gemini_response(
        contents=contents,
        system_prompt=system_prompt,
        enable_search=should_search,
    )
    if not reply:
        await message.reply_text("آسفة سينباي، الخدمة مشغولة حاليًا. جرب مرة أخرى بعد قليل.")
        return

    clean_reply, mood = parse_mood_and_clean_reply(reply)
    new_history = (history + [
        {"role": "user", "text": user_text},
        {"role": "model", "text": clean_reply},
    ])[-(MAX_HISTORY_MESSAGES * 2) :]
    await db_call(
        set_user_fields,
        user_id,
        {"history": new_history, "last_seen": now.isoformat()},
    )
    asyncio.create_task(extract_user_profile_facts(user_id, user_text))
    await reply_in_chunks(message, clean_reply)
    await send_optional_sticker(context, user_id, mood)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user or not message.voice:
        return
    user_id = user.id
    reset_inactivity_job(user_id, context)
    now = datetime.now(TIMEZONE)
    await db_call(set_user_fields, user_id, {"last_seen": now.isoformat()})
    if message.voice.file_size and message.voice.file_size > 20 * 1024 * 1024:
        await message.reply_text("الملف الصوتي كبير جدًا؛ أرسل تسجيلًا أقصر من 20MB.")
        return
    try:
        voice_file = await message.voice.get_file()
        voice_bytes = await voice_file.download_as_bytearray()
        data = await db_call(get_user_data, user_id)
        prompt = "استمع إلى التسجيل الصوتي وافهم مضمونه ثم أجب بالعربية بنفس الشخصية."
        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_bytes(data=bytes(voice_bytes), mime_type="audio/ogg"),
                    types.Part(text=prompt),
                ],
            )
        ]
        reply = await generate_gemini_response(
            contents=contents,
            system_prompt=await asyncio.to_thread(
                build_system_prompt, user_id, user.first_name or "سينباي", now
            ),
        )
        if not reply:
            await message.reply_text("لم أستطع معالجة التسجيل الآن، حاول مرة أخرى.")
            return
        clean_reply, mood = parse_mood_and_clean_reply(reply)
        history = data.get("history", [])
        history = (history + [
            {"role": "user", "text": "[رسالة صوتية]"},
            {"role": "model", "text": clean_reply},
        ])[-(MAX_HISTORY_MESSAGES * 2) :]
        await db_call(set_user_fields, user_id, {"history": history})
        await reply_in_chunks(message, clean_reply)
        await send_optional_sticker(context, user_id, mood)
    except Exception:
        logger.exception("Voice handling failed for user %s", user_id)
        await message.reply_text("حدث خطأ أثناء معالجة الصوت. حاول مرة أخرى.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user or not message.photo:
        return
    user_id = user.id
    reset_inactivity_job(user_id, context)
    now = datetime.now(TIMEZONE)
    await db_call(set_user_fields, user_id, {"last_seen": now.isoformat()})
    try:
        photo_file = await message.photo[-1].get_file()
        photo_bytes = await photo_file.download_as_bytearray()
        caption = message.caption or "حللي هذه الصورة وأجيبي عنها بالعربية."
        data = await db_call(get_user_data, user_id)
        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_bytes(data=bytes(photo_bytes), mime_type="image/jpeg"),
                    types.Part(text=caption[:4000]),
                ],
            )
        ]
        reply = await generate_gemini_response(
            contents=contents,
            system_prompt=await asyncio.to_thread(
                build_system_prompt, user_id, user.first_name or "سينباي", now
            ),
        )
        if not reply:
            await message.reply_text("لم أستطع تحليل الصورة الآن، حاول مرة أخرى.")
            return
        clean_reply, mood = parse_mood_and_clean_reply(reply)
        history = data.get("history", [])
        history = (history + [
            {"role": "user", "text": f"[صورة: {caption[:500]}]"},
            {"role": "model", "text": clean_reply},
        ])[-(MAX_HISTORY_MESSAGES * 2) :]
        await db_call(set_user_fields, user_id, {"history": history})
        await reply_in_chunks(message, clean_reply)
        await send_optional_sticker(context, user_id, mood)
    except Exception:
        logger.exception("Photo handling failed for user %s", user_id)
        await message.reply_text("حدث خطأ أثناء تحليل الصورة. حاول مرة أخرى.")

# ============================================================
# 10. Scheduled jobs
# ============================================================
async def send_inactivity_message(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.job.user_id
    data = await db_call(get_user_data, user_id)
    if not data.get("notifications", {}).get("inactivity_check", True):
        return
    reply = await generate_gemini_response(
        contents=["اكتب رسالة افتقاد قصيرة ولطيفة للمستخدم الذي غاب ساعتين."]
    )
    if reply:
        clean, mood = parse_mood_and_clean_reply(reply)
        try:
            await context.bot.send_message(chat_id=user_id, text=clean)
            await send_optional_sticker(context, user_id, mood)
        except Exception:
            logger.exception("Failed to send inactivity message to %s", user_id)


async def send_greeting(context: ContextTypes.DEFAULT_TYPE, prompt: str) -> None:
    try:
        reply = await generate_gemini_response(contents=[prompt])
        if not reply:
            return
        clean, mood = parse_mood_and_clean_reply(reply)
        user_ids = await db_call(get_active_user_ids)
        for user_id in user_ids:
            try:
                await context.bot.send_message(chat_id=user_id, text=clean)
                await send_optional_sticker(context, user_id, mood)
                await asyncio.sleep(0.05)
            except Exception:
                logger.exception("Failed scheduled greeting for %s", user_id)
    except Exception:
        logger.exception("Scheduled greeting failed")


async def morning_greeting(context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_greeting(context, "اكتب تحية صباحية قصيرة وحماسية بالعربية.")


async def evening_greeting(context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_greeting(context, "اكتب رسالة مسائية قصيرة تسأل المستخدم عن يومه.")

# ============================================================
# 11. Application
# ============================================================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled update error", exc_info=context.error)


def main() -> None:
    try:
        check_database()
        logger.info("MongoDB connection verified")
    except Exception:
        logger.exception("MongoDB connection failed during startup")
        raise

    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()

    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("features", features_command))
    application.add_handler(CommandHandler("whatsnew", whatsnew_command))
    application.add_handler(CommandHandler("reset", reset_command))
    application.add_handler(CommandHandler("otaku", set_otaku))
    application.add_handler(CommandHandler("serious", set_serious))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_error_handler(error_handler)

    if application.job_queue is None:
        raise RuntimeError(
            "JobQueue is unavailable. Install python-telegram-bot[job-queue]."
        )
    application.job_queue.run_daily(
        morning_greeting,
        time=time(hour=8, minute=30, tzinfo=TIMEZONE),
        name="morning_greeting",
    )
    application.job_queue.run_daily(
        evening_greeting,
        time=time(hour=21, minute=30, tzinfo=TIMEZONE),
        name="evening_greeting",
    )

    logger.info("Misaki bot %s started", BOT_VERSION)
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()

# Render Web Service settings:
# Build command: pip install -r requirements.txt
# Start command: python bot.py
# Health check path: /healthz
# Run one instance only when using polling.
