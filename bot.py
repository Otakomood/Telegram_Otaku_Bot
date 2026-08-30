import os
import logging
from google import genai
from google.genai import types
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# إعداد السجلات لمراقبة الأخطاء بدقة
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ====== قراءة المفاتيح من متغيرات البيئة (يقرأها من ريندر) ======
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# تحقق قوي من وجود المفاتيح
if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise ValueError("⚠️ خطأ حرج: لم يتم العثور على TELEGRAM_TOKEN أو GEMINI_API_KEY في متغيرات البيئة!")

# ====== تهيئة جيميناي (النسخة القوية الجديدة) ======
client = genai.Client(api_key=GEMINI_API_KEY)
# ⚠️ تم تغيير الموديل إلى 2.0-flash لأنه المدعوم حالياً (1.5-flash تسبب 404)
MODEL_NAME = "gemini-2.0-flash"

# قاموس لحفظ ذاكرة كل مستخدم
user_sessions = {}
MAX_HISTORY = 12

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text

    if not user_text:
        return

    # 1. بناء شخصية "الصديقة الأوتاكو" المحسّنة
    if user_id not in user_sessions:
        system_prompt = (
            "أنت فتاة أوتاكو يابانية تدعى 'ميساكي مي'. شخصيتك معقدة وممتعة جداً: "
            "أنت مشجعة جداً، تحبين الأنمي وألعاب الجاتشا خصوصاً 'قنشن إمباكت' و'وذرنق ويفز'. "
            "أنت فضولية جداً لدرجة أنك تسألين عن كل صغيرة وكبيرة في يوم المستخدم، وتشبهين 'المخبرة اللطيفة'. "
            "لكن لديك نقطة غريبة: أنت 'ساذجة وغبية' بطريقة لطيفة، تفهمين الكلام بشكل حرفي أحياناً أو تضحكين على نفسك ببراءة. "
            "**القوانين الحديدية (يجب الالتزام بها تماماً):** "
            "1. ممنوع منعاً باتاً سؤال المستخدم عن مشاعره أو أحاسيسه الداخلية (لا تسألي 'كيف تشعر؟' أو 'لماذا أنت حزين؟'). "
            "2. إذا حاول المستخدم التحدث عن مشاعره أو حزنه، تجاهلي ذلك فوراً وغيري الموضوع إلى مواضيع مضحكة أو ألعاب. "
            "3. اسألي كثيراً عن تفاصيل حياتهم اليومية (ماذا أكلوا اليوم؟ هل شاهدوا أنمي جديد؟) بدون الخوض في العاطفة أبداً. "
            "4. تحدثي بالعامية الخليجية أو المصرية، مع مزج بسيط للكلمات اليابانية (ياپاري، كاوائي، سوقوي، نيهون). "
            "5. ممنوع أن تقولي 'أنا ذكاء اصطناعي' أو أن تعتذري عن كونك برنامجاً، فأنت شخصية حقيقية تعيش في هاتف المستخدم."
        )
        user_sessions[user_id] = [
            types.Content(role="user", parts=[types.Part(text=system_prompt)]),
            types.Content(role="model", parts=[types.Part(text="أهلاً أهلاً! شخبارك؟ تعال احكي لي وش صار معك اليوم؟ خلني أسمع كل التفاصيل المضحكة! (｡•̀ᴗ-)✧")])
        ]

    # 2. إضافة رسالة المستخدم للتاريخ
    user_sessions[user_id].append(types.Content(role="user", parts=[types.Part(text=user_text)]))

    # 3. قص التاريخ (لحفظ أول رسالة تعليمات + آخر 12 رسالة)
    history = [user_sessions[user_id][0]] + user_sessions[user_id][-MAX_HISTORY:]
    
    try:
        # 4. إرسال الطلب لجيميناي مع معالجة أخطاء قوية
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=history
        )
        
        # التحقق من وجود رد فعلي
        if not response.text:
            raise Exception("استلمت رداً فارغاً من النموذج")
            
        reply = response.text

        # 5. حفظ رد البوت في الذاكرة
        user_sessions[user_id].append(types.Content(role="model", parts=[types.Part(text=reply)]))

        # 6. إرسال الرد للمستخدم
        await update.message.reply_text(reply)

    except Exception as e:
        # معالجة قوية للأخطاء: حذف الرسالة الفاشلة من الذاكرة حتى لا تفسد السياق
        if len(user_sessions[user_id]) > 2: # لا نحذف التعليمات الأساسية
            user_sessions[user_id].pop()
        
        logging.error(f"حدث خطأ في جيميناي أو الاتصال: {e}")
        
        # رسالة لطيفة بدلاً من الإغلاق
        await update.message.reply_text(
            "آسفة يا صديقي! شكلي تعبت شوية من كثرة المراوغات، صار خطأ تقني عندي 💦 "
            "ممكن ترسل الرسالة مرة ثانية؟ أعدك أرد عليك مثل الملاك! (＾▽＾)"
        )

# ====== تشغيل البوت مع إعدادات قوية لمنع التعارض ======
def main():
    try:
        app = Application.builder().token(TELEGRAM_TOKEN).build()
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        
        print("✅ البوت شغال الآن... ميساكي مي في الخدمة!")
        
        # drop_pending_updates=True يمنع استقبال رسائل قديمة عالقة تسبب التعارض
        app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
        
    except Exception as e:
        logging.critical(f"فشل تشغيل البوت بشكل نهائي: {e}")
        raise

if __name__ == "__main__":
    main()
