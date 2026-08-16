import os
from dotenv import load_dotenv
load_dotenv()
TELEGRAM_BOT_TOKEN=os.getenv("TELEGRAM_BOT_TOKEN","")
BINANCE_BASE_URL=os.getenv("BINANCE_BASE_URL","https://fapi.binance.com")
DATABASE_PATH=os.getenv("DATABASE_PATH","data/signals.db")
MIN_SIGNAL_SCORE=float(os.getenv("MIN_SIGNAL_SCORE","80"))
MAX_SYMBOLS_TO_SCAN=int(os.getenv("MAX_SYMBOLS_TO_SCAN","0"))  # 0 = every liquid USDT perpetual
MIN_24H_QUOTE_VOLUME=float(os.getenv("MIN_24H_QUOTE_VOLUME","15000000"))
SCAN_CONCURRENCY=int(os.getenv("SCAN_CONCURRENCY","8"))
TOP_COINS=tuple(x.strip().upper() for x in os.getenv(
    "TOP_COINS","BTC,ETH,BNB,SOL,XRP,DOGE"
).split(",") if x.strip())
AUTO_SCAN_INTERVAL_MIN=int(os.getenv("AUTO_SCAN_INTERVAL_MIN","10"))
SIGNAL_COOLDOWN_HOURS=int(os.getenv("SIGNAL_COOLDOWN_HOURS","6"))
DEEP_ANALYSIS_LIMIT=int(os.getenv("DEEP_ANALYSIS_LIMIT","0"))  # 0 = deep-check every candidate
SIGNAL_MAX_AGE_HOURS=int(os.getenv("SIGNAL_MAX_AGE_HOURS","48"))
STRATEGY_VERSION=os.getenv("STRATEGY_VERSION","4.0")
DEFAULT_RISK_PCT=float(os.getenv("DEFAULT_RISK_PCT","0.5"))
MAX_RISK_PCT=float(os.getenv("MAX_RISK_PCT","1.0"))
DAILY_STOP_R=float(os.getenv("DAILY_STOP_R","-2.0"))
MAX_OPEN_SIGNALS=int(os.getenv("MAX_OPEN_SIGNALS","2"))
ROUND_TRIP_COST_PCT=float(os.getenv("ROUND_TRIP_COST_PCT","0.12"))
ENTRY_EXPIRY_HOURS=int(os.getenv("ENTRY_EXPIRY_HOURS","8"))
MAX_PORTFOLIO_RISK_PCT=float(os.getenv("MAX_PORTFOLIO_RISK_PCT","1.5"))
