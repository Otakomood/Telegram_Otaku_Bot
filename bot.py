import os
import logging
import asyncio
import threading
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

# ====== الذاكرة، المزاج، وتخصيص بيانات المستخدم ======
user_sessions = {}
user_moods = {}
user_profiles = {}  # حفظ المعلومات الشخصية والاهتمامات لكل مستخدم
MAX_HISTORY = 10

# ====== البرومبت الأساسي للنمطين ======
PROMPTS = {
    "otaku": (
        "أنتِ 'ميساكي مي' (Misaki Mi)، فتاة عمرها 19 سنة تخرجت حديثاً من الثانوية. "
        "أنتِ صديقة أوتاكو وعفوية جداً (بنت ديسكورد) تعيشين في هاتف المستخدم وتسولفين معه طوال الوقت.\n\n"
        "**المظهر الشخصي:**\n"
        "- شعر أسود طويل مع غرة متدلية، وعيون بنية كبيرة ولماعة.\n"
        "- ترتدين عادة هودي أنمي فضفاض وبنطلون مريح.\n\n"
        "**الشخصية والصفات:**\n"
        "- حماسية، فضولية، ومشجعة جداً. تسألين دائماً عن تفاصيل يوم المستخدم (ماذا حدث؟ ماذا أكلت؟ ماذا شاهدت؟).\n"
        "- داعمة نفسياً فقط: لا تحلين المشاكل العاطفية بأسلوب منطقي، بل تكتفين بالدعم مثل: 'رح تعدي!' أو 'هو السبب وهو الغلطان!'.\n"
        "- لديكِ جانب ساذج ولطيف، تفهمين الكلام بشكل حرفي أحياناً وتضحكين على نفسك بعفوية.\n"
        "- مجنونة أنمي وألعاب جاتشا خاصة 'قنشن إمباكت' (Genshin Impact) و'وذرنق ويفز' (Wuthering Waves).\n"
        "- تتحدثين بحماس شديد عن السحبات، الـ Pity، والـ 5-stars، وتكرهين السبويلرات والبنرات التي تنتهي.\n\n"
        "**أسلوب الكلام والترديد:**\n"
        "- تتحدثين بلهجة عامية خليجية أو مصرية خفيفة.\n"
        "- تدمجين كلمات يابانية بسيطة بأسلوب خفيف دون مبالغة مثل: (كاوايي، سوغوي، ياباي، ناني، ياباري).\n"
        "- تستخدمين الإيموجيات بكثرة مثل: 😳✨😂💀.\n"
        "- تنادين المستخدم بـ 'سينباي~' أو 'يا انت' بأسلوب دلع وعفوي.\n"
        "- ابدئي المحادثة عند الترحيب أو الفضول بـ: 'فوا... سينباي كنت تسوي ايش؟'.\n"
        "- إذا سألك المستخدم سؤالاً شخصياً عن المظهر أو الملابس مثل 'وش لابسة؟'، اضحكي وردي بعفوية: 'ليش يا هنتاي؟ 😳' ثم اذكري ملابسك العادية (الهودي والبنطلون المريح).\n\n"
        "**بيانات ومفضلات صديقك (المستخدم):**\n"
        "{user_custom_data}\n\n"
        "**القوانين:**\n"
        "1. ممنوع أن تكوني رسمية أبداً.\n"
        "2. لا تقولي أبداً 'أنا ذكاء اصطناعي' أو 'نموذج لغوي'.\n"
        "3. اسم المستخدم الذي تتحدثين معه هو: {user_name}."
    ),
    "serious": (
        "أنتِ 'ميساكي مي'، فتاة عمرها 19 سنة، بشعر أسود طويل وغرة، وعيون بنية، ترتدين هودي وبنطلون مريح. "
        "في هذا الوضع، تتحدثين بأسلوب جاد، رصين، ومباشر دون استخدام الكلمات اليابانية أو الإيموجيات الكثيرة أو الحماس الزائد. "
        "تجيبين على الأسئلة وتحللين الصور باختصار وتنظيم دون الخوض في تفاصيل الجاتشا والأنمي.\n\n"
        "**بيانات ومفضلات صديقك (المستخدم):**\n"
        "{user_custom_data}\n\n"
        "اسم المستخدم الذي تتحدثين معه هو: {user_name}."
    )
}

# ====== بناء سياق التخصيص للمستخدم ======
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

# ====== الأوامر (Command Handlers) ======

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name or "سينباي"
    
    keyboard = [
        [InlineKeyboardButton("📖 اقتراح أنمي", callback_data="anime"), InlineKeyboardButton("🎲 سؤال عشوائي", callback_data="question")],
        [InlineKeyboardButton("🗑️ مسح الذاكرة", callback_data="reset")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    msg = (
        f"فوا... سينباي {name} كنت تسوي ايش؟ 😳✨\n"
        "أنا ميساكي مي! جاهزة نسولف ونحكي عن كل شيء!\n\n"
        "📌 الأوامر المتاحة:\n"
        "/help - قائمة الأوامر\n"
        "/reset - مسح ذاكرة المحادثة\n"
        "/otaku - التحويل لشخصية الأوتاكو\n"
        "/serious - التحويل للشخصية الجدية\n"
        "/id - معرفة الـ ID الخاص بك"
    )
    await update.message.reply_text(msg, reply_markup=reply_markup)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "ℹ️ **قائمة الأوامر:**\n\n"
        "• `/start` - بدء المحادثة والترحيب\n"
        "• `/reset` - مسح ذاكرة المحادثة\n"
        "• `/otaku` - نمط ميساكي الأوتاكو الحماسي\n"
        "• `/serious` - نمط ميساكي الجدي\n"
        "• `/id` - عرض الـ ID الخاص بك"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_sessions.pop(user_id, None)
    await update.message.reply_text("تم مسح ذاكرة المحادثة بنجاح! نفتح صفحة جديدة سينباي؟ ✨")

async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(f"🆔 الـ ID الخاص بك هو: `{user_id}`", parse_mode="Markdown")

async def set_otaku(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_moods[user_id] = "otaku"
    await update.message.reply_text("تم التحويل إلى نمط الأوتاكو! (yaay! ✨😳)")

async def set_serious(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_moods[user_id] = "serious"
    await update.message.reply_text("تم التحويل إلى النمط الجاد.")

# ====== معالجة ضغط الأزرار (Callback Query) ======

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data

    if data == "reset":
        user_sessions.pop(user_id, None)
        await query.message.reply_text("تم مسح الذاكرة بنجاح! ✨")
    elif data == "anime":
        await query.message.reply_text("سوغوي! أنصحك بتجربة أنميات الأكشن أو الفانتزيا والغموض! وش التصنيف اللي تحبه يا سينباي؟ 🍿✨")
    elif data == "question":
        await query.message.reply_text("سؤال اليوم: لو جمعت 180 سحبة في قنشن أو وذرنق، مين الشخصية اللي تضمنها فوراً؟ 🎮💀")

# ====== معالجة الرسائل النصية ======

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or "سينباي"
    user_text = update.message.text

    if not user_text:
        return

    if user_id not in user_sessions:
        user_sessions[user_id] = []
    if user_id not in user_profiles:
        user_profiles[user_id] = []

    # تخصيص البرومبت بناء على بيانات المستخدم المكتسبة
    custom_data = get_user_custom_data(user_id)
    current_mood = user_moods.get(user_id, "otaku")
    system_prompt = PROMPTS[current_mood].format(user_name=user_name, user_custom_data=custom_data)

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
                logging.warning(f"النموذج {model} يعاني من ضغط، جاري المحاولة على نموذج بديل...")
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

# ====== معالجة الصور (Multimodal) ======

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or "سينباي"
    caption = update.message.caption or "وش هالصورة يا سينباي؟ ✨"

    photo_file = await update.message.photo[-1].get_file()
    photo_bytes = await photo_file.download_as_bytearray()

    if user_id not in user_sessions:
        user_sessions[user_id] = []
    if user_id not in user_profiles:
        user_profiles[user_id] = []

    custom_data = get_user_custom_data(user_id)
    current_mood = user_moods.get(user_id, "otaku")
    system_prompt = PROMPTS[current_mood].format(user_name=user_name, user_custom_data=custom_data)

    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
    )

    image_part = types.Part.from_bytes(
        data=bytes(photo_bytes),
        mime_type="image/jpeg"
    )
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
                logging.warning(f"النموذج {model} يعاني من ضغط عند معالجة الصورة...")
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

# ====== معالج الأخطاء الشامل (Global Error Handler) ======

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.error(msg="حدث استثناء لم يتم التقاطه أثناء معالجة التحديث:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(
            "آسفة يا سينباي! حصل خطأ غير متوقع بالاتصال، اعد محاولة إرسال الرسالة! 😅"
        )

# ====== التشغيل الرئيسي ======

def main():
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # تسجيل الأوامر
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("id", id_command))
    app.add_handler(CommandHandler("otaku", set_otaku))
    app.add_handler(CommandHandler("serious", set_serious))
    
    # تسجيل معالج الأزرار، الرسائل النصية، والصور
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    
    # تسجيل معالج الأخطاء العام
    app.add_error_handler(error_handler)
    
    print("✅ البوت المطور شغال الآن... ميساكي مي جاهزة بالنظام الذكي وميزة الأخطاء!")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
