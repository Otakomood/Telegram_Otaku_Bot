import os
import logging
import asyncio
import threading
from datetime import datetime, time
from zoneinfo import ZoneInfo
from http.server import HTTPServer, BaseHTTPRequestHandler
from google import genai
from google.genai import types
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes
)

# ====== إعداد السجلات ======
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ====== قراءة المفاتيح ======
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise ValueError("⚠️ خطأ حرج: لم يتم العثور على TELEGRAM_TOKEN أو GEMINI_API_KEY!")

# ====== تهيئة جيميناي ======
client = genai.Client(api_key=GEMINI_API_KEY)
MODEL_NAME = "gemini-3.6-flash"  

# ====== إصدار البوت وسجل التحديثات (Self-Awareness) ======
BOT_VERSION = "v3.0"
CHANGELOG = (
    "• ميزة الإدراك الزمني الحقيقي (معرفة الساعة والتاريخ والفارق الزمني بين المحادثات).\n"
    "• ميزة افتقاد المستخدم إذا غاب لمدة ساعتين والعتاب اللطيف تلقائياً.\n"
    "• ميزة الرسائل المجدولة اليومية (صباحاً 8:30 والمساء 9:30).\n"
    "• ميزة أمر /features لمعرفة كل الميزات وأمر /whatsnew للتحديثات."
)

# ====== الذاكرة، المزاج، والتخصيص ======
user_sessions = {}
user_moods = {}
user_profiles = {}
user_last_seen = {}  # ذاكرة حفظ تاريخ ووقت آخر رسالة لكل مستخدم
subscribers = set()
MAX_HISTORY = 10
INACTIVITY_TIMEOUT = 7200  # ساعتين (7200 ثانية)

# المنطقة الزمنية (توقيت مكة المكرمة / اليمن / السعودية)
TIMEZONE = ZoneInfo("Asia/Riyadh")

# ====== البرومبت الأساسي ======
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
        "أنتِ تعرفين قدراتك جيداً وإصدارك الحالي هو {bot_version}. إذا سألك المستخدم عن ميزاتك أو تحديثاتك الجديدة، تحدثي عنها بحماس واشرحي له التحديثات التالية:\n"
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
    )
}

def get_user_custom_data(user_id: int) -> str:
    profile = user_profiles.get(user_id, [])
    if not profile:
        return "- لا توجد مفضلات خاصة مسجلة بعد، تعرف عليه بفضولك العادي."
    return "- " + "\n- ".join(profile)

# ====== خادم ويب وهمي لإرضاء Render ======
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")
    def log_message(self, format, *args):
        pass

def run_web_server():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"🔥 خادم الويب الوهمي يعمل على المنفذ {port}")
    server.serve_forever()

# ====== تجديد مؤقت الغياب ======
def reset_inactivity_timer(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    job_name = f"inactivity_{user_id}"
    current_jobs = context.job_queue.get_jobs_by_name(job_name)
    for job in current_jobs:
        job.schedule_removal()
        
    context.job_queue.run_once(
        send_inactivity_message,
        when=INACTIVITY_TIMEOUT,
        name=job_name,
        user_id=user_id,
        data={"user_id": user_id}
    )

async def send_inactivity_message(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.user_id
    try:
        prompt = (
            "المستخدم غاب عنك ولم يراسك لمدة ساعتين! "
            "اكتبي رسالة عتاب لطيفة وعفوية بشخصية ميساكي تسألينه فيها بحماس وتذمر لطيف أين اختفى!"
        )
        response = await client.aio.models.generate_content(
            model=MODEL_NAME,
            contents=prompt
        )
        if response.text:
            await context.bot.send_message(chat_id=user_id, text=response.text)
    except Exception as e:
        logging.error(f"خطأ في إرسال رسالة الغياب للمستخدم {user_id}: {e}")

# ====== الأوامر (Command Handlers) ======

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    name = update.effective_user.first_name or "سينباي"
    subscribers.add(user_id)
    reset_inactivity_timer(user_id, context)
    user_last_seen[user_id] = datetime.now(TIMEZONE)
    
    keyboard = [
        [InlineKeyboardButton("⭐ الميزات الكاملة", callback_data="features"), InlineKeyboardButton("🔥 التحديثات الجديدة", callback_data="whatsnew")],
        [InlineKeyboardButton("🗑️ مسح الذاكرة", callback_data="reset")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
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
    await update.message.reply_text(msg, reply_markup=reply_markup)

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
    await update.message.reply_text("تم مسح ذاكرة المحادثة بنجاح! نفتح صفحة جديدة سينباي؟ ✨")

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
    data = query.data

    if data == "reset":
        user_sessions.pop(user_id, None)
        await query.message.reply_text("تم مسح الذاكرة بنجاح! ✨")
    elif data == "features":
        await features_command(update, context)
    elif data == "whatsnew":
        await whatsnew_command(update, context)

# ====== معالجة الرسائل والصور مع الحساب الزمني ======

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or "سينباي"
    subscribers.add(user_id)
    reset_inactivity_timer(user_id, context)
    user_text = update.message.text

    if not user_text:
        return

    # 1. معرفة الوقت والتاريخ الحالي بالتوقيت المحلي
    now = datetime.now(TIMEZONE)
    current_time_str = now.strftime("%Y-%m-%d %I:%M %p")

    # 2. حساب الفارق الزمني عن آخر تواصل
    time_passed_info = "هذه أول رسالة في الجلسة الحالية."
    if user_id in user_last_seen:
        last_time = user_last_seen[user_id]
        time_diff = now - last_time
        
        hours = int(time_diff.total_seconds() // 3600)
        minutes = int((time_diff.total_seconds() % 3600) // 60)
        
        if hours >= 24:
            days = hours // 24
            time_passed_info = f"مرّ {days} يوم على آخر تواصل بينكما."
        elif hours > 0:
            time_passed_info = f"مرّت {hours} ساعة و {minutes} دقيقة على آخر تواصل."
        else:
            time_passed_info = f"مرّت {minutes} دقيقة فقط على آخر تواصل."

    # تحديث تاريخ ووقت التواصل الأخير
    user_last_seen[user_id] = now

    if user_id not in user_sessions:
        user_sessions[user_id] = []

    custom_data = get_user_custom_data(user_id)
    current_mood = user_moods.get(user_id, "otaku")
    
    # 3. دمج السياق الزمني في التعليمات المخفية
    time_awareness_prompt = (
        f"\n\n**معلومات الوقت الحقيقي:**\n"
        f"- الوقت والتاريخ الحالي عندك الآن: {current_time_str}\n"
        f"- حالة التواصل: {time_passed_info}\n"
        f"- استغلي هذه المعلومات لتعرفي هل مرت أيام أم ساعات وتتفاعلي بأسلوب واقعي مع كلام المستخدم!"
    )

    system_prompt = PROMPTS[current_mood].format(
        user_name=user_name,
        user_custom_data=custom_data,
        bot_version=BOT_VERSION,
        changelog=CHANGELOG
    ) + time_awareness_prompt

    user_sessions[user_id].append(types.Content(role="user", parts=[types.Part(text=user_text)]))
    user_sessions[user_id] = user_sessions[user_id][-MAX_HISTORY:]
    
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
    )

    models_to_try = [MODEL_NAME, "gemini-2.0-flash"]
    reply = None

    for model in models_to_try:
        try:
            response = await client.aio.models.generate_content(
                model=model,
                contents=user_sessions[user_id],
                config=config
            )
            reply = response.text
            break
        except Exception as e:
            if "503" in str(e) or "UNAVAILABLE" in str(e):
                logging.warning(f"النموذج {model} يعاني من ضغط...")
                await asyncio.sleep(1)
                continue
            else:
                logging.error(f"حدث خطأ: {e}")
                break

    if reply:
        user_sessions[user_id].append(types.Content(role="model", parts=[types.Part(text=reply)]))
        await update.message.reply_text(reply)
    else:
        if user_sessions[user_id] and user_sessions[user_id][-1].role == "user":
            user_sessions[user_id].pop()
        await update.message.reply_text("آسفة يا سينباي! السيرفرات حالياً عليها ضغط عالي، جرب ترسل رسالتك بعد لحظات! 😅")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or "سينباي"
    subscribers.add(user_id)
    reset_inactivity_timer(user_id, context)
    caption = update.message.caption or "وش هالصورة يا سينباي؟ ✨"

    photo_file = await update.message.photo[-1].get_file()
    photo_bytes = await photo_file.download_as_bytearray()

    now = datetime.now(TIMEZONE)
    user_last_seen[user_id] = now

    if user_id not in user_sessions:
        user_sessions[user_id] = []

    custom_data = get_user_custom_data(user_id)
    current_mood = user_moods.get(user_id, "otaku")
    system_prompt = PROMPTS[current_mood].format(
        user_name=user_name,
        user_custom_data=custom_data,
        bot_version=BOT_VERSION,
        changelog=CHANGELOG
    )

    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
    )

    image_part = types.Part.from_bytes(data=bytes(photo_bytes), mime_type="image/jpeg")
    contents = [image_part, caption]

    models_to_try = [MODEL_NAME, "gemini-2.0-flash"]
    reply = None

    for model in models_to_try:
        try:
            response = await client.aio.models.generate_content(
                model=model,
                contents=contents,
                config=config
            )
            reply = response.text
            break
        except Exception as e:
            if "503" in str(e) or "UNAVAILABLE" in str(e):
                logging.warning(f"النموذج {model} يعاني من ضغط...")
                await asyncio.sleep(1)
                continue
            else:
                logging.error(f"حدث خطأ في الصورة: {e}")
                break

    if reply:
        user_sessions[user_id].append(types.Content(role="user", parts=[types.Part(text=f"[أرسل صورة مرفقة بهذا النص: {caption}]")]))
        user_sessions[user_id].append(types.Content(role="model", parts=[types.Part(text=reply)]))
        await update.message.reply_text(reply)
    else:
        await update.message.reply_text("آسفة يا سينباي! ما قدرت أشوف الصورة زين، السيرفرات مشغولة حالياً! 😅")

# ====== التفاعل المجدول (الصباح والمساء) ======

async def morning_greeting(context: ContextTypes.DEFAULT_TYPE):
    for user_id in list(subscribers):
        try:
            prompt = "اكتبي رسالة ترحيبية صباحية قصيرة ولطيفة جداً ومفعمة بالحماس والنشاط المعتاد بشخصية ميساكي سينباي لتبدئي بها اليوم مع المستخدم!"
            response = await client.aio.models.generate_content(model=MODEL_NAME, contents=prompt)
            if response.text:
                await context.bot.send_message(chat_id=user_id, text=response.text)
        except Exception as e:
            logging.error(f"خطأ في إرسال رسالة الصباح للمستخدم {user_id}: {e}")

async def evening_greeting(context: ContextTypes.DEFAULT_TYPE):
    for user_id in list(subscribers):
        try:
            prompt = "اكتبي رسالة مسائية قصيرة وبفضول لطيف تسألين فيها المستخدم بشخصية ميساكي عن ماذا فعل اليوم وكيف كان يومه!"
            response = await client.aio.models.generate_content(model=MODEL_NAME, contents=prompt)
            if response.text:
                await context.bot.send_message(chat_id=user_id, text=response.text)
        except Exception as e:
            logging.error(f"خطأ في إرسال رسالة المساء للمستخدم {user_id}: {e}")

# ====== معالج الأخطاء العام ======

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.error(msg="حدث استثناء لم يتم التقاطه:", exc_info=context.error)

# ====== التشغيل الرئيسي ======

def main():
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # تسجيل الأوامر
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("features", features_command))
    app.add_handler(CommandHandler("whatsnew", whatsnew_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("otaku", set_otaku))
    app.add_handler(CommandHandler("serious", set_serious))
    
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_error_handler(error_handler)
    
    # جدولة الرسائل
    job_queue = app.job_queue
    if job_queue:
        job_queue.run_daily(morning_greeting, time=time(hour=8, minute=30, second=0, tzinfo=TIMEZONE))
        job_queue.run_daily(evening_greeting, time=time(hour=21, minute=30, second=0, tzinfo=TIMEZONE))
        print("⏰ تم تفعيل جدولة الرسائل وتتبع الغياب التلقائي!")
    
    print("✅ البوت المطور شغال الآن بشبكة الميزات والإدراك الزمني الكامل!")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
