import os

class BaseConfig:
    SECRET_KEY = os.environ.get("SECRET_KEY", "your-secret-key")
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "sqlite:///store_scheduler.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

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
    # LOCK_PREFERRED_OVERRIDES = True means do NOT override preferred off days.
    # Set to False (via the settings tab) to allow overriding in worst-case scenarios.
    LOCK_PREFERRED_OVERRIDES = True
    PREFERRED_OVERRIDE_THRESHOLD = 2  # (Not used in current logic; kept for reference.)
    MAX_REBALANCE_ATTEMPTS = 10

class DevelopmentConfig(BaseConfig):
    DEBUG = True

class ProductionConfig(BaseConfig):
    DEBUG = False
