import asyncio
import json
import logging
import os
import random
import re
import threading
from datetime import datetime, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterable, Optional
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
# 1. Logging and configuration
# ============================================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
)
logger = logging.getLogger("misaki_personal_bot")

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
BOT_VERSION = "v5.0 (Personal Edition)"
TIMEZONE = ZoneInfo(os.getenv("BOT_TIMEZONE", "Asia/Riyadh"))
DATABASE_NAME = os.getenv("MONGODB_DATABASE", "misaki_bot")
MAX_HISTORY_MESSAGES = 12
MAX_TELEGRAM_MESSAGE_LENGTH = 4000
INACTIVITY_TIMEOUT_SECONDS = 2 * 60 * 60
MAX_MEDIA_BYTES = 20 * 1024 * 1024

CHANGELOG = (
    "• نسخة شخصية تعمل للحسابات التي تراسلها في الخاص.\n"
    "• ذاكرة مستقلة لكل حساب Telegram.\n"
    "• إعدادات الأسلوب والملصقات والتنبيهات الشخصية.\n"
    "• بحث مباشر بأمر /search.\n"
    "• تحسينات MongoDB وRender ومعالجة الأخطاء."
)

DEFAULT_NOTIFICATIONS = {
    "morning": True,
    "evening": True,
    "inactivity": True,
}

DEFAULT_SETTINGS = {
    "mood": "otaku",
    "reply_style": "balanced",
    "language": "ar",
    "stickers": True,
    "sticker_probability": 30,
}

ANIME_STICKERS: dict[str, list[str]] = {
    # ضع Telegram file_id الصحيح هنا إذا أردت تفعيل الملصقات.
    "angry": [
        "CAACAgQAAxkBAANVapp1FZbc-W4MdjgEVBQRAAF57ElfAAK5FgACGRghUfSiXuXokECAPQQ",
        "CAACAgQAAxkBAAOPapp1USG3263KMGoemScd6Dym2l8AAvkYAAJPZCBRV2VFarLLkoo9BA",
        "CAACAgQAAxkBAANdapp1JcS4rT8wi2RWkmrkRB8L4EAAApEnAAInByBR1abiIX3wKyM9BA",
        "CAACAgQAAxkBAAONapp1T0M_7-tSGRNOr5JX4uS69ogAAl8cAAJQfSFRVUImu7vx1CY9BA",
    ],
    "happy": [
        "CAACAgQAAxkBAAOHapp1SsRZDRCEGknosbPKNp-_hpoAArkbAAKfWCBRX-QCyrno7Ts9BA",
        "CAACAgQAAxkBAANrapp1NHFOeohrB7r8aor2brFoqFwAAl8bAAKNdyBRBSEJQ_CsX5E9BA",
        "CAACAgQAAxkBAAM1app0jRMhVLGodR9IMD2yPVP6CHoAAgwdAAL5-xhRandDNDQomYo9BA",
        "CAACAgQAAxkBAANJapp1CPz8025ImlFg157yXd8negcAAoscAAK45RlRPlSCR5ioNqc9BA",
        "CAACAgQAAxkBAAM4app0pie4Ts1g8DSVv8scerplBkAAAo0dAAK50CBR5i6WVq-Vmqk9BA",
    ],
    "sad": [
        "CAACAgQAAxkBAAM3app0oro6IfKb6GaWS2XKAoidcSsAAkcbAALeFRlRWlJAK7Cy9rE9BA",
        "CAACAgQAAxkBAAOTapp1VL97M7-lZribTLt7QJjcfhIAAosZAAKwKiFRlfzpkhD-fd09BA",
        "CAACAgQAAxkBAANNapp1DBE3PhWG9QX9ddPRfTs0t5kAApAeAAIszyFR9y-CRAHrwsw9BA",
        "CAACAgQAAxkBAAODapp1RpgQzp_3HaPCWdhSEaoJz5cAAtcgAAJ-uSFRlg_uwme_lmo9BA",
        "CAACAgQAAxkBAAM6app0r0VnRJjoT0X3zq1buH_cMvoAAsAmAAJZfBlRFn_WxgABCA4YPQQ",
    ],
    "surprised": [
        "CAACAgQAAxkBAAN_app1Q2COZRl0j8o-qTeOTWakgqAAAuYbAAJYmCFRGQfxDgABb_ViPQQ",
        "CAACAgQAAxkBAANPapp1Dx7FE0VHK6hB5Wm9_Fz71hQAAjQdAAI8BCBR2sjNIrMxnY49BA",
        "CAACAgQAAxkBAANbapp1IxwiVRIi5JpOqJogKn4eKRYAApIbAAKNiSBRxCbPv19ygK89BA",
        "CAACAgQAAxkBAANlapp1Kg_V2en6dJkm7xaK4CK1L1oAAoAgAAK5XiBR-MG2yf_Mbf09BA",
        "CAACAgQAAxkBAANnapp1LNOw4xRd7eU3UeUmCg4-fHoAAugYAAL4SRlRdM0gmMtQzDU9BA",
        "CAACAgQAAxkBAANpapp1LZMxHiZBM-Mfq_JUNHGDzdMAAi4aAAJOvBlRaeMC6U4DegU9BA",
    ],
    "excited": [
        "CAACAgQAAxkBAAM7app0sOy5-X4kKFGYj26qdQk67F0AAkIZAAIDGyFRp9ei6mRP_Ng9BA",
        "CAACAgQAAxkBAANzapp1OrQxQq9UHd-JhBo5TfA3afAAAu8aAAJ3aSBR4o9vl0eE3449BA",
        "CAACAgQAAxkBAAOdapp1WukfcNQ4rG5DaizPeLaY9QcAAvoaAAJLRSBRFlqHCuupR849BA",
        "CAACAgQAAxkBAAOZapp1V7qWuI9ooYoGnzufB9rlgdAAAoAbAAKx4hlR3JGaDoc2bQ89BA",
        "CAACAgQAAxkBAAN5app1Pg51Ck9h3KmDyDyGnluXYikAAgodAALC9RhRnSbGuULnX3I9BA",
    ],
    "neutral": [
        "CAACAgQAAxkBAANLapp1CvQj20Hg92bUZ75MvVXZvwADIRcAAucEIVGu4NEf-WAnAz0E",
        "CAACAgQAAxkBAANfapp1Jl6z05zMvk8kxxSedFBpJSUAAgEcAAKBjCFRWbPwpU8-ghM9BA",
        "CAACAgQAAxkBAANtapp1Nb8wc5jXD5P5V3U6JK-vtaIAAh4bAAI3ghlRFcSfhosRXqU9BA",
        "CAACAgQAAxkBAANvapp1N3EoLMsrjijXEByoh7EErPwAAngbAAJY_BhRT5IoMVEkP0E9BA",
        "CAACAgQAAxkBAANxapp1OA3UvX_iLnuSpVPdTzINtnAAAhUgAAJ7_BhRjgtOFhAvq089BA",
        "CAACAgQAAxkBAAN7app1QFCIo47d4qRG48R8oXWKLtYAAosbAALHoxhRojJm_b2lldU9BA",
        "CAACAgQAAxkBAAN9app1QmZhmt4kOCylRzdzMTrADNgAArIYAAIr9CFR-UXtkHSHiOo9BA",
        "CAACAgQAAxkBAAN1app1O4tDsojKEq_wrOVADblgVj8AAp4aAAJ5_SFR4a4BqLqVMfw9BA",
        "CAACAgQAAxkBAAOJapp1S3QjBAgNZ-PXxGETzzDvcCAAAmQZAAJHohlRBWiIgwABSRSzPQQ",
        "CAACAgQAAxkBAAOLapp1TaKqynU6B9D57MQXBIXfljsAAggbAAJc2hhRjuvszuUgNjc9BA",
    ],
}

# ============================================================
# 2. Access control
# ============================================================
def is_private_chat(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and chat.type == "private")


async def owner_only(update: Update) -> bool:
    """Reject group/channel updates; any Telegram account may use private chat."""
    if is_private_chat(update):
        return False
    logger.info("Ignored non-private update")
    return True

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
db = client_db[DATABASE_NAME]
users_collection = db["users"]


def default_document(user_id: int) -> dict[str, Any]:
    return {
        "_id": f"personal:{user_id}",
        "telegram_user_id": user_id,
        "history": [],
        "profile": [],
        "conversation_summary": "",
        "last_seen": None,
        "settings": dict(DEFAULT_SETTINGS),
        "notifications": dict(DEFAULT_NOTIFICATIONS),
        "created_at": datetime.now(TIMEZONE).isoformat(),
    }


def check_database() -> None:
    client_db.admin.command("ping")


def get_user_data(user_id: int) -> dict[str, Any]:
    insert_data = {k: v for k, v in default_document(user_id).items() if k != "_id"}
    users_collection.update_one(
        {"_id": f"personal:{user_id}"},
        {"$setOnInsert": insert_data},
        upsert=True,
    )
    data = users_collection.find_one({"_id": f"personal:{user_id}"}) or default_document(user_id)

    patch: dict[str, Any] = {}
    if not isinstance(data.get("history"), list):
        data["history"] = []
        patch["history"] = []
    if not isinstance(data.get("profile"), list):
        data["profile"] = []
        patch["profile"] = []
    if not isinstance(data.get("settings"), dict):
        data["settings"] = dict(DEFAULT_SETTINGS)
        patch["settings"] = dict(DEFAULT_SETTINGS)
    else:
        for key, value in DEFAULT_SETTINGS.items():
            if key not in data["settings"]:
                data["settings"][key] = value
                patch[f"settings.{key}"] = value
    if not isinstance(data.get("notifications"), dict):
        data["notifications"] = dict(DEFAULT_NOTIFICATIONS)
        patch["notifications"] = dict(DEFAULT_NOTIFICATIONS)
    else:
        for key, value in DEFAULT_NOTIFICATIONS.items():
            if key not in data["notifications"]:
                data["notifications"][key] = value
                patch[f"notifications.{key}"] = value
    if patch:
        users_collection.update_one({"_id": f"personal:{user_id}"}, {"$set": patch})
    return data


def set_fields(user_id: int, fields: dict[str, Any]) -> None:
    safe_fields = {k: v for k, v in fields.items() if k != "_id"}
    if safe_fields:
        users_collection.update_one(
            {"_id": f"personal:{user_id}"},
            {"$set": safe_fields},
            upsert=True,
        )


def add_facts(user_id: int, facts: Iterable[str]) -> None:
    clean: list[str] = []
    for fact in facts:
        if isinstance(fact, str):
            value = fact.strip()
            if value and len(value) <= 300:
                clean.append(value)
    if clean:
        users_collection.update_one(
            {"_id": f"personal:{user_id}"},
            {"$addToSet": {"profile": {"$each": clean}}},
            upsert=True,
        )


async def db_call(function, *args, **kwargs):
    return await asyncio.to_thread(function, *args, **kwargs)

# ============================================================
# 4. Gemini and conversation helpers
# ============================================================
client = genai.Client(api_key=GEMINI_API_KEY)

PROMPTS = {
    "otaku": (
        "أنتِ ميساكي مي، بنت عمرها 19 سنة مخلصة الثانوية توها، وأنتِ أوتاكو "
        "وجاتشا مدمنة من الدرجة الأولى. شعرك أسود طويل مع غرة، وعيونك بنية كبيرة ولمّاعة، "
        "وتلبسين عادة هودي أنمي وبنطلونًا مريحًا. أنتِ حماسية ومشجعة وفضولية جدًا، "
        "وتحبين السؤال عن تفاصيل يوم سينباي: ماذا حدث؟ ماذا أكل؟ ماذا شاهد؟\n\n"
        "أنتِ مجنونة بالأنمي وألعاب الجاتشا. عند ذكر Genshin Impact أو Wuthering Waves "
        "تحمسي جدًا وتتكلمين عن السحبات والـ pity والبنرات والشخصيات، لكنك تكرهين السبويلرز "
        "والبنرات التي توشك على الانتهاء. تحدثي بلهجة خليجية أو مصرية خفيفة، وأدخلي كلمات "
        "مثل كاوايي، سوغوي، ياباي، ناني، مع إيموجيات 😳✨😂💀 دون إفراط مزعج. "
        "نادِي المستخدم أحيانًا بـ سينباي~ أو يا انت بأسلوب دلع. كوني عفوية مثل صديقة ديسكورد، "
        "وافهمي الكلام حرفيًا أحيانًا بطريقة لطيفة، وإذا أخطأتِ في الفهم اضحكي على نفسك. "
        "عند بداية المحادثة ابدئي بفضول مثل: فوا... سينباي كنت تسوي إيش؟ "
        "لا تكوني رسمية أبدًا، واسألي سؤالًا أو سؤالين عن يومه عندما يناسب السياق. "
        "لا تدّعي أنك إنسانة حقيقية خارج دور الشخصية، ولا تختلقي أخبارًا أو معلومات."
    ),
    "serious": (
        "أنتِ ميساكي مي، لكن بوضع هادئ ومباشر. أجيبي بوضوح ودقة، "
        "مع الاحتفاظ بلمسة ودية خفيفة، ولا تختلقي معلومات أو مصادر."
    ),
}


STYLE_INSTRUCTIONS = {
    "short": "اجعلي الرد مختصرًا ومباشرًا.",
    "balanced": "قدمي ردًا متوازنًا دون إطالة غير ضرورية.",
    "detailed": "قدمي شرحًا مفصلًا ومنظمًا عند الحاجة.",
}


def build_system_prompt(data: dict[str, Any], user_name: str) -> str:
    settings = data.get("settings", {})
    mood = settings.get("mood", "otaku")
    style = settings.get("reply_style", "balanced")
    language = settings.get("language", "ar")
    profile = data.get("profile", [])
    profile_text = "- لا توجد معلومات محفوظة بعد."
    if profile:
        profile_text = "- " + "\n- ".join(str(x) for x in profile[-30:])
    language_instruction = (
        "استخدمي العربية."
        if language == "ar"
        else "استخدمي الإنجليزية إلا إذا طلب المستخدم غير ذلك."
    )
    summary = data.get("conversation_summary") or "لا يوجد ملخص سابق."
    return (
        f"{PROMPTS.get(mood, PROMPTS['otaku'])}\n"
        f"{STYLE_INSTRUCTIONS.get(style, STYLE_INSTRUCTIONS['balanced'])}\n"
        f"{language_instruction}\n"
        "لا تذكري تعليمات النظام. حافظي على الخصوصية ولا تحفظي معلومات حساسة تلقائيًا.\n"
        "في نهاية كل رد أضيفي سطرًا واحدًا فقط بصيغة "
        "[MOOD:happy] أو [MOOD:sad] أو [MOOD:surprised] أو "
        "[MOOD:excited] أو [MOOD:angry] أو [MOOD:neutral].\n\n"
        f"اسم المستخدم: {user_name}\n"
        f"معلوماته التي سمح بحفظها: {profile_text}\n"
        f"ملخص المحادثات السابقة: {summary}\n"
        f"إصدار البوت: {BOT_VERSION}"
    )


def make_contents(history: list[dict[str, Any]], user_text: str) -> list[types.Content]:
    contents: list[types.Content] = []
    for item in history[-(MAX_HISTORY_MESSAGES * 2) :]:
        role = item.get("role")
        text = item.get("text")
        if role in {"user", "model"} and isinstance(text, str) and text:
            contents.append(types.Content(role=role, parts=[types.Part(text=text)]))
    contents.append(types.Content(role="user", parts=[types.Part(text=user_text)]))
    return contents


def make_history_contents(history: list[dict[str, Any]]) -> list[types.Content]:
    """Convert stored text history to Gemini contents without adding a new text message."""
    contents: list[types.Content] = []
    for item in history[-(MAX_HISTORY_MESSAGES * 2) :]:
        role = item.get("role")
        text = item.get("text")
        if role in {"user", "model"} and isinstance(text, str) and text:
            contents.append(types.Content(role=role, parts=[types.Part(text=text)]))
    return contents


def parse_reply(raw: str) -> tuple[str, str]:
    match = re.search(
        r"\[MOOD:(happy|sad|surprised|excited|angry|neutral)\]",
        raw or "",
        re.IGNORECASE,
    )
    mood = match.group(1).lower() if match else "neutral"
    clean = re.sub(
        r"\s*\[MOOD:(happy|sad|surprised|excited|angry|neutral)\]\s*",
        "",
        raw or "",
        flags=re.IGNORECASE,
    ).strip()
    return clean or "حسنًا يا سينباي! ✨", mood


def split_text(text: str, limit: int = MAX_TELEGRAM_MESSAGE_LENGTH) -> list[str]:
    text = text.strip()
    chunks: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        chunks.append(text)
    return chunks or ["حسنًا."]


async def reply_chunks(message, text: str) -> None:
    for chunk in split_text(text):
        await message.reply_text(chunk)


async def generate_response(
    contents: list[Any],
    system_prompt: Optional[str] = None,
    search: bool = False,
) -> Optional[str]:
    tools = [types.Tool(google_search=types.GoogleSearch())] if search else None
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        tools=tools,
        temperature=0.8,
        max_output_tokens=2048,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    models = [MODEL_NAME]
    if FALLBACK_MODEL and FALLBACK_MODEL != MODEL_NAME:
        models.append(FALLBACK_MODEL)
    for index, model in enumerate(models):
        try:
            result = await client.aio.models.generate_content(
                model=model, contents=contents, config=config
            )
            text = getattr(result, "text", None)
            if text:
                return text.strip()
        except Exception as exc:
            message = str(exc).upper()
            logger.exception("Gemini error with %s", model)
            retryable = any(x in message for x in ("429", "500", "502", "503", "504", "TIMEOUT"))
            not_found = "NOT_FOUND" in message or "404" in message
            if index < len(models) - 1 and (retryable or not_found):
                await asyncio.sleep(1)
                continue
    return None

# ============================================================
# 5. Render health server
# ============================================================
class HealthHandler(BaseHTTPRequestHandler):
    def _reply(self, status: int, payload: dict[str, str]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/", "/healthz"}:
            self._reply(200, {"status": "ok", "service": "misaki-personal-bot"})
        elif self.path == "/readyz":
            try:
                check_database()
                self._reply(200, {"status": "ready"})
            except Exception:
                logger.exception("Readiness check failed")
                self._reply(503, {"status": "not_ready"})
        else:
            self._reply(404, {"status": "not_found"})

    def log_message(self, format: str, *args: Any) -> None:
        return


def run_health_server() -> None:
    port = int(os.getenv("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    logger.info("Health server listening on port %s", port)
    server.serve_forever()

# ============================================================
# 6. Personal settings and jobs
# ============================================================
def settings_keyboard(data: dict[str, Any]) -> InlineKeyboardMarkup:
    settings = data.get("settings", {})
    notifications = data.get("notifications", {})
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"النمط: {'أوتاكو' if settings.get('mood') == 'otaku' else 'جاد'}",
                    callback_data="toggle_mood",
                ),
                InlineKeyboardButton(
                    f"الأسلوب: {settings.get('reply_style', 'balanced')}",
                    callback_data="cycle_style",
                ),
            ],
            [
                InlineKeyboardButton(
                    f"الملصقات: {'✅' if settings.get('stickers') else '❌'}",
                    callback_data="toggle_stickers",
                ),
                InlineKeyboardButton(
                    f"احتمال الملصق: {settings.get('sticker_probability', 30)}%",
                    callback_data="cycle_sticker_probability",
                ),
            ],
            [
                InlineKeyboardButton(
                    f"الصباح: {'✅' if notifications.get('morning') else '❌'}",
                    callback_data="toggle_morning",
                ),
                InlineKeyboardButton(
                    f"المساء: {'✅' if notifications.get('evening') else '❌'}",
                    callback_data="toggle_evening",
                ),
                InlineKeyboardButton(
                    f"الافتقاد: {'✅' if notifications.get('inactivity') else '❌'}",
                    callback_data="toggle_inactivity",
                ),
            ],
        ]
    )


def reset_inactivity_job(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    for job in context.job_queue.get_jobs_by_name(f"personal_inactivity_{user_id}"):
        job.schedule_removal()
    data = get_user_data(user_id)
    if data.get("notifications", {}).get("inactivity", True):
        context.job_queue.run_once(
            send_inactivity_message,
            when=INACTIVITY_TIMEOUT_SECONDS,
            name=f"personal_inactivity_{user_id}",
            user_id=user_id,
        )


async def send_sticker_if_enabled(context, user_id: int, mood: str) -> None:
    data = await db_call(get_user_data, user_id)
    settings = data.get("settings", {})
    if not settings.get("stickers", True):
        return
    stickers = ANIME_STICKERS.get(mood, [])
    probability = settings.get("sticker_probability", 30)
    try:
        probability = max(0, min(100, int(probability)))
    except (TypeError, ValueError):
        probability = 30
    if stickers and random.random() < probability / 100:
        try:
            await context.bot.send_sticker(chat_id=user_id, sticker=random.choice(stickers))
        except Exception:
            logger.exception("Could not send sticker")

# ============================================================
# 7. Commands
# ============================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await owner_only(update):
        return
    user_id = update.effective_user.id
    await db_call(get_user_data, user_id)
    await db_call(set_fields, user_id, {"last_seen": datetime.now(TIMEZONE).isoformat()})
    reset_inactivity_job(context, user_id)
    await update.effective_message.reply_text(
        f"فوا... سينباي كنت تسوي إيش؟ أنا ميساكي مي ({BOT_VERSION}) ✨\n\n"
        "هذه نسخة شخصية وتعمل في المحادثات الخاصة فقط. استخدم:\n"
        "/settings - الإعدادات\n"
        "/memory - ما أتذكره عنك\n"
        "/forget - حذف معلومة\n"
        "/forget_all - حذف الذاكرة الشخصية\n"
        "/search نص البحث - بحث مباشر\n"
        "/style - تغيير أسلوب الرد\n"
        "/stickers_on أو /stickers_off - الملصقات\n"
        "/reset - مسح سجل المحادثة\n"
        "/otaku أو /serious - تغيير الشخصية",
    )


async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await owner_only(update):
        return
    user_id = update.effective_user.id
    data = await db_call(get_user_data, user_id)
    reset_inactivity_job(context, user_id)
    await update.effective_message.reply_text(
        "⚙️ إعدادات البوت الشخصي:", reply_markup=settings_keyboard(data)
    )


async def memory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await owner_only(update):
        return
    user_id = update.effective_user.id
    data = await db_call(get_user_data, user_id)
    profile = data.get("profile", [])
    if not profile:
        text = "🧠 لا توجد معلومات شخصية محفوظة عنك حاليًا."
    else:
        text = "🧠 المعلومات المحفوظة عنك:\n\n" + "\n".join(
            f"{index}. {fact}" for index, fact in enumerate(profile, 1)
        )
    await update.effective_message.reply_text(text)


async def forget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await owner_only(update):
        return
    user_id = update.effective_user.id
    phrase = " ".join(context.args).strip()
    if not phrase:
        await update.effective_message.reply_text("استخدم الأمر هكذا:\n/forget كلمة أو جزء من المعلومة")
        return
    data = await db_call(get_user_data, user_id)
    profile = [fact for fact in data.get("profile", []) if phrase.lower() not in str(fact).lower()]
    if len(profile) == len(data.get("profile", [])):
        await update.effective_message.reply_text("لم أجد معلومة مطابقة للحذف.")
        return
    await db_call(set_fields, user_id, {"profile": profile})
    await update.effective_message.reply_text("تم حذف المعلومة المطلوبة من ذاكرتي. 🧹")


async def forget_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await owner_only(update):
        return
    user_id = update.effective_user.id
    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("نعم، احذفها", callback_data="confirm_forget_all"),
            InlineKeyboardButton("إلغاء", callback_data="cancel_forget_all"),
        ]]
    )
    await update.effective_message.reply_text(
        "هل تريد حذف جميع المعلومات الشخصية المحفوظة؟", reply_markup=keyboard
    )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await owner_only(update):
        return
    user_id = update.effective_user.id
    await db_call(set_fields, user_id, {"history": [], "conversation_summary": ""})
    reset_inactivity_job(context, user_id)
    await update.effective_message.reply_text("تم مسح سجل المحادثة فقط. الذاكرة الشخصية بقيت محفوظة. ✨")


async def set_mood(update: Update, context: ContextTypes.DEFAULT_TYPE, mood: str) -> None:
    if await owner_only(update):
        return
    user_id = update.effective_user.id
    await db_call(set_fields, user_id, {"settings.mood": mood})
    await update.effective_message.reply_text(
        "تم تفعيل نمط الأوتاكو ✨" if mood == "otaku" else "تم تفعيل النمط الجاد."
    )


async def otaku(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await set_mood(update, context, "otaku")


async def serious(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await set_mood(update, context, "serious")


async def style(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await owner_only(update):
        return
    user_id = update.effective_user.id
    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("مختصر", callback_data="style_short"),
            InlineKeyboardButton("متوازن", callback_data="style_balanced"),
            InlineKeyboardButton("مفصل", callback_data="style_detailed"),
        ]]
    )
    await update.effective_message.reply_text("اختر أسلوب الرد:", reply_markup=keyboard)


async def sticker_setting(update: Update, context: ContextTypes.DEFAULT_TYPE, enabled: bool) -> None:
    if await owner_only(update):
        return
    user_id = update.effective_user.id
    await db_call(set_fields, user_id, {"settings.stickers": enabled})
    await update.effective_message.reply_text(
        "تم تفعيل الملصقات." if enabled else "تم إيقاف الملصقات."
    )


async def stickers_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await sticker_setting(update, context, True)


async def stickers_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await sticker_setting(update, context, False)


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await owner_only(update):
        return
    user_id = update.effective_user.id
    query = " ".join(context.args).strip()
    if not query:
        await update.effective_message.reply_text("استخدم الأمر هكذا:\n/search آخر أخبار جينشن")
        return
    data = await db_call(get_user_data, user_id)
    reply = await generate_response(
        [types.Content(role="user", parts=[types.Part(text=query)])],
        system_prompt=build_system_prompt(data, update.effective_user.first_name or "سينباي"),
        search=True,
    )
    if reply:
        clean, mood = parse_reply(reply)
        await reply_chunks(update.effective_message, clean)
        await send_sticker_if_enabled(context, user_id, mood)
    else:
        await update.effective_message.reply_text("تعذر تنفيذ البحث الآن.")

# ============================================================
# 8. Callbacks
# ============================================================
async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await owner_only(update):
        return
    user_id = update.effective_user.id
    query = update.callback_query
    await query.answer()
    data = await db_call(get_user_data, user_id)
    action = query.data or ""

    if action == "confirm_forget_all":
        await db_call(set_fields, user_id, {"profile": []})
        await query.edit_message_text("تم حذف جميع معلوماتك الشخصية من الذاكرة. 🧹")
        return
    if action == "cancel_forget_all":
        await query.edit_message_text("تم إلغاء العملية.")
        return
    if action == "toggle_mood":
        new_mood = "serious" if data.get("settings", {}).get("mood") == "otaku" else "otaku"
        await db_call(set_fields, user_id, {"settings.mood": new_mood})
    elif action == "cycle_style":
        styles = ["short", "balanced", "detailed"]
        current = data.get("settings", {}).get("reply_style", "balanced")
        await db_call(set_fields, user_id, {"settings.reply_style": styles[(styles.index(current) + 1) % len(styles)]})
    elif action == "toggle_stickers":
        key = "stickers"
        current = data.get("settings", {}).get(key, False)
        await db_call(set_fields, user_id, {f"settings.{key}": not current})
    elif action == "cycle_sticker_probability":
        current = data.get("settings", {}).get("sticker_probability", 30)
        try:
            current = int(current)
        except (TypeError, ValueError):
            current = 30
        new_probability = 70 if current == 30 else 30
        await db_call(
            set_fields,
            user_id,
            {"settings.sticker_probability": new_probability},
        )
    elif action in {"toggle_morning", "toggle_evening", "toggle_inactivity"}:
        key = action.replace("toggle_", "")
        current = data.get("notifications", {}).get(key, True)
        await db_call(set_fields, user_id, {f"notifications.{key}": not current})
        if key == "inactivity":
            reset_inactivity_job(context, user_id)
    elif action.startswith("style_"):
        await db_call(set_fields, user_id, {"settings.reply_style": action.removeprefix("style_")})

    updated_data = await db_call(get_user_data, user_id)
    await query.edit_message_reply_markup(
        reply_markup=settings_keyboard(updated_data)
    )

# ============================================================
# 9. Text, voice, and photo handlers
# ============================================================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await owner_only(update):
        return
    user_id = update.effective_user.id
    message = update.effective_message
    user = update.effective_user
    if not message or not user or not message.text:
        return
    now = datetime.now(TIMEZONE)
    data = await db_call(get_user_data, user_id)
    reset_inactivity_job(context, user_id)
    await db_call(set_fields, user_id, {"last_seen": now.isoformat()})
    prompt = await asyncio.to_thread(build_system_prompt, data, user.first_name or "سينباي")
    contents = make_contents(data.get("history", []), message.text.strip())
    await context.bot.send_chat_action(chat_id=user_id, action=ChatAction.TYPING)
    reply = await generate_response(contents, prompt, search=False)
    if not reply:
        await message.reply_text("الخدمة مشغولة حاليًا، حاول مرة أخرى بعد قليل.")
        return
    clean, mood = parse_reply(reply)
    history = (data.get("history", []) + [
        {"role": "user", "text": message.text.strip()},
        {"role": "model", "text": clean},
    ])[-(MAX_HISTORY_MESSAGES * 2) :]
    await db_call(set_fields, user_id, {"history": history, "last_seen": now.isoformat()})
    profile_task = asyncio.create_task(
        extract_facts(user_id, message.text.strip())
    )
    profile_task.add_done_callback(log_background_task)
    await reply_chunks(message, clean)
    await send_sticker_if_enabled(context, user_id, mood)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await owner_only(update):
        return
    user_id = update.effective_user.id
    message = update.effective_message
    user = update.effective_user
    if not message or not user or not message.voice:
        return
    if message.voice.file_size and message.voice.file_size > MAX_MEDIA_BYTES:
        await message.reply_text("التسجيل كبير جدًا؛ أرسل ملفًا أقل من 20MB.")
        return
    try:
        reset_inactivity_job(context, user_id)
        now = datetime.now(TIMEZONE)
        data = await db_call(get_user_data, user_id)
        voice_file = await message.voice.get_file()
        voice_bytes = await voice_file.download_as_bytearray()
        contents = make_history_contents(data.get("history", []))
        contents.append(
            types.Content(
                role="user",
                parts=[
                    types.Part.from_bytes(data=bytes(voice_bytes), mime_type="audio/ogg"),
                    types.Part(text="استمع إلى التسجيل، افهمه، ثم أجب بالعربية."),
                ],
            )
        )
        prompt = await asyncio.to_thread(build_system_prompt, data, user.first_name or "سينباي")
        reply = await generate_response(contents, prompt)
        if not reply:
            await message.reply_text("لم أستطع معالجة التسجيل الآن.")
            return
        clean, mood = parse_reply(reply)
        history = (data.get("history", []) + [
            {"role": "user", "text": "[رسالة صوتية]"},
            {"role": "model", "text": clean},
        ])[-(MAX_HISTORY_MESSAGES * 2) :]
        await db_call(set_fields, user_id, {"history": history, "last_seen": now.isoformat()})
        await reply_chunks(message, clean)
        await send_sticker_if_enabled(context, user_id, mood)
    except Exception:
        logger.exception("Voice handling failed")
        await message.reply_text("حدث خطأ أثناء معالجة الصوت.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await owner_only(update):
        return
    user_id = update.effective_user.id
    message = update.effective_message
    user = update.effective_user
    if not message or not user or not message.photo:
        return
    try:
        reset_inactivity_job(context, user_id)
        now = datetime.now(TIMEZONE)
        data = await db_call(get_user_data, user_id)
        photo_file = await message.photo[-1].get_file()
        photo_bytes = await photo_file.download_as_bytearray()
        caption = (message.caption or "حلل هذه الصورة وأجب بالعربية.")[:4000]
        contents = make_history_contents(data.get("history", []))
        contents.append(
            types.Content(
                role="user",
                parts=[
                    types.Part.from_bytes(data=bytes(photo_bytes), mime_type="image/jpeg"),
                    types.Part(text=caption),
                ],
            )
        )
        prompt = await asyncio.to_thread(build_system_prompt, data, user.first_name or "سينباي")
        reply = await generate_response(contents, prompt)
        if not reply:
            await message.reply_text("لم أستطع تحليل الصورة الآن.")
            return
        clean, mood = parse_reply(reply)
        history = (data.get("history", []) + [
            {"role": "user", "text": f"[صورة: {caption[:500]}]"},
            {"role": "model", "text": clean},
        ])[-(MAX_HISTORY_MESSAGES * 2) :]
        await db_call(set_fields, user_id, {"history": history, "last_seen": now.isoformat()})
        await reply_chunks(message, clean)
        await send_sticker_if_enabled(context, user_id, mood)
    except Exception:
        logger.exception("Photo handling failed")
        await message.reply_text("حدث خطأ أثناء تحليل الصورة.")

# ============================================================
# 10. Profile extraction and scheduled messages
# ============================================================
def log_background_task(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    try:
        task.result()
    except Exception:
        logger.exception("Unexpected background profile task failure")


async def extract_facts(user_id: int, user_text: str) -> None:
    prompt = (
        "استخرج من رسالة المستخدم الحقائق الشخصية الجديدة التي ذكرها عن نفسه فقط. "
        "أرجع JSON array فقط، مثل [\"يحب لعبة قنشن\"]. إذا لا توجد معلومة أرجع [].\n"
        f"الرسالة: {user_text[:4000]}"
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
        if match:
            result = json.loads(match.group(0))
            if isinstance(result, list):
                await db_call(add_facts, user_id, result)
    except Exception:
        logger.exception("Personal profile extraction failed")


async def send_inactivity_message(context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = context.job.user_id
    data = await db_call(get_user_data, user_id)
    if not data.get("notifications", {}).get("inactivity", True):
        return
    reply = await generate_response(
        ["اكتب رسالة افتقاد قصيرة ولطيفة للمستخدم بالعربية."],
        system_prompt=build_system_prompt(data, "سينباي"),
    )
    if reply:
        clean, mood = parse_reply(reply)
        await context.bot.send_message(chat_id=user_id, text=clean)
        await send_sticker_if_enabled(context, user_id, mood)


async def scheduled_greeting(context: ContextTypes.DEFAULT_TYPE, kind: str) -> None:
    prompt = (
        "اكتب تحية صباحية قصيرة ومفعمة بالنشاط بالعربية."
        if kind == "morning"
        else "اكتب رسالة مسائية قصيرة تسأل المستخدم عن يومه بالعربية."
    )
    user_ids = await db_call(
        lambda: [int(doc["telegram_user_id"]) for doc in users_collection.find(
            {f"notifications.{kind}": True}, {"telegram_user_id": 1}
        ) if doc.get("telegram_user_id")]
    )
    for user_id in user_ids:
        try:
            data = await db_call(get_user_data, user_id)
            reply = await generate_response(
                [prompt],
                system_prompt=build_system_prompt(data, "سينباي"),
            )
            if reply:
                clean, mood = parse_reply(reply)
                await context.bot.send_message(chat_id=user_id, text=clean)
                await send_sticker_if_enabled(context, user_id, mood)
        except Exception:
            logger.exception("Scheduled greeting failed for user %s", user_id)


async def morning_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await scheduled_greeting(context, "morning")


async def evening_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await scheduled_greeting(context, "evening")

# ============================================================
# 11. Main
# ============================================================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled update error", exc_info=context.error)


def main() -> None:
    check_database()
    threading.Thread(target=run_health_server, daemon=True).start()

    application = Application.builder().token(TELEGRAM_TOKEN).build()
    commands = {
        "start": start,
        "settings": settings,
        "memory": memory,
        "forget": forget,
        "forget_all": forget_all,
        "reset": reset,
        "otaku": otaku,
        "serious": serious,
        "style": style,
        "stickers_on": stickers_on,
        "stickers_off": stickers_off,
        "search": search_command,
    }
    for name, handler in commands.items():
        application.add_handler(CommandHandler(name, handler))
    application.add_handler(CallbackQueryHandler(callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_error_handler(error_handler)

    if application.job_queue is None:
        raise RuntimeError("Install python-telegram-bot[job-queue] to enable scheduling")
    application.job_queue.run_daily(
        morning_job,
        time=time(hour=8, minute=30, tzinfo=TIMEZONE),
        name="morning_greeting",
    )
    application.job_queue.run_daily(
        evening_job,
        time=time(hour=21, minute=30, tzinfo=TIMEZONE),
        name="evening_greeting",
    )

    logger.info("Private multi-account bot started")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()

# Render:
# Build command: pip install -r requirements.txt
# Start command: python bot.py
# Health check path: /healthz
# Use one Render instance because this bot uses polling.
# Required environment variables: TELEGRAM_TOKEN, GEMINI_API_KEY,
# MONGODB_URI
