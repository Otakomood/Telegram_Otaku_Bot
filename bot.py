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

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise ValueError(
        "⚠️ Critical Error: TELEGRAM_TOKEN or GEMINI_API_KEY not found in environment!"
    )

MODEL_NAME = "gemini-3.6-flash"
FALLBACK_MODEL = "gemini-2.0-flash"

BOT_VERSION = "v3.0"
CHANGELOG = (
    "• ميزة الإدراك الزمني الحقيقي (معرفة الساعة والتاريخ والفارق الزمني بين المحادثات).\n"
    "• ميزة افتقاد المستخدم إذا غاب لمدة ساعتين والعتاب اللطيف تلقائياً.\n"
    "• ميزة الرسائل المجدولة اليومية (صباحاً 8:30 والمساء 9:30).\n"
    "• ميزة أمر /features لمعرفة كل الميزات وأمر /whatsnew للتحديثات."
)

MAX_HISTORY = 10
INACTIVITY_TIMEOUT = 7200  # 2 Hours in seconds
TIMEZONE = ZoneInfo("Asia/Riyadh")

# ==========================================
# 3. Memory & State Management
# ==========================================
user_sessions = {}
user_moods = {}
user_profiles = {}
user_last_seen = {}

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
        "- أسيوية روحاً، تدمجين كلمات يابانية قليلة (كاوايي، سوغوي، ياباي، ناني) وإيموجيات قليلة أيضا (😳✨😂💀).\n"
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
    """Retrieve user preferences profile or default string."""
    profile = user_profiles.get(user_id, [])
    if not profile:
        return "- لا توجد مفضلات خاصة مسجلة بعد، تعرف عليه بفضولك العادي."
    return "- " + "\n- ".join(profile)


def calculate_time_passed(user_id: int, now: datetime) -> str:
    """Calculate time difference since user's last interaction."""
    if user_id not in user_last_seen:
        return "هذه أول رسالة في الجلسة الحالية."

    last_time = user_last_seen[user_id]
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
    """Broadcast morning greeting to all active users."""
    prompt = "اكتبي رسالة ترحيبية صباحية قصيرة ولطيفة جداً ومفعمة بالحماس والنشاط المعتاد بشخصية ميساكي سينباي لتبدئي بها اليوم مع المستخدم!"
    for user_id in list(user_last_seen.keys()):
        reply = await generate_gemini_response(contents=[prompt])
        if reply:
            try:
                await context.bot.send_message(chat_id=user_id, text=reply)
            except Exception as e:
                logger.error(
                    f"Failed to send morning message to {user_id}: {e}"
                )


async def evening_greeting(context: ContextTypes.DEFAULT_TYPE):
    """Broadcast evening greeting to all active users."""
    prompt = "اكتبي رسالة مسائية قصيرة وبفضول لطيف تسألين فيها المستخدم بشخصية ميساكي عن ماذا فعل اليوم وكيف كان يومه!"
    for user_id in list(user_last_seen.keys()):
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
    user_last_seen[user_id] = datetime.now(TIMEZONE)

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
        "1️⃣ **إدراك الزمن والوقت الحقيقي:** معرفة الساعة والتاريخ والفارق الزمني بين رسائلك وتفاعلك معها بذكاء.\n"
        "2️⃣ **شخصية تفاعلية متكاملة:** الرد بأسلوب أنمي عفوي مع نمطين (`/otaku` و `/serious`).\n"
        "3️⃣ **تحليل الصور:** إرسال الصور وقراءتها والتعليق عليها بذكاء.\n"
        "4️⃣ **التفاعل التلقائي بالغياب:** تفقُّدك والعتاب اللطيف إذا غبت لمدة ساعتين دون مراسلة.\n"
        "5️⃣ **الرسائل المجدولة:** تحية صباحية (8:30 ص) ومسائية (9:30 م) يومياً.\n"
        "6️⃣ **الذاكرة والتخصيص:** حفظ مفضلاتك واهتماماتك والاطلاع عليها.\n"
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
    user_sessions.pop(user_id, None)
    reset_inactivity_timer(user_id, context)
    await update.message.reply_text(
        "تم مسح ذاكرة المحادثة بنجاح! نفتح صفحة جديدة سينباي؟ ✨"
    )


async def set_otaku(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_moods[user_id] = "otaku"
    await update.message.reply_text("تم التحويل إلى نمط الأوتاكو! (yaay! ✨😳)")


async def set_serious(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_moods[user_id] = "serious"
    await update.message.reply_text("تم التحويل إلى النمط الجاد.")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    reset_inactivity_timer(user_id, context)

    if query.data == "reset":
        user_sessions.pop(user_id, None)
        await query.message.reply_text("تم مسح الذاكرة بنجاح! ✨")
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
    user_last_seen[user_id] = now

    current_time_str = now.strftime("%Y-%m-%d %I:%M %p")
    custom_data = get_user_custom_data(user_id)
    current_mood = user_moods.get(user_id, "otaku")

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

    if user_id not in user_sessions:
        user_sessions[user_id] = []

    user_sessions[user_id].append(
        types.Content(role="user", parts=[types.Part(text=user_text)])
    )
    user_sessions[user_id] = user_sessions[user_id][-MAX_HISTORY:]

    reply = await generate_gemini_response(
        contents=user_sessions[user_id], system_prompt=system_prompt
    )

    if reply:
        user_sessions[user_id].append(
            types.Content(role="model", parts=[types.Part(text=reply)])
        )
        await update.message.reply_text(reply)
    else:
        if (
            user_sessions[user_id]
            and user_sessions[user_id][-1].role == "user"
        ):
            user_sessions[user_id].pop()
        await update.message.reply_text(
            "آسفة يا سينباي! السيرفرات حالياً عليها ضغط عالي، جرب ترسل رسالتك بعد لحظات! 😅"
        )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or "سينباي"
    caption = update.message.caption or "وش هالصورة يا سينباي؟ ✨"

    reset_inactivity_timer(user_id, context)
    user_last_seen[user_id] = datetime.now(TIMEZONE)

    photo_file = await update.message.photo[-1].get_file()
    photo_bytes = await photo_file.download_as_bytearray()

    custom_data = get_user_custom_data(user_id)
    current_mood = user_moods.get(user_id, "otaku")

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
        if user_id not in user_sessions:
            user_sessions[user_id] = []

        user_sessions[user_id].append(
            types.Content(
                role="user",
                parts=[
                    types.Part(
                        text=f"[أرسل صورة مرفقة بهذا النص: {caption}]"
                    )
                ],
            )
        )
        user_sessions[user_id].append(
            types.Content(role="model", parts=[types.Part(text=reply)])
        )
        await update.message.reply_text(reply)
    else:
        await update.message.reply_text(
            "آسفة يا سينباي! ما قدرت أشوف الصورة زين، السيرفرات مشغولة حالياً! 😅"
        )


async def error_handler(
    update: object, context: ContextTypes.DEFAULT_TYPE
) -> None:
    logger.error(msg="Exception while handling an update:", exc_info=context.error)


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

    logger.info("✅ Bot successfully started!")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
