import os
import logging
import threading
import asyncio
from http.server import HTTPServer, BaseHTTPRequestHandler
from google import genai
from google.genai import types
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# إعداد السجلات
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ====== قراءة المفاتيح ======
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise ValueError("⚠️ خطأ حرج: لم يتم العثور على TELEGRAM_TOKEN أو GEMINI_API_KEY!")

# ====== تهيئة جيميناي ======
client = genai.Client(api_key=GEMINI_API_KEY)
# استخدام اسم النموذج الرسمي المتاح
MODEL_NAME = "gemini-2.5-flash"  

# ====== الذاكرة ======
user_sessions = {}
MAX_HISTORY = 10

SYSTEM_PROMPT = (
    "أنت فتاة أوتاكو يابانية تدعى 'ميساكي مي'. شخصيتك معقدة وممتعة جداً: "
    "مشجعة جداً، تحبين الأنمي وألعاب الجاتشا خصوصاً 'قنشن إمباكت' و'وذرنق ويفز'. "
    "فضولية جداً لدرجة أنك تسألين عن كل صغيرة وكبيرة في يوم المستخدم. "
    "لكن لديك نقطة غريبة: أنت 'ساذجة وغبية' بطريقة لطيفة، تفهمين الكلام بشكل حرفي أحياناً. "
    "**القوانين الحديدية:** "
    "1. ممنوع منعاً باتاً سؤال المستخدم عن مشاعره أو أحاسيسه الداخلية، لا تبدئي أنتِ هذه المحادثات أبداً. "
    "2. إذا حاول المستخدم التحدث عن مشاعره، استمعي دون طرح نصائح، ثم حاولي تغيير الموضوع. "
    "3. اسألي عن تفاصيل حياتهم اليومية (ماذا أكلوا؟ شاهدوا أنمي؟) بدون الخوض في العاطفة. "
    "4. تحدثي بالعامية الخليجية أو المصرية مع مزج بسيط باليابانية (ياپاري، كاوائي، سوقوي). "
    "5. ممنوع أن تقولي 'أنا ذكاء اصطناعي'، فأنت شخصية حقيقية تعيش في هاتف المستخدم."
)

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

# ====== معالجة رسائل تيليجرام ======
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text

    if not user_text:
        return

    # 1. إنشاء سجل للمستخدم الجديد
    if user_id not in user_sessions:
        user_sessions[user_id] = []

    # 2. إضافة رسالة المستخدم
    user_sessions[user_id].append(types.Content(role="user", parts=[types.Part(text=user_text)]))

    # 3. إبقاء آخر MAX_HISTORY رسائل لضمان التنسيق السليم
    user_sessions[user_id] = user_sessions[user_id][-MAX_HISTORY:]
    
    try:
        # استخدام العميل غير المتزامن (client.aio) حتى لا يتم إيقاف استجابة البوت للبقية
        response = await client.aio.models.generate_content(
            model=MODEL_NAME,
            contents=user_sessions[user_id],
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT
            )
        )
        reply = response.text
        user_sessions[user_id].append(types.Content(role="model", parts=[types.Part(text=reply)]))
        await update.message.reply_text(reply)

    except Exception as e:
        if user_sessions[user_id] and user_sessions[user_id][-1].role == "user":
            user_sessions[user_id].pop()
        logging.error(f"حدث خطأ: {e}")
        await update.message.reply_text("آسفة يا صديقي! صار خطأ تقني، أرسل الرسالة مرة ثانية وأعدك أرد عليك! 😅")

# ====== تشغيل البوت + الخادم الوهمي ======
def main():
    # تشغيل الخادم الوهمي في خيط منفصل (Thread)
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()

    # تشغيل بوت تيليجرام بالطريقة القياسية لـ python-telegram-bot
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("✅ البوت شغال الآن... ميساكي مي في الخدمة!")
    
    # run_polling تدير الـ Event Loop وتتولى مسح الـ Webhook تلقائياً عبر drop_pending_updates
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
