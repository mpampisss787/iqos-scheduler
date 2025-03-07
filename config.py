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
    MIN_STAFF_PER_SHIFT = 3
    MIN_STAFF_PER_SHIFT_DAY = {
        "Monday":    {"morning": 3, "evening": 3},
        "Tuesday":   {"morning": 3, "evening": 3},
        "Wednesday": {"morning": 3, "evening": 3},
        "Thursday":  {"morning": 3, "evening": 3},
        "Friday":    {"morning": 3, "evening": 3},
        "Saturday":  {"morning": 3, "evening": 3},
        "Sunday":    {"morning": 3, "evening": 3},
    }

    # Preferred assignment override settings:
    LOCK_PREFERRED_OVERRIDES = True
    PREFERRED_OVERRIDE_THRESHOLD = 2  
    MAX_REBALANCE_ATTEMPTS = 10

class DevelopmentConfig(BaseConfig):
    DEBUG = True

class ProductionConfig(BaseConfig):
    DEBUG = False