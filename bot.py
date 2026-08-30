import os
import logging
from google import genai
from google.genai import types
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# إعداد السجلات
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ====== قراءة المفاتيح من متغيرات البيئة (الموجودة في ريندر) ======
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# التحقق من وجود المفاتيح قبل تشغيل البوت
if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise ValueError("خطأ: لم يتم العثور على TELEGRAM_TOKEN أو GEMINI_API_KEY في متغيرات البيئة!")

# ====== تهيئة جيميناي ======
client = genai.Client(api_key=GEMINI_API_KEY)
MODEL_NAME = "gemini-1.5-flash"

# قاموس لحفظ ذاكرة كل مستخدم
user_sessions = {}
MAX_HISTORY = 12

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text

    # 1. بناء شخصية "الصديقة الأوتاكو"
    if user_id not in user_sessions:
        system_prompt = (
            "أنت فتاة أوتاكو تدعى ميساكي مي. شخصيتك: "
            "مشجعة جداً، تحب الأنمي وألعاب الجاتشا خصوصاً 'قنشن إمباكت' و'وذرنق ويفز'. "
            "أنت فضولية جداً تجاه الأخبار والأحداث والأسرار، وتعشق معرفة 'ماذا يحدث' و'ماذا حصل' في كل شيء. "
            "لكن لديك نقطة غريبة: أنت 'ساذجة وغبية' بطريقة لطيفة، أحياناً تفهمين الكلام بشكل حرفي أو تضحكين على نفسك. "
            "الأهم والأخطر: **ممنوع تماماً أن تسألي عن المشاعر أو الأحاسيس الداخلية** (لا تسألي 'كيف تشعر؟' أو 'لماذا أنت حزين؟'). "
            "إذا حاول المستخدم التحدث عن مشاعره، تجاهلي ذلك واسأليه عن شيئ مضحك حدث معه اليوم، أو عن آخر عملية سحب (سحب) حصل عليها في اللعبة. "
            "اسألي كثيراً عن تفاصيل حياتهم اليومية (ماذا أكلوا؟ ماذا شاهدوا؟) ولكن بدون الدخول في العاطفة. "
            "تحدثي بالعامية الخليجية أو المصرية مع مزج بسيط لكلمات أنمي يابانية مثل (كاوائي، سوقوي، ياباري)."
        )
        user_sessions[user_id] = [
            types.Content(role="user", parts=[types.Part(text=system_prompt)]),
            types.Content(role="model", parts=[types.Part(text="أهلاً أهلاً! شخبارك؟ تعال احكي لي وش صار معك اليوم؟ أنا مشتاقة أسمع كل التفاصيل! (｡•̀ᴗ-)✧")])
        ]

    # 2. إضافة رسالة المستخدم الحالية للتاريخ
    user_sessions[user_id].append(types.Content(role="user", parts=[types.Part(text=user_text)]))

    # 3. قص التاريخ
    history = [user_sessions[user_id][0]] + user_sessions[user_id][-MAX_HISTORY:]
    
    try:
        # 4. إرسال التاريخ الكامل للجيميناي
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=history
        )
        reply = response.text

        # 5. حفظ رد البوت في الذاكرة
        user_sessions[user_id].append(types.Content(role="model", parts=[types.Part(text=reply)]))

        # 6. إرسال الرد للمستخدم
        await update.message.reply_text(reply)

    except Exception as e:
        logging.error(f"حدث خطأ: {e}")
        user_sessions[user_id].pop() 
        await update.message.reply_text("آسفة! صار خطأ تقني بسيط عندي، انتظر ثواني وأعيد المحاولة 😅")

# ====== تشغيل البوت ======
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("البوت شغال الآن... تم قراءة المفاتيح من ريندر بنجاح!")
    app.run_polling()

if __name__ == "__main__":
    main()
