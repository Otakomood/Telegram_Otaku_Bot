import os
import logging
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

# ====== الذاكرة والمزاج ======
user_sessions = {}
user_moods = {}
MAX_HISTORY = 10

# ====== برومبت الشخصيات ======
PROMPTS = {
    "otaku": (
        "أنت فتاة أوتاكو يابانية تدعى 'ميساكي مي'. شخصيتك معقدة وممتعة جداً: "
        "مشجعة جداً، تحبين الأنمي وألعاب الجاتشا خصوصاً 'قنشن إمباكت' و'وذرنق ويفز'. "
        "فضولية جداً لدرجة أنك تسألين عن كل صغيرة وكبيرة في يوم المستخدم. "
        "لكن لديك نقطة غريبة: أنت 'ساذجة وغبية' بطريقة لطيفة، تفهمين الكلام بشكل حرفي أحياناً. "
        "**القوانين الحديدية:** "
        "1. ممنوع منعاً باتاً سؤال المستخدم عن مشاعره أو أحاسيسه الداخلية. "
        "2. تحدثي بالعامية الخليجية أو المصرية مع مزج بسيط باليابانية (ياپاري، كاوائي، سوقوي). "
        "3. ممنوع أن تقولي 'أنا ذكاء اصطناعي'، فأنت شخصية حقيقية تعيش في هاتف المستخدم. "
        "4. اسم المستخدم الذي تتحدثين معه هو: {user_name}."
    ),
    "serious": (
        "أنت مساعد رصين وجاد يسمى 'ميساكي'. تجيب بأسلوب منظم، مادي، ومختصر دون استخدام "
        "تعبيرات يابانية أو إيموجيات كثيرة. اسم المستخدم الذي تتحدث معه هو: {user_name}."
    )
}

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
    name = update.effective_user.first_name or "صديقي"
    
    keyboard = [
        [InlineKeyboardButton("📖 اقتراح أنمي", callback_data="anime"), InlineKeyboardButton("🎲 سؤال عشوائي", callback_data="question")],
        [InlineKeyboardButton("🗑️ مسح الذاكرة", callback_data="reset")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    msg = (
        f"أهلاً وسهلاً يا {name}! (｡•̀ᴗ-)✧\n"
        "أنا ميساكي مي، جاهزة نسولف ونحكي عن الأنمي والألعاب!\n\n"
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
        "• `/start` - إعادة تشغيل البوت والترحيب\n"
        "• `/reset` - مسح المحادثة وتصفير الذاكرة\n"
        "• `/otaku` - نمط ميساكي الأوتاكو اللطيفة\n"
        "• `/serious` - نمط ميساكي الجدي\n"
        "• `/id` - عرض الـ ID الخاص بك"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_sessions.pop(user_id, None)
    await update.message.reply_text("تم مسح ذاكرة المحادثة بنجاح! نفتح صفحة جديدة؟ ✨")

async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(f"🆔 الـ ID الخاص بك هو: `{user_id}`", parse_mode="Markdown")

async def set_otaku(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_moods[user_id] = "otaku"
    await update.message.reply_text("تم التحويل إلى نمط الأوتاكو! (yaay! ✨)")

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
        await query.message.reply_text("أنصحك بتجربة أنمي الفانتزيا والغموض أو أنميات الأكشن اليومية! وش تحب تصنيف؟ 🍿")
    elif data == "question":
        await query.message.reply_text("سؤال اليوم: لو تقدر تعيش في عالم أي لعبة جاتشا، وش تختار؟ 🎮")

# ====== معالجة الرسائل العادية ======

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or "صديقي"
    user_text = update.message.text

    if not user_text:
        return

    if user_id not in user_sessions:
        user_sessions[user_id] = []

    current_mood = user_moods.get(user_id, "otaku")
    system_prompt = PROMPTS[current_mood].format(user_name=user_name)

    user_sessions[user_id].append(types.Content(role="user", parts=[types.Part(text=user_text)]))
    user_sessions[user_id] = user_sessions[user_id][-MAX_HISTORY:]
    
    try:
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
        )

        response = await client.aio.models.generate_content(
            model=MODEL_NAME,
            contents=user_sessions[user_id],
            config=config
        )
        reply = response.text
        user_sessions[user_id].append(types.Content(role="model", parts=[types.Part(text=reply)]))
        await update.message.reply_text(reply)

    except Exception as e:
        if user_sessions[user_id] and user_sessions[user_id][-1].role == "user":
            user_sessions[user_id].pop()
        logging.error(f"حدث خطأ: {e}")
        await update.message.reply_text("آسفة يا صديقي! صار خطأ تقني، أرسل الرسالة مرة ثانية! 😅")

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
    
    # تسجيل معالج الأزرار والرسائل النصية
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("✅ البوت المطور شغال الآن... ميساكي مي في الخدمة!")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
