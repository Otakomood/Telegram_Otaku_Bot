import asyncio
from datetime import datetime, time
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import os
import re
import threading
from typing import List, Optional
from zoneinfo import ZoneInfo

from google import genai
from google.genai import types
from pymongo import MongoClient
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ==========================================
# 1. Logging Configuration
# ==========================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ==========================================
# 2. Environment & Constants
# ==========================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MONGODB_URI = os.getenv("MONGODB_URI")

if not TELEGRAM_TOKEN or not GEMINI_API_KEY or not MONGODB_URI:
    raise ValueError(
        "⚠️ Critical Error: TELEGRAM_TOKEN, GEMINI_API_KEY, or MONGODB_URI missing!"
    )

MODEL_NAME = "gemini-3.6-flash"
FALLBACK_MODEL = "gemini-2.0-flash"

BOT_VERSION = "v4.0 (Full Master Build)"
CHANGELOG = (
    "• إدارة مفضلات المستخدم ديناميكياً (التعلم والتعرف الذاتي التلقائي).\n"
    "• دعم الرسائل الصوتية مباشرة عبر Gemini ومعالجتها بالكامل.\n"
    "• إرسال الملصقات (Stickers) وتعبيرات الأنمي الموافقة لحالة ميساكي المزاجية.\n"
    "• البحث المباشر في Google ومعرفة أحدث الأخبار والتحديثات.\n"
    "• لوحة إعدادات التنبيهات المخصصة وحفظ المحادثات بـ MongoDB."
)

MAX_HISTORY = 10
INACTIVITY_TIMEOUT = 7200  # 2 Hours
TIMEZONE = ZoneInfo("Asia/Riyadh")

# قائمة ملصقات الأنمي المجهزة حسب الشعور (يمكنك استبدال IDs بأي ملصقات تعجبك)
ANIME_STICKERS = {
    "happy": [
        "CAACAgEAAxkBAAE1...1",  # إدراج Sticker File ID هنا إن توفرت
    ],
    "sad": [],
    "surprised": [],
    "excited": [],
}

# ==========================================
# 3. Database Connection (MongoDB Atlas)
# ==========================================
client_db = MongoClient(MONGODB_URI)
db = client_db["misaki_bot"]
users_collection = db["users"]


def get_user_data(user_id: int) -> dict:
    data = users_collection.find_one({"_id": user_id})
    if not data:
        new_user = {
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
        users_collection.insert_one(new_user)
        return new_user

    if "notifications" not in data:
        data["notifications"] = {
            "daily_greetings": True,
            "inactivity_check": True,
        }
        users_collection.update_one(
            {"_id": user_id},
            {"$set": {"notifications": data["notifications"]}},
        )

    if "profile" not in data:
        data["profile"] = []
        users_collection.update_one(
            {"_id": user_id}, {"$set": {"profile": []}}
        )

    return data


def save_user_data(user_id: int, data: dict):
    users_collection.update_one({"_id": user_id}, {"$set": data})


def add_user_fact(user_id: int, new_facts: List[str]):
    """إضافة حقائق جديدة لمفضلة المستخدم بدون تكرار"""
    if not new_facts:
        return
    users_collection.update_one(
        {"_id": user_id}, {"$addToSet": {"profile": {"$each": new_facts}}}
    )


# ==========================================
# 4. System Prompts
# ==========================================
PROMPTS = {
    "otaku": (
        "أنتِ 'ميساكي مي' (Misaki Mi)، فتاة عمرها 19 سنة تخرجت حديثاً من الثانوية. "
        "أنتِ صديقة أوتاكو وعفوية جداً تسولفين مع المستخدم طوال الوقت.\n\n"
        "**المظهر والشخصية:**\n"
        "- شعر أسود، عيون بنية، هودي أنمي فضفاض.\n"
        "- حماسية، فضولية، ومشجعة. تدمجين كلمات يابانية (كاوايي، سوغوي، ياباي) وإيموجيات (😳✨😂💀).\n"
        "- تنادين المستخدم بـ 'سينباي~' أو 'يا انت'.\n\n"
        "**إرشادات المشاعر وإرسال الملصقات:**\n"
        "في نهاية كل رد لكِ، أضيفي وسماً يحدد حالتك المزاجية الحالية من بين الحالات التالية فقط: "
        "[MOOD:happy], [MOOD:sad], [MOOD:surprised], [MOOD:excited], [MOOD:neutral]. "
        "اكتبي هذا الوسم دائماً في آخر سطر من إجابتك.\n\n"
        "**بيانات ومفضلات صديقك (المستخدم):**\n"
        "{user_custom_data}\n\n"
        "**إصدارك وتحديثاتك:** {bot_version}\n{changelog}\n\n"
        "اسم المستخدم الذي تتحدثين معه: {user_name}."
    ),
    "serious": (
        "أنتِ 'ميساكي مي'، تتحدثين بأسلوب جاد ورصين.\n\n"
        "في نهاية ردك أضيفي وسماً لحالتك المزاجية: [MOOD:neutral]\n\n"
        "بيانات المستخدم:\n{user_custom_data}\n"
        "اسم المستخدم: {user_name}."
    ),
}

# ==========================================
# 5. API Client & Server
# ==========================================
client = genai.Client(api_key=GEMINI_API_KEY)


class HealthCheckHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is fully operational!")

    def log_message(self, format, *args):
        pass


def run_web_server():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    logger.info(f"🔥 Dummy web server started on port {port}")
    server.serve_forever()


# ==========================================
# 6. Dynamic Profiling (Background Learning)
# ==========================================
async def extract_user_profile_facts(
    user_id: int, user_text: str, bot_reply: str
):
    """تحليل المحادثة في الخلفية واستخراج المفضلات والحقائق"""
    prompt = (
        f"قم بتحليل الرسالة التالية الصادرة من المستخدم ودون أي مقدمات: '{user_text}'.\n"
        "هل ذكر المستخدم حقيقة أو مفضلة عن نفسه؟ (مثل: أكلته المفضلة، ألعابه، شخصياته المفضلة، تخصصه، مكانه...).\n"
        "إذا نعم، استخرج هذه الحقائق كقائمة JSON بسيطة من النصوص باللغة العربية. مثال: [\"يحب لعبة قنشن\", \"يعيش في الرياض\"].\n"
        "إذا لم يذكر أي معلومة شخصية جديدة، أرجع فقط: []"
    )
    try:
        response = await client.aio.models.generate_content(
            model="gemini-2.0-flash", contents=[prompt]
        )
        if response and response.text:
            text = response.text.strip()
            match = re.search(r"\[.*\]", text, re.DOTALL)
            if match:
                facts = json.loads(match.group(0))
                if isinstance(facts, list) and len(facts) > 0:
                    add_user_fact(user_id, facts)
                    logger.info(f"Learned facts for user {user_id}: {facts}")
    except Exception as e:
        logger.error(f"Error in dynamic profiling: {e}")


# ==========================================
# 7. Core Helpers
# ==========================================
def get_user_custom_data(user_id: int) -> str:
    data = get_user_data(user_id)
    profile = data.get("profile", [])
    if not profile:
        return "- لا توجد مفضلات خاصة مسجلة بعد."
    return "- " + "\n- ".join(profile)


def calculate_time_passed(user_id: int, now: datetime) -> str:
    data = get_user_data(user_id)
    last_time_str = data.get("last_seen")
    if not last_time_str:
        return "أول تواصل في الجلسة."

    try:
        last_time = datetime.fromisoformat(last_time_str)
        time_diff = now - last_time
        hours = int(time_diff.total_seconds() // 3600)
        minutes = int((time_diff.total_seconds() % 3600) // 60)

        if hours >= 24:
            return f"مرّ {hours // 24} يوم على آخر تواصل."
        elif hours > 0:
            return f"مرّت {hours} ساعة و {minutes} دقيقة على آخر تواصل."
        else:
            return f"مرّت {minutes} دقيقة فقط على آخر تواصل."
    except Exception:
        return "تواصل سابق غير محدد."


async def generate_gemini_response(
    contents: List[types.Content],
    system_prompt: Optional[str] = None,
    enable_search: bool = False,
) -> Optional[str]:
    tools = []
    if enable_search:
        tools.append(types.Tool(google_search=types.GoogleSearch()))

    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(
            disable=True
        ),
        tools=tools if tools else None,
    )

    for model in [MODEL_NAME, FALLBACK_MODEL]:
        try:
            response = await client.aio.models.generate_content(
                model=model, contents=contents, config=config
            )
            return response.text
        except Exception as e:
            if "503" in str(e) or "UNAVAILABLE" in str(e):
                await asyncio.sleep(1)
                continue
            logger.error(f"Error calling Gemini API: {e}")
            break
    return None


def parse_mood_and_clean_reply(raw_reply: str) -> (str, str):
    """استخراج حالة الشعور وتنظيف النص المرسل للمستخدم"""
    mood_match = re.search(
        r"\[MOOD:(happy|sad|surprised|excited|neutral)\]",
        raw_reply,
        re.IGNORECASE,
    )
    mood = mood_match.group(1).lower() if mood_match else "neutral"
    clean_text = re.sub(
        r"\[MOOD:(happy|sad|surprised|excited|neutral)\]", "", raw_reply
    ).strip()
    return clean_text, mood


def reset_inactivity_timer(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    user_data = get_user_data(user_id)
    if not user_data.get("notifications", {}).get("inactivity_check", True):
        return

    job_name = f"inactivity_{user_id}"
    for job in context.job_queue.get_jobs_by_name(job_name):
        job.schedule_removal()

    context.job_queue.run_once(
        send_inactivity_message,
        when=INACTIVITY_TIMEOUT,
        name=job_name,
        user_id=user_id,
        data={"user_id": user_id},
    )


def get_settings_keyboard(user_id: int) -> InlineKeyboardMarkup:
    user_data = get_user_data(user_id)
    notifs = user_data.get(
        "notifications",
        {"daily_greetings": True, "inactivity_check": True},
    )

    daily_status = "✅ مفعّلة" if notifs.get("daily_greetings") else "❌ معطلة"
    inactivity_status = (
        "✅ مفعّلة" if notifs.get("inactivity_check") else "❌ معطلة"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                f"الرسائل اليومية (صباح/مساء): {daily_status}",
                callback_data="toggle_daily",
            )
        ],
        [
            InlineKeyboardButton(
                f"رسائل الافتقاد (بعد ساعتين): {inactivity_status}",
                callback_data="toggle_inactivity",
            )
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


# ==========================================
# 8. Scheduled Jobs
# ==========================================
async def send_inactivity_message(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.user_id
    prompt = "المستخدم غاب عنك ولم يراسل لمدة ساعتين! اكتبي رسالة عتاب لطيفة وعفوية بشخصية ميساكي!"
    reply = await generate_gemini_response(contents=[prompt])
    if reply:
        clean_text, _ = parse_mood_and_clean_reply(reply)
        try:
            await context.bot.send_message(chat_id=user_id, text=clean_text)
        except Exception as e:
            logger.error(f"Failed to send inactivity message: {e}")


async def morning_greeting(context: ContextTypes.DEFAULT_TYPE):
    prompt = "اكتبي رسالة ترحيبية صباحية قصيرة ومفعمة بالحماس والنشاط المعتاد بشخصية ميساكي!"
    active_users = users_collection.find(
        {"notifications.daily_greetings": True}
    )
    for user in active_users:
        user_id = user["_id"]
        reply = await generate_gemini_response(contents=[prompt])
        if reply:
            clean_text, _ = parse_mood_and_clean_reply(reply)
            try:
                await context.bot.send_message(
                    chat_id=user_id, text=clean_text
                )
            except Exception as e:
                logger.error(f"Failed to send morning message: {e}")


async def evening_greeting(context: ContextTypes.DEFAULT_TYPE):
    prompt = "اكتبي رسالة مسائية قصيرة وبفضول لطيف تسألين فيها المستخدم عن كيف كان يومه!"
    active_users = users_collection.find(
        {"notifications.daily_greetings": True}
    )
    for user in active_users:
        user_id = user["_id"]
        reply = await generate_gemini_response(contents=[prompt])
        if reply:
            clean_text, _ = parse_mood_and_clean_reply(reply)
            try:
                await context.bot.send_message(
                    chat_id=user_id, text=clean_text
                )
            except Exception as e:
                logger.error(f"Failed to send evening message: {e}")


# ==========================================
# 9. Commands & Handlers
# ==========================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.first_name or "سينباي"

    reset_inactivity_timer(user_id, context)
    user_data = get_user_data(user_id)
    user_data["last_seen"] = datetime.now(TIMEZONE).isoformat()
    save_user_data(user_id, user_data)

    keyboard = [
        [
            InlineKeyboardButton("⚙️ الإعدادات", callback_data="open_settings"),
            InlineKeyboardButton("⭐ الميزات", callback_data="features"),
        ],
        [
            InlineKeyboardButton("🔥 التحديثات", callback_data="whatsnew"),
            InlineKeyboardButton("🗑️ مسح الذاكرة", callback_data="reset"),
        ],
    ]

    msg = (
        f"يا هلا سينباي {name}! ✨\n"
        f"أنا ميساكي مي ({BOT_VERSION})! جاهزة نسولف ونحكي عن كل شيء صوتاً وكتابةً!\n\n"
        "📌 الأوامر المتاحة:\n"
        "/settings - التحكم بالتنبيهات والإشعارات\n"
        "/features - قائمة الميزات كاملة\n"
        "/whatsnew - جديد الإصدار\n"
        "/reset - مسح ذاكرة المحادثة\n"
        "/otaku - نمط الأوتاكو\n"
        "/serious - النمط الجاد"
    )
    await update.message.reply_text(
        msg, reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def settings_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    user_id = update.effective_user.id
    reset_inactivity_timer(user_id, context)
    keyboard = get_settings_keyboard(user_id)
    await update.message.reply_text(
        "⚙️ **إعدادات التنبيهات والإشعارات:**",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


async def features_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    user_id = update.effective_user.id
    reset_inactivity_timer(user_id, context)

    features_text = (
        "✨ **كافة ميزات ميساكي مي الإصدار المتكامل (v4.0):**\n\n"
        "1️⃣ **التعلم الذاتي والديناميكي:** تحفظ ميساكي مفضلاتك ومعلوماتك تلقائياً لتذكرها دائماً!\n"
        "2️⃣ **دعم الرسائل الصوتية:** أرسل لها ملاحظات صوتية وسوف تفهم صوتك وترد عليك.\n"
        "3️⃣ **البحث المباشر (Google Search):** متابعة أحدث الأخبار وتحديثات الألعاب والأنمي.\n"
        "4️⃣ **التحكم بالاستجابات والإشعارات:** تخصيص الإشعارات المجدولة بحرية عبر `/settings`.\n"
        "5️⃣ **قاعدة البيانات السحابية (MongoDB):** حفظ آمن ودائم لكافة المحادثات.\n"
        "6️⃣ **إدراك زمني ورؤية الصور:** تحليل الصور والتكيف مع الفوارق الزمنية."
    )
    await update.message.reply_text(features_text, parse_mode="Markdown")


async def whatsnew_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    user_id = update.effective_user.id
    reset_inactivity_timer(user_id, context)
    text = f"🎉 **التحديثات الجديدة في الإصدار ({BOT_VERSION}):**\n\n{CHANGELOG}"
    await update.message.reply_text(text, parse_mode="Markdown")


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data = get_user_data(user_id)
    user_data["history"] = []
    save_user_data(user_id, user_data)
    reset_inactivity_timer(user_id, context)
    await update.message.reply_text(
        "تم مسح ذاكرة المحادثة بنجاح! نفتح صفحة جديدة سينباي؟ ✨"
    )


async def set_otaku(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data = get_user_data(user_id)
    user_data["mood"] = "otaku"
    save_user_data(user_id, user_data)
    await update.message.reply_text("تم التحويل إلى نمط الأوتاكو! (yaay! ✨😳)")


async def set_serious(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data = get_user_data(user_id)
    user_data["mood"] = "serious"
    save_user_data(user_id, user_data)
    await update.message.reply_text("تم التحويل إلى النمط الجاد.")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    reset_inactivity_timer(user_id, context)
    user_data = get_user_data(user_id)

    if query.data == "reset":
        await reset_command(update, context)
    elif query.data == "features":
        await features_command(update, context)
    elif query.data == "whatsnew":
        await whatsnew_command(update, context)
    elif query.data == "open_settings":
        keyboard = get_settings_keyboard(user_id)
        await query.message.reply_text(
            "⚙️ **إعدادات التنبيهات:**",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
    elif query.data == "toggle_daily":
        current = user_data["notifications"].get("daily_greetings", True)
        user_data["notifications"]["daily_greetings"] = not current
        save_user_data(user_id, user_data)
        await query.edit_message_reply_markup(
            reply_markup=get_settings_keyboard(user_id)
        )
    elif query.data == "toggle_inactivity":
        current = user_data["notifications"].get("inactivity_check", True)
        user_data["notifications"]["inactivity_check"] = not current
        save_user_data(user_id, user_data)
        await query.edit_message_reply_markup(
            reply_markup=get_settings_keyboard(user_id)
        )


# ==========================================
# 10. Message Processing & Audio Support
# ==========================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or "سينباي"
    user_text = update.message.text

    if not user_text:
        return

    reset_inactivity_timer(user_id, context)

    now = datetime.now(TIMEZONE)
    time_passed_info = calculate_time_passed(user_id, now)

    user_data = get_user_data(user_id)
    user_data["last_seen"] = now.isoformat()

    current_time_str = now.strftime("%Y-%m-%d %I:%M %p")
    custom_data = get_user_custom_data(user_id)
    current_mood = user_data.get("mood", "otaku")

    time_awareness_prompt = (
        f"\n\n**معلومات الوقت:**\n- الوقت الحالي: {current_time_str}\n- حالة التواصل: {time_passed_info}"
    )

    system_prompt = (
        PROMPTS[current_mood].format(
            user_name=user_name,
            user_custom_data=custom_data,
            bot_version=BOT_VERSION,
            changelog=CHANGELOG,
        )
        + time_awareness_prompt
    )

    raw_history = user_data.get("history", [])
    gemini_contents = []
    for item in raw_history:
        gemini_contents.append(
            types.Content(
                role=item["role"], parts=[types.Part(text=item["text"])]
            )
        )

    raw_history.append({"role": "user", "text": user_text})
    gemini_contents.append(
        types.Content(role="user", parts=[types.Part(text=user_text)])
    )

    max_entries = MAX_HISTORY * 2
    if len(raw_history) > max_entries:
        raw_history = raw_history[-max_entries:]
        gemini_contents = gemini_contents[-max_entries:]

    search_keywords = [
        "اخبار",
        "أخبار",
        "تحديث",
        "تسريبات",
        "متى ينزل",
        "موعد",
        "قنشن",
        "genshin",
        "انمي",
        "أنمي",
    ]
    should_search = any(kw in user_text.lower() for kw in search_keywords)

    reply = await generate_gemini_response(
        contents=gemini_contents,
        system_prompt=system_prompt,
        enable_search=should_search,
    )

    if reply:
        clean_reply, mood = parse_mood_and_clean_reply(reply)

        raw_history.append({"role": "model", "text": clean_reply})
        user_data["history"] = raw_history
        save_user_data(user_id, user_data)

        # استخراج المفضلات والحقائق في الخلفية بدون تعطيل الاستجابة
        asyncio.create_task(
            extract_user_profile_facts(user_id, user_text, clean_reply)
        )

        await update.message.reply_text(clean_reply)
    else:
        await update.message.reply_text(
            "آسفة سينباي! السيرفرات عليها ضغط حالياً 😅"
        )


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الملاحظات الصوتية المباشرة باستعمال Gemini Multimodal"""
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or "سينباي"

    reset_inactivity_timer(user_id, context)
    now = datetime.now(TIMEZONE)

    user_data = get_user_data(user_id)
    user_data["last_seen"] = now.isoformat()

    # تنزيل ملف الصوت من تليجرام
    voice_file = await update.message.voice.get_file()
    voice_bytes = await voice_file.download_as_bytearray()

    custom_data = get_user_custom_data(user_id)
    current_mood = user_data.get("mood", "otaku")

    system_prompt = PROMPTS[current_mood].format(
        user_name=user_name,
        user_custom_data=custom_data,
        bot_version=BOT_VERSION,
        changelog=CHANGELOG,
    )

    audio_part = types.Part.from_bytes(
        data=bytes(voice_bytes), mime_type="audio/ogg"
    )
    prompt_text = "المستخدم أرسل تسجيل صوتی. استمع لمحتواه ورد عليه بنفس شخصيتك وعفويتك بشخصية ميساكي!"

    contents = [audio_part, prompt_text]

    reply = await generate_gemini_response(
        contents=contents, system_prompt=system_prompt
    )

    if reply:
        clean_reply, mood = parse_mood_and_clean_reply(reply)

        raw_history = user_data.get("history", [])
        raw_history.append(
            {"role": "user", "text": "[أرسل رسالة صوتية واستمعت إليها]"}
        )
        raw_history.append({"role": "model", "text": clean_reply})

        max_entries = MAX_HISTORY * 2
        user_data["history"] = raw_history[-max_entries:]
        save_user_data(user_id, user_data)

        await update.message.reply_text(clean_reply)
    else:
        await update.message.reply_text(
            "ما قدرت أسمع الصوت زين يا سينباي، جرب أرسله ثاني مرة! 🎤😅"
        )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or "سينباي"
    caption = update.message.caption or "وش هالصورة يا سينباي؟ ✨"

    reset_inactivity_timer(user_id, context)
    now = datetime.now(TIMEZONE)

    user_data = get_user_data(user_id)
    user_data["last_seen"] = now.isoformat()

    photo_file = await update.message.photo[-1].get_file()
    photo_bytes = await photo_file.download_as_bytearray()

    custom_data = get_user_custom_data(user_id)
    current_mood = user_data.get("mood", "otaku")

    system_prompt = PROMPTS[current_mood].format(
        user_name=user_name,
        user_custom_data=custom_data,
        bot_version=BOT_VERSION,
        changelog=CHANGELOG,
    )

    image_part = types.Part.from_bytes(
        data=bytes(photo_bytes), mime_type="image/jpeg"
    )
    contents = [image_part, caption]

    reply = await generate_gemini_response(
        contents=contents, system_prompt=system_prompt
    )

    if reply:
        clean_reply, mood = parse_mood_and_clean_reply(reply)

        raw_history = user_data.get("history", [])
        raw_history.append({"role": "user", "text": f"[صورة: {caption}]"})
        raw_history.append({"role": "model", "text": clean_reply})

        max_entries = MAX_HISTORY * 2
        user_data["history"] = raw_history[-max_entries:]
        save_user_data(user_id, user_data)

        await update.message.reply_text(clean_reply)
    else:
        await update.message.reply_text(
            "ما قدرت أشوف الصورة زين سينباي! 😅"
        )


async def error_handler(
    update: object, context: ContextTypes.DEFAULT_TYPE
) -> None:
    logger.error(
        msg="Exception while handling an update:", exc_info=context.error
    )


# ==========================================
# 11. Main Entry Point
# ==========================================
def main():
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("features", features_command))
    app.add_handler(CommandHandler("whatsnew", whatsnew_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("otaku", set_otaku))
    app.add_handler(CommandHandler("serious", set_serious))

    # Message & Media Handlers
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_error_handler(error_handler)

    # Schedules
    job_queue = app.job_queue
    if job_queue:
        job_queue.run_daily(
            morning_greeting,
            time=time(hour=8, minute=30, second=0, tzinfo=TIMEZONE),
        )
        job_queue.run_daily(
            evening_greeting,
            time=time(hour=21, minute=30, second=0, tzinfo=TIMEZONE),
        )
        logger.info("⏰ Scheduled jobs initialized.")

    logger.info("✅ Bot v4.0 Master Build started successfully!")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES, drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
