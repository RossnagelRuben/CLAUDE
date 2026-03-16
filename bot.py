from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

TOKEN = "8658712463:AAETkSCQrZMls24l2iX5rMe1djJkrg_rkJY"

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = await update.message.voice.get_file()
    await file.download_to_drive("audio.ogg")
    await update.message.reply_text("Audio recibido!")

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(MessageHandler(filters.VOICE, handle_voice))
app.run_polling()
