import os

CLOB_BASE_URL = "https://clob.polymarket.com"
GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
DATA_BASE_URL = "https://data-api.polymarket.com"

TOP_TRADER_COUNT = 10
MIN_TRADER_VOLUME = 0  # volume > $0

MAX_POSITION_PCT = 0.10        # 10% of bankroll
TRAILING_STOP_PCT = 0.10       # 10% below peak
KALSHI_AGREEMENT_THRESHOLD = 0.05
MAX_SLIPPAGE_PCT = 0.03

MIN_MARKET_VOLUME = 10_000
MIN_MARKET_LIQUIDITY = 1_000
MIN_HOURS_TO_CLOSE = 24
MAX_DAYS_TO_CLOSE = 30

KALSHI_CACHE_TTL = 600         # 10 minutes
MARKET_CACHE_TTL = 300         # 5 minutes

REQUEST_TIMEOUT = 10

# Intelligence layer (JARVIS)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")
OBSIDIAN_VAULT_PATH = os.getenv("OBSIDIAN_VAULT_PATH", r"C:\Users\aadha\OneDrive\Trading Journal")
NEWS_MAX_ARTICLES = 5
INTELLIGENCE_WORKERS = 4  # parallel Claude calls
