import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import List, Set

import telepot

from src.parser import Parser
from src.models import UserConf
from src.notification_builder import NotificationBuilder
from src.settings import Settings
from src.telegram_notifier import TelegramNotifier

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    datefmt="%m/%d/%Y %I:%M:%S %p",
    level=logging.INFO,
)
logger = logging.getLogger("accommodation_notifier")

STATE_FILE = Path("state.json")


def load_seen_ids() -> Set[int]:
    """Charge les IDs de logements deja vus lors des executions precedentes."""
    if not STATE_FILE.exists():
        return set()
    try:
        return set(json.loads(STATE_FILE.read_text()))
    except Exception:
        logger.warning("Impossible de lire state.json, on repart de zero.")
        return set()


def save_seen_ids(ids: Set[int]) -> None:
    """Sauvegarde les IDs vus pour la prochaine execution."""
    STATE_FILE.write_text(json.dumps(sorted(ids)))


def load_users_conf() -> List[UserConf]:
    # L'URL de recherche vient d'une variable d'environnement (secret GitHub
    # SEARCH_URL) au lieu d'etre codee en dur, pour pouvoir changer de ville
    # sans toucher au code.
    search_url = os.environ.get(
        "SEARCH_URL",
        # Valeur par defaut : recherche Avignon (verifiee fonctionnelle)
        "https://trouverunlogement.lescrous.fr/tools/47/search?bounds=4.7396309_43.9967419_4.9271468_43.8866492&locationName=Avignon+%2884000%29",
    )
    return [
        UserConf(
            conf_title="Avignon",
            telegram_id=settings.MY_TELEGRAM_ID,
            search_url=search_url,  # type:ignore
            ignored_ids=[],
        )
    ]


def get_accommodations_with_retry(parser: Parser, search_url, max_attempts: int = 3):
    """Reessaie en cas d'erreur reseau ponctuelle (site lent, indisponible)."""
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            return parser.get_accommodations(search_url)  # type: ignore
        except Exception as e:
            last_error = e
            logger.warning(
                f"Tentative {attempt}/{max_attempts} echouee ({e.__class__.__name__}), "
                f"nouvel essai dans 15s..."
            )
            time.sleep(15)
    raise last_error


if __name__ == "__main__":
    parser_args = argparse.ArgumentParser(
        description="Verifie les logements CROUS disponibles."
    )
    args = parser_args.parse_args()

    settings = Settings()
    bot = telepot.Bot(token=settings.TELEGRAM_BOT_TOKEN)
    bot.getMe()  # test if the bot is working

    user_confs = load_users_conf()
    seen_ids = load_seen_ids()
    logger.info(f"{len(seen_ids)} logement(s) deja vus lors des executions precedentes")

    # Authentification MSE et navigateur Selenium retires : le site rend le
    # contenu cote serveur, une simple requete HTTP suffit (plus rapide et
    # plus fiable qu'un navigateur headless). Reserve : sans authentification,
    # la liste peut etre incomplete selon l'eligibilite DSE du profil.
    parser = Parser()
    notification_builder = NotificationBuilder()
    notifier = TelegramNotifier(bot)

    all_current_ids: Set[int] = set()

    for conf in user_confs:
        logging.info(f"Handling configuration : {conf}")
        search_results = get_accommodations_with_retry(parser, conf.search_url, max_attempts=3)

        current_ids = {a.id for a in search_results.accommodations if a.id is not None}
        all_current_ids |= current_ids

        # Ne garder que les logements reellement nouveaux depuis la derniere
        # execution (sinon on recoit les memes logements a chaque cycle).
        new_accommodations = [
            a for a in search_results.accommodations if a.id not in seen_ids
        ]
        search_results.accommodations = new_accommodations

        if new_accommodations:
            notification = notification_builder.search_results_notification(
                search_results
            )
            if notification:
                notifier.send_notification(conf.telegram_id, notification)
                logger.info(f"{len(new_accommodations)} nouveau(x) logement(s) notifie(s)")
        else:
            logger.info("Aucun nouveau logement depuis la derniere verification")

    save_seen_ids(all_current_ids)
