from src.models import Notification
from telepot import Bot  # type: ignore
 
 
class TelegramNotifier:
    """Class that sends notifications to a Telegram user."""
 
    def __init__(self, bot: Bot):
        self.bot = bot
 
    def send_notification(
        self, telegramId: str, notification: Notification, parse_mode: str | None = None
    ) -> None:
        # parse_mode=None : texte brut, evite les erreurs Telegram liees au
        # parsing Markdown quand le titre du logement contient des
        # caracteres speciaux.
        self.bot.sendMessage(telegramId, notification.message, parse_mode=parse_mode)
