import os
from dotenv import load_dotenv
load_dotenv()
TELEGRAM_BOT_TOKEN=os.getenv("TELEGRAM_BOT_TOKEN","")
BINANCE_BASE_URL=os.getenv("BINANCE_BASE_URL","https://fapi.binance.com")
DATABASE_PATH=os.getenv("DATABASE_PATH","data/signals.db")
MIN_SIGNAL_SCORE=float(os.getenv("MIN_SIGNAL_SCORE","75"))
MAX_SYMBOLS_TO_SCAN=int(os.getenv("MAX_SYMBOLS_TO_SCAN","0"))  # 0 = every liquid USDT perpetual
MIN_24H_QUOTE_VOLUME=float(os.getenv("MIN_24H_QUOTE_VOLUME","15000000"))
SCAN_CONCURRENCY=int(os.getenv("SCAN_CONCURRENCY","8"))
TOP_COINS=tuple(x.strip().upper() for x in os.getenv(
    "TOP_COINS","BTC,ETH,BNB,SOL,XRP,DOGE"
).split(",") if x.strip())
