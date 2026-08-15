import os
from dotenv import load_dotenv
load_dotenv()
TELEGRAM_BOT_TOKEN=os.getenv("TELEGRAM_BOT_TOKEN","")
BINANCE_BASE_URL=os.getenv("BINANCE_BASE_URL","https://fapi.binance.com")
DATABASE_PATH=os.getenv("DATABASE_PATH","data/signals.db")
MIN_SIGNAL_SCORE=float(os.getenv("MIN_SIGNAL_SCORE","75"))
MAX_SYMBOLS_TO_SCAN=int(os.getenv("MAX_SYMBOLS_TO_SCAN","40"))
