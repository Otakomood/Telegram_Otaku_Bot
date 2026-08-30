import os
import google.generativeai as genai
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# ====== ضع مفاتيحك هنا (أو استخدم المتغيرات البيئية) ======
TELEGRAM_TOKEN = "8206519602:AAGK-kU8TLUhF_N3EPDDpaR6PUvpbX-SSTA"
GEMINI_API_KEY = "AQ.Ab8RN6LVVIu6v6Uc1ApJ4vdwbY3yd3sHypej_96MN7CPHTjwBw"

# ====== تهيئة جيميناي ======
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

# قاموس لحفظ ذاكرة كل مستخدم
user_sessions = {}

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text

    # 1. بناء شخصية "الصديقة الأوتاكو" للمستخدم الجديد (مع التعليمات الصارمة)
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
            {"role": "user", "parts": [system_prompt]},
            {"role": "model", "parts": ["أهلاً أهلاً! شخبارك؟ تعال احكي لي وش صار معك اليوم؟ أنا مشتاقة أسمع كل التفاصيل! (｡•̀ᴗ-)✧"]}
        ]

    # 2. إضافة رسالة المستخدم الحالية للتاريخ
    user_sessions[user_id].append({"role": "user", "parts": [user_text]})

    # 3. قص التاريخ لآخر ١٢ رسالة لتوفير السياق مع الحفاظ على الشخصية
    history = user_sessions[user_id][-12:]
    
    # 4. بدء المحادثة مع التاريخ وإرسال طلب الرد
    chat = model.start_chat(history=history)
    response = chat.send_message(user_text)
    reply = response.text

    # 5. حفظ رد البوت في الذاكرة
    user_sessions[user_id].append({"role": "model", "parts": [reply]})

    # 6. إرسال الرد للمستخدم
    await update.message.reply_text(reply)

# ====== تشغيل البوت ======
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("البوت شغال الآن... صديقتك الأوتاكو في انتظار رسائلك!")
    app.run_polling()

if __name__ == "__main__":
    main()