import os

CLOB_BASE_URL = "https://clob.polymarket.com"
GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
DATA_BASE_URL = "https://data-api.polymarket.com"

TOP_TRADER_COUNT = 10
MIN_TRADER_VOLUME = 0  # volume > $0

MAX_POSITION_PCT = 0.10        # 10% of capital
TRAILING_STOP_PCT = 0.10       # 10% below peak
TAKE_PROFIT_PCT = 0.10         # sell immediately when price gains 10% above entry
KELLY_MIN_EDGE = 0.04          # assumed edge when live price = trader avg price (Gamma API gap)
KALSHI_AGREEMENT_THRESHOLD = 0.05
MAX_SLIPPAGE_PCT = 0.03

MIN_MARKET_VOLUME = 10_000
MIN_MARKET_LIQUIDITY = 1_000
MIN_HOURS_TO_CLOSE = 24
MAX_DAYS_TO_CLOSE = 90

KALSHI_CACHE_TTL = 600         # 10 minutes
KALSHI_ENABLED = False         # set True + add KALSHI_API_KEY once you have credentials
KALSHI_API_KEY = os.getenv("KALSHI_API_KEY", "")
KALSHI_MATCH_THRESHOLD = 0.40  # minimum title similarity to consider a match
KALSHI_AGREE_MULTIPLIER = 1.5
KALSHI_DISAGREE_MULTIPLIER = 0.0
KALSHI_NO_MATCH_MULTIPLIER = 1.0
MARKET_CACHE_TTL = 300         # 5 minutes

REQUEST_TIMEOUT = 30

# Execution / portfolio
DRY_RUN = True                 # paper trading — no real orders are ever placed
INITIAL_CAPITAL = 1000.0    # starting paper capital in USDC
PORTFOLIO_PATH = os.getenv("PORTFOLIO_PATH", "portfolio.json")

# Intelligence layer (JARVIS)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = "claude-sonnet-4-6"
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "")
OBSIDIAN_VAULT_PATH = os.getenv("OBSIDIAN_VAULT_PATH", r"C:\Users\aadha\OneDrive\Trading Journal")
NEWS_MAX_ARTICLES = 5
INTELLIGENCE_WORKERS = 4  # parallel Claude calls

# JARVIS watchlist — per-period market selection
# Only consider markets resolving inside this near-term window (hours).
WATCHLIST_MIN_RESOLUTION_HOURS = 24        # skip markets resolving sooner than this
WATCHLIST_MAX_RESOLUTION_HOURS = 90 * 24   # skip markets resolving later than ~3 months
WATCHLIST_MIN_SCORE = 30.0                 # bot trades entries scoring at/above this

# Score = base(confidence) + conviction bonus + Kalshi-agree bonus, clamped to 0–100.
# confidence == "skip" or Kalshi "disagree" force the score to 0 (excluded).
WATCHLIST_CONFIDENCE_BASE = {"skip": 0.0, "low": 25.0, "medium": 60.0, "high": 85.0}
WATCHLIST_CONVICTION_BASELINE = 2          # trader count at which the bonus starts
WATCHLIST_CONVICTION_BONUS_PER_TRADER = 3.0
WATCHLIST_CONVICTION_BONUS_CAP = 9.0
WATCHLIST_KALSHI_AGREE_BONUS = 6.0
