import os
import logging
import threading
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
MODEL_NAME = "gemini-2.0-flash"  # النسخة الأحدث المدعومة مجاناً

# ====== الذاكرة ======
user_sessions = {}
MAX_HISTORY = 12

# ====== خادم ويب وهمي لإرضاء ريندر (لمنع الإيقاف) ======
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")
    def log_message(self, format, *args):
        # إخفاء سجلات الخادم الوهمي حتى لا تزاحم سجلات البوت
        pass

def run_web_server():
    # ريندر يوفر المنفذ تلقائياً في متغير PORT
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

    # 1. بناء شخصية الصديقة الأوتاكو
    if user_id not in user_sessions:
        system_prompt = (
            "أنت فتاة أوتاكو يابانية تدعى 'ميساكي مي'. شخصيتك معقدة وممتعة جداً: "
            "مشجعة جداً، تحبين الأنمي وألعاب الجاتشا خصوصاً 'قنشن إمباكت' و'وذرنق ويفز'. "
            "فضولية جداً لدرجة أنك تسألين عن كل صغيرة وكبيرة في يوم المستخدم. "
            "لكن لديك نقطة غريبة: أنت 'ساذجة وغبية' بطريقة لطيفة، تفهمين الكلام بشكل حرفي أحياناً. "
            "**القوانين الحديدية:** "
            "1. ممنوع منعاً باتاً سؤال المستخدم عن مشاعره أو أحاسيسه الداخلية. "
            "2. إذا حاول المستخدم التحدث عن مشاعره، تجاهلي ذلك وغيري الموضوع لمواضيع مضحكة أو ألعاب. "
            "3. اسألي عن تفاصيل حياتهم اليومية (ماذا أكلوا؟ شاهدوا أنمي؟) بدون الخوض في العاطفة. "
            "4. تحدثي بالعامية الخليجية أو المصرية مع مزج بسيط باليابانية (ياپاري، كاوائي، سوقوي). "
            "5. ممنوع أن تقولي 'أنا ذكاء اصطناعي'، فأنت شخصية حقيقية تعيش في هاتف المستخدم."
        )
        user_sessions[user_id] = [
            types.Content(role="user", parts=[types.Part(text=system_prompt)]),
            types.Content(role="model", parts=[types.Part(text="أهلاً أهلاً! شخبارك؟ تعال احكي لي وش صار معك اليوم؟ خلني أسمع كل التفاصيل! (｡•̀ᴗ-)✧")])
        ]

    # 2. إضافة رسالة المستخدم
    user_sessions[user_id].append(types.Content(role="user", parts=[types.Part(text=user_text)]))

    # 3. قص التاريخ
    history = [user_sessions[user_id][0]] + user_sessions[user_id][-MAX_HISTORY:]
    
    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=history
        )
        reply = response.text
        user_sessions[user_id].append(types.Content(role="model", parts=[types.Part(text=reply)]))
        await update.message.reply_text(reply)

    except Exception as e:
        # معالجة الأخطاء
        if len(user_sessions[user_id]) > 2:
            user_sessions[user_id].pop()
        logging.error(f"حدث خطأ: {e}")
        await update.message.reply_text("آسفة يا صديقي! صار خطأ تقني، أرسل الرسالة مرة ثانية وأعدك أرد عليك! 😅")

# ====== تشغيل البوت + الخادم الوهمي معاً ======
def main():
    # تشغيل الخادم الوهمي في خيط منفصل (Thread) حتى لا يوقف البوت
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()

    # تشغيل بوت تيليجرام
    try:
        app = Application.builder().token(TELEGRAM_TOKEN).build()
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        print("✅ البوت شغال الآن... ميساكي مي في الخدمة!")
        app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    except Exception as e:
        logging.critical(f"فشل تشغيل البوت: {e}")
        raise

if __name__ == "__main__":
    main()
