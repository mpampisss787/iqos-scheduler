import os

class BaseConfig:
    SECRET_KEY = os.environ.get("SECRET_KEY", "your-secret-key")
    
    # Use PostgreSQL in production, fallback to SQLite for local development
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///store_scheduler.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Ensure PostgreSQL works properly on Render
    if SQLALCHEMY_DATABASE_URI.startswith("postgres://"):
        SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace("postgres://", "postgresql://")

    # Scheduling Defaults
    WEEK_WORKING_DAYS = 6  # 6 = store closed on Sunday, 7 = all week
    MIN_STAFF_PER_SHIFT = 0
    MIN_STAFF_PER_SHIFT_DAY = {
        "Monday":    {"morning": 0, "evening": 0},
        "Tuesday":   {"morning": 0, "evening": 0},
        "Wednesday": {"morning": 0, "evening": 0},
        "Thursday":  {"morning": 0, "evening": 0},
        "Friday":    {"morning": 0, "evening": 0},
        "Saturday":  {"morning": 0, "evening": 0},
        "Sunday":    {"morning": 0, "evening": 0},
    }

    # Preferred assignment override settings:
    LOCK_PREFERRED_OVERRIDES = True
    PREFERRED_OVERRIDE_THRESHOLD = 2  
    MAX_REBALANCE_ATTEMPTS = 10

class DevelopmentConfig(BaseConfig):
    DEBUG = True

class ProductionConfig(BaseConfig):
    DEBUG = False