import argparse
import json
import logging
import os
from pathlib import Path
from typing import List, Set

import telepot
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromiumService
from selenium.webdriver.chrome.webdriver import WebDriver
from chromedriver_py import binary_path  # this will get you the path variable

from src.authenticator import Authenticator
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
    search_url = os.environ.get(
        "SEARCH_URL",
        "https://trouverunlogement.lescrous.fr/tools/45/search?bounds=4.74754144416955_43.99596600971674_4.87245855583045_43.90603399028326",
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

    return webdriver.Chrome(
        options=chrome_options,
        service=ChromiumService(
            executable_path=binary_path,
        ),
    )


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

    driver = create_driver(headless=not args.no_headless)
    Authenticator(settings.MSE_EMAIL, settings.MSE_PASSWORD).authenticate_driver(driver)

    parser = Parser(driver)
    notification_builder = NotificationBuilder()
    notifier = TelegramNotifier(bot)

    all_current_ids: Set[int] = set()

    for conf in user_confs:
        logging.info(f"Handling configuration : {conf}")
        search_results = parser.get_accommodations(conf.search_url)  # type: ignore

        current_ids = {a.id for a in search_results.accommodations if a.id is not None}
        all_current_ids |= current_ids

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
    driver.quit()
