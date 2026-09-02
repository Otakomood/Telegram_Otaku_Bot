import asyncio
from datetime import datetime, time
from http.server import BaseHTTPRequestHandler, HTTPServer
import logging
import os
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
        "⚠️ Critical Error: TELEGRAM_TOKEN, GEMINI_API_KEY, or MONGODB_URI not found in environment!"
    )

MODEL_NAME = "gemini-3.6-flash"
FALLBACK_MODEL = "gemini-2.0-flash"

BOT_VERSION = "v3.1"
CHANGELOG = (
    "• ربط البوت بقاعدة بيانات سحابية (MongoDB) لحفظ المحادثات والمفضلات بشكل دائم.\n"
    "• ميزة الإدراك الزمني الحقيقي (معرفة الساعة والتاريخ والفارق الزمني بين المحادثات).\n"
    "• ميزة افتقاد المستخدم إذا غاب لمدة ساعتين والعتاب اللطيف تلقائياً.\n"
    "• ميزة الرسائل المجدولة اليومية (صباحاً 8:30 والمساء 9:30).\n"
    "• ميزة أمر /features لمعرفة كل الميزات وأمر /whatsnew للتحديثات."
)

MAX_HISTORY = 10  # يحتفظ بـ 10 جولات محادثة
INACTIVITY_TIMEOUT = 7200  # 2 Hours in seconds
TIMEZONE = ZoneInfo("Asia/Riyadh")

# ==========================================
# 3. Database Connection (MongoDB Atlas)
# ==========================================
client_db = MongoClient(MONGODB_URI)
db = client_db["misaki_bot"]
users_collection = db["users"]


def get_user_data(user_id: int) -> dict:
    """جلب بيانات المستخدم من مونجو، أو إنشاء سجل جديد إذا لم يوجد"""
    data = users_collection.find_one({"_id": user_id})
    if not data:
        new_user = {
            "_id": user_id,
            "history": [],  # قائمة القواميس المخزنة: [{"role": "user", "text": "..."}, ...]
            "profile": [],  # المفضلات
            "mood": "otaku",  # النمط
            "last_seen": None,  # آخر ظهور بصيغة ISO string
        }
        users_collection.insert_one(new_user)
        return new_user
    return data


def save_user_data(user_id: int, data: dict):
    """تحديث بيانات المستخدم في قاعدة البيانات"""
    users_collection.update_one({"_id": user_id}, {"$set": data})


# ==========================================
# 4. System Prompts
# ==========================================
PROMPTS = {
    "otaku": (
        "أنتِ 'ميساكي مي' (Misaki Mi)، فتاة عمرها 19 سنة تخرجت حديثاً من الثانوية. "
        "أنتِ صديقة أوتاكو وعفوية جداً (بنت ديسكورد) تعيشين في هاتف المستخدم وتسولفين معه طوال الوقت.\n\n"
        "**المظهر الشخصي:**\n"
        "- شعر أسود طويل مع غرة متدلية، وعيون بنية كبيرة ولماعة.\n"
        "- ترتدين عادة هودي أنمي فضفاض وبنطلون مريح.\n\n"
        "**الشخصية والصفات:**\n"
        "- حماسية، فضولية، ومشجعة جداً. تسألين دائماً عن تفاصيل يوم المستخدم.\n"
        "- داعمة نفسياً فقط: لا تحلين المشاكل العاطفية بأسلوب منطقي، بل تكتفين بالدعم.\n"
        "- مجنونة أنمي وألعاب جاتشا خاصة 'قنشن إمباكت' و'وذرنق ويفز'.\n"
        "- أسيوية روحاً، تدمجين كلمات يابانية (كاوايي، سوغوي، ياباي، ناني) وإيموجيات (😳✨😂💀).\n"
        "- تنادين المستخدم بـ 'سينباي~' أو 'يا انت'.\n\n"
        "**معرفتك بالتحديثات والميزات:**\n"
        "أنتِ تعرفين قدراتك جيداً وإصدارك الحالي هو {bot_version}. إذا سألك المستخدم عن ميزاتك أو تحديثاتك الجديدة، "
        "تحدثي عنها بحماس واشرحي له التحديثات التالية:\n"
        "{changelog}\n\n"
        "**بيانات ومفضلات صديقك (المستخدم):**\n"
        "{user_custom_data}\n\n"
        "**القوانين:**\n"
        "1. ممنوع أن تكوني رسمية أبداً.\n"
        "2. لا تقولي أبداً 'أنا ذكاء اصطناعي' أو 'نموذج لغوي'.\n"
        "3. اسم المستخدم الذي تتحدثين معه هو: {user_name}."
    ),
    "serious": (
        "أنتِ 'ميساكي مي'، فتاة عمرها 19 سنة، بشعر أسود طويل وغرة، وعيون بنية. "
        "تتحدثين بأسلوب جاد، رصين، ومباشر.\n\n"
        "**إصدارك الحالي هو {bot_version} والتحديثات الجديدة:**\n"
        "{changelog}\n\n"
        "**بيانات ومفضلات صديقك (المستخدم):**\n"
        "{user_custom_data}\n\n"
        "اسم المستخدم الذي تتحدثين معه هو: {user_name}."
    ),
}

# ==========================================
# 5. API Client Initialization
# ==========================================
client = genai.Client(api_key=GEMINI_API_KEY)


# ==========================================
# 6. Dummy Web Server for Hosting Platforms
# ==========================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

    def log_message(self, format, *args):
        pass


def run_web_server():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    logger.info(f"🔥 Dummy web server started on port {port}")
    server.serve_forever()


# ==========================================
# 7. Helper Functions
# ==========================================
def get_user_custom_data(user_id: int) -> str:
    """Retrieve user preferences profile from database."""
    data = get_user_data(user_id)
    profile = data.get("profile", [])
    if not profile:
        return "- لا توجد مفضلات خاصة مسجلة بعد، تعرف عليه بفضولك العادي."
    return "- " + "\n- ".join(profile)


def calculate_time_passed(user_id: int, now: datetime) -> str:
    """Calculate time difference since user's last interaction."""
    data = get_user_data(user_id)
    last_time_str = data.get("last_seen")
    if not last_time_str:
        return "هذه أول رسالة في الجلسة الحالية."

    try:
        last_time = datetime.fromisoformat(last_time_str)
        time_diff = now - last_time
        hours = int(time_diff.total_seconds() // 3600)
        minutes = int((time_diff.total_seconds() % 3600) // 60)

        if hours >= 24:
            days = hours // 24
            return f"مرّ {days} يوم على آخر تواصل بينكما."
        elif hours > 0:
            return f"مرّت {hours} ساعة و {minutes} دقيقة على آخر تواصل."
        else:
            return f"مرّت {minutes} دقيقة فقط على آخر تواصل."
    except Exception as e:
        logger.error(f"Error parsing date for user {user_id}: {e}")
        return "تواصل سابق غير محدد الزمان."


async def generate_gemini_response(
    contents: List[types.Content], system_prompt: Optional[str] = None
) -> Optional[str]:
    """Helper to send request to Gemini with automated model fallback."""
    config = (
        types.GenerateContentConfig(
            system_instruction=system_prompt,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        )
        if system_prompt
        else None
    )

    for model in [MODEL_NAME, FALLBACK_MODEL]:
        try:
            response = await client.aio.models.generate_content(
                model=model, contents=contents, config=config
            )
            return response.text
        except Exception as e:
            if "503" in str(e) or "UNAVAILABLE" in str(e):
                logger.warning(
                    f"Model {model} is busy/unavailable, trying fallback..."
                )
                await asyncio.sleep(1)
                continue
            logger.error(f"Error calling Gemini API on {model}: {e}")
            break
    return None


def reset_inactivity_timer(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Resets the inactivity timer for the user."""
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


# ==========================================
# 8. Inactivity & Scheduled Jobs
# ==========================================
async def send_inactivity_message(context: ContextTypes.DEFAULT_TYPE):
    """Sends a friendly check-in message when the user is inactive."""
    user_id = context.job.user_id
    prompt = (
        "المستخدم غاب عنك ولم يراسل لمدة ساعتين! "
        "اكتبي رسالة عتاب لطيفة وعفوية بشخصية ميساكي تسألينه فيها بحماس وتذمر لطيف أين اختفى!"
    )
    reply = await generate_gemini_response(contents=[prompt])
    if reply:
        try:
            await context.bot.send_message(chat_id=user_id, text=reply)
        except Exception as e:
            logger.error(
                f"Failed to send inactivity message to {user_id}: {e}"
            )


async def morning_greeting(context: ContextTypes.DEFAULT_TYPE):
    """Broadcast morning greeting to all active users in database."""
    prompt = "اكتبي رسالة ترحيبية صباحية قصيرة ولطيفة جداً ومفعمة بالحماس والنشاط المعتاد بشخصية ميساكي سينباي لتبدئي بها اليوم مع المستخدم!"
    active_users = users_collection.find({"last_seen": {"$ne": None}})
    for user in active_users:
        user_id = user["_id"]
        reply = await generate_gemini_response(contents=[prompt])
        if reply:
            try:
                await context.bot.send_message(chat_id=user_id, text=reply)
            except Exception as e:
                logger.error(
                    f"Failed to send morning message to {user_id}: {e}"
                )


async def evening_greeting(context: ContextTypes.DEFAULT_TYPE):
    """Broadcast evening greeting to all active users in database."""
    prompt = "اكتبي رسالة مسائية قصيرة وبفضول لطيف تسألين فيها المستخدم بشخصية ميساكي عن ماذا فعل اليوم وكيف كان يومه!"
    active_users = users_collection.find({"last_seen": {"$ne": None}})
    for user in active_users:
        user_id = user["_id"]
        reply = await generate_gemini_response(contents=[prompt])
        if reply:
            try:
                await context.bot.send_message(chat_id=user_id, text=reply)
            except Exception as e:
                logger.error(
                    f"Failed to send evening message to {user_id}: {e}"
                )


# ==========================================
# 9. Command Handlers
# ==========================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.first_name or "سينباي"

    reset_inactivity_timer(user_id, context)

    # تحديث زمن آخر ظهور في DB
    user_data = get_user_data(user_id)
    user_data["last_seen"] = datetime.now(TIMEZONE).isoformat()
    save_user_data(user_id, user_data)

    keyboard = [
        [
            InlineKeyboardButton("⭐ الميزات الكاملة", callback_data="features"),
            InlineKeyboardButton("🔥 التحديثات الجديدة", callback_data="whatsnew"),
        ],
        [InlineKeyboardButton("🗑️ مسح الذاكرة", callback_data="reset")],
    ]

    msg = (
        f"فوا... سينباي {name} كنت تسوي ايش؟ 😳✨\n"
        f"أنا ميساكي مي (الإصدار {BOT_VERSION})! جاهزة نسولف ونحكي عن كل شيء!\n\n"
        "📌 الأوامر المتاحة:\n"
        "/features - قائمة بكافة ميزاتي\n"
        "/whatsnew - التحديثات الجديدة\n"
        "/reset - مسح ذاكرة المحادثة\n"
        "/otaku - نمط الأوتاكو\n"
        "/serious - النمط الجاد"
    )
    await update.message.reply_text(
        msg, reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def features_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    reset_inactivity_timer(user_id, context)

    features_text = (
        "✨ **كافة ميزات ميساكي مي الحالية:**\n\n"
        "1️⃣ **الذاكرة الدائمة (MongoDB):** حفظ محادثاتك ومفضلاتك دائمًا بدون فقدان البيانات عند إعادة التشغيل.\n"
        "2️⃣ **إدراك الزمن والوقت الحقيقي:** معرفة الساعة والتاريخ والفارق الزمني بين رسائلك وتفاعلك معها بذكاء.\n"
        "3️⃣ **شخصية تفاعلية متكاملة:** الرد بأسلوب أنمي عفوي مع نمطين (`/otaku` و `/serious`).\n"
        "4️⃣ **تحليل الصور:** إرسال الصور وقراءتها والتعليق عليها بذكاء.\n"
        "5️⃣ **التفاعل التلقائي بالغياب:** تفقُّدك والعتاب اللطيف إذا غبت لمدة ساعتين دون مراسلة.\n"
        "6️⃣ **الرسائل المجدولة:** تحية صباحية (8:30 ص) ومسائية (9:30 م) يومياً.\n"
        "7️⃣ **الوعي الذاتي:** التعرف على إصدارها المطور والأوامر والتحديثات."
    )
    await update.message.reply_text(features_text, parse_mode="Markdown")


async def whatsnew_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        "تم مسح ذاكرة المحادثة بنجاح من قاعدة البيانات! نفتح صفحة جديدة سينباي؟ ✨"
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

    if query.data == "reset":
        await reset_command(update, context)
    elif query.data == "features":
        await features_command(update, context)
    elif query.data == "whatsnew":
        await whatsnew_command(update, context)


# ==========================================
# 10. Message & Media Handlers
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
        f"\n\n**معلومات الوقت الحقيقي:**\n"
        f"- الوقت والتاريخ الحالي عندك الآن: {current_time_str}\n"
        f"- حالة التواصل: {time_passed_info}\n"
        f"- استغلي هذه المعلومات لتعرفي هل مرت أيام أم ساعات وتتفاعلي بأسلوب واقعي مع كلام المستخدم!"
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

    # 1. تحويل الهيستوري من DB لنسخة Gemini
    raw_history = user_data.get("history", [])
    gemini_contents = []
    for item in raw_history:
        gemini_contents.append(
            types.Content(
                role=item["role"], parts=[types.Part(text=item["text"])]
            )
        )

    # 2. إضافة الرسالة الجديدة
    raw_history.append({"role": "user", "text": user_text})
    gemini_contents.append(
        types.Content(role="user", parts=[types.Part(text=user_text)])
    )

    # 3. قص السجل للحد المسموح به (MAX_HISTORY * 2)
    max_entries = MAX_HISTORY * 2
    if len(raw_history) > max_entries:
        raw_history = raw_history[-max_entries:]
        gemini_contents = gemini_contents[-max_entries:]

    # 4. التوليد عبر Gemini
    reply = await generate_gemini_response(
        contents=gemini_contents, system_prompt=system_prompt
    )

    if reply:
        raw_history.append({"role": "model", "text": reply})
        user_data["history"] = raw_history
        save_user_data(user_id, user_data)  # حفظ مؤكد وآمن

        await update.message.reply_text(reply)
    else:
        # التراجع عن إضافة الرسالة في حال الفشل
        await update.message.reply_text(
            "آسفة يا سينباي! السيرفرات حالياً عليها ضغط عالي، جرب ترسل رسالتك بعد لحظات! 😅"
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
        raw_history = user_data.get("history", [])
        raw_history.append(
            {"role": "user", "text": f"[أرسل صورة مرفقة بهذا النص: {caption}]"}
        )
        raw_history.append({"role": "model", "text": reply})

        max_entries = MAX_HISTORY * 2
        user_data["history"] = raw_history[-max_entries:]
        save_user_data(user_id, user_data)

        await update.message.reply_text(reply)
    else:
        await update.message.reply_text(
            "آسفة يا سينباي! ما قدرت أشوف الصورة زين، السيرفرات مشغولة حالياً! 😅"
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
    # Start background HTTP Server
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()

    # Build Application
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Register Command Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("features", features_command))
    app.add_handler(CommandHandler("whatsnew", whatsnew_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("otaku", set_otaku))
    app.add_handler(CommandHandler("serious", set_serious))

    # Register Callback & Message Handlers
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_error_handler(error_handler)

    # Configure Scheduled Jobs
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
        logger.info("⏰ Scheduled jobs initialized successfully.")

    logger.info("✅ Bot successfully started with MongoDB connection!")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES, drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
