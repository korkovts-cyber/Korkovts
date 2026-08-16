import os

from dotenv import load_dotenv

load_dotenv()
TELEGRAM_BOT_TOKEN=os.getenv("TELEGRAM_BOT_TOKEN","")
BINANCE_BASE_URL=os.getenv("BINANCE_BASE_URL","https://fapi.binance.com")
DATABASE_PATH=os.getenv("DATABASE_PATH","data/signals.db")
# Frozen research parameters. Railway environment variables from older
# releases must not silently alter the v10R.4 cohort.
MIN_SIGNAL_SCORE=82.0
MAX_SYMBOLS_TO_SCAN=int(os.getenv("MAX_SYMBOLS_TO_SCAN","0"))  # 0 = every liquid USDT perpetual
MIN_24H_QUOTE_VOLUME=float(os.getenv("MIN_24H_QUOTE_VOLUME","15000000"))
SCAN_CONCURRENCY=int(os.getenv("SCAN_CONCURRENCY","6"))
TOP_COINS=tuple(x.strip().upper() for x in os.getenv(
    "TOP_COINS","BTC,ETH,BNB,SOL,XRP,DOGE"
).split(",") if x.strip())
AUTO_SCAN_INTERVAL_MIN=int(os.getenv("AUTO_SCAN_INTERVAL_MIN","10"))
SIGNAL_COOLDOWN_HOURS=24
DEEP_ANALYSIS_LIMIT=int(os.getenv("DEEP_ANALYSIS_LIMIT","0"))  # 0 = deep-check every candidate
SIGNAL_MAX_AGE_HOURS=48
APP_VERSION="10R.4"
# Fixed per release so forward-test cohorts cannot be mixed accidentally by an
# old Railway environment variable.
STRATEGY_VERSION="6.0.4-research"
DEFAULT_RISK_PCT=float(os.getenv("DEFAULT_RISK_PCT","0.5"))
MAX_RISK_PCT=float(os.getenv("MAX_RISK_PCT","1.0"))
DAILY_STOP_R=float(os.getenv("DAILY_STOP_R","-2.0"))
MAX_OPEN_SIGNALS=int(os.getenv("MAX_OPEN_SIGNALS","2"))
ROUND_TRIP_COST_PCT=0.12
ENTRY_EXPIRY_HOURS=8
MAX_PORTFOLIO_RISK_PCT=float(os.getenv("MAX_PORTFOLIO_RISK_PCT","1.5"))

# Research release safeguards.  These are deliberately fixed for the cohort:
# changing them mid-test would make the collected outcomes incomparable.
CORRELATION_CLUSTER_THRESHOLD=0.82
BREADTH_EXTREME_LOW=0.25
BREADTH_EXTREME_HIGH=0.75
ADL_MAX_AGE_MINUTES=90
LIQUIDATION_WINDOW_MINUTES=15
NEUTRAL_REGIME_SCORE_PENALTY=8
NEUTRAL_REGIME_MAX_SIGNALS=1
NEWS_CACHE_SECONDS=60
NEWS_POLL_INTERVAL_SEC=120
NEWS_ALERT_MAX_AGE_MIN=45
X_BEARER_TOKEN=os.getenv("X_BEARER_TOKEN","").strip()
X_NEWS_QUERY=os.getenv(
    "X_NEWS_QUERY",
    "(from:realDonaldTrump OR from:POTUS OR from:elonmusk OR from:cz_binance "
    "OR from:saylor OR from:binance OR from:coinbase) "
    "(bitcoin OR crypto OR stablecoin OR tariff OR sanction OR rates OR dollar "
    "OR reserve OR bank OR economy OR market OR SEC OR ETF) -is:retweet",
).strip()
