import argparse
import json
import logging
import os
from pathlib import Path
from typing import List, Set

import telepot
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.webdriver import WebDriver

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


def create_driver(headless: bool = True) -> WebDriver:
    chrome_options = Options()
    if headless:
        logging.info("Running in headless mode")
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--disable-gpu")
    else:
        logging.info("Running in non-headless mode")

    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--no-sandbox")

    # Selenium Manager (integre a Selenium >=4.6) detecte et telecharge
    # automatiquement la version de ChromeDriver compatible avec le Chrome
    # installe, evitant les incompatibilites de version.
    driver = webdriver.Chrome(options=chrome_options)
    # Timeout de chargement de page reduit (60s au lieu des 300s par defaut)
    # pour echouer plus vite en cas de vrai probleme, combine a des
    # tentatives multiples ci-dessous pour absorber les ralentissements
    # ponctuels du site.
    driver.set_page_load_timeout(60)
    return driver


def get_accommodations_with_retry(search_url, headless: bool, max_attempts: int = 3):
    """Reessaie en recreant entierement le navigateur a chaque tentative.

    Une session Chrome ayant subi un timeout reste souvent corrompue :
    reessayer sur la meme session echoue quasi instantanement. On recree
    donc un driver frais a chaque tentative, et on le ferme proprement
    meme en cas d'echec pour ne pas laisser de processus zombies.
    """
    import time as _time

    last_error = None
    for attempt in range(1, max_attempts + 1):
        driver = create_driver(headless=headless)
        try:
            parser = Parser(driver)
            return parser.get_accommodations(search_url)  # type: ignore
        except Exception as e:
            last_error = e
            logger.warning(
                f"Tentative {attempt}/{max_attempts} echouee ({e.__class__.__name__}), "
                f"nouvel essai dans 15s..."
            )
            _time.sleep(15)
        finally:
            driver.quit()
    raise last_error


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run the script in headless mode or not."
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Run the script without headless mode",
    )

    args = parser.parse_args()

    settings = Settings()
    bot = telepot.Bot(token=settings.TELEGRAM_BOT_TOKEN)
    bot.getMe()  # test if the bot is working

    user_confs = load_users_conf()
    seen_ids = load_seen_ids()
    logger.info(f"{len(seen_ids)} logement(s) deja vus lors des executions precedentes")

    # Authentification MSE retiree : le selecteur CSS du bouton de connexion
    # ne correspond plus a la structure actuelle du site (change depuis
    # l'ecriture du script d'origine). La recherche fonctionne sans
    # authentification, avec une reserve : le site officiel indique que la
    # liste peut etre incomplete selon l'eligibilite DSE du profil connecte.
    notification_builder = NotificationBuilder()
    notifier = TelegramNotifier(bot)

    all_current_ids: Set[int] = set()

    for conf in user_confs:
        logging.info(f"Handling configuration : {conf}")
        search_results = get_accommodations_with_retry(
            conf.search_url, headless=not args.no_headless, max_attempts=3
        )

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
