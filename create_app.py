# create_app.py

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_cors import CORS

from config import DevelopmentConfig
from models import db
# Import your blueprint modules
from employees.routes import employees_bp
from schedule.routes import schedule_bp
from settings.routes import settings_bp

def create_app():
    app = Flask(__name__, template_folder="templates")
    app.config.from_object(DevelopmentConfig)

    db.init_app(app)
    migrate = Migrate(app, db)

    # optional: enable CORS
    CORS(app)

    # Register your blueprints
    app.register_blueprint(employees_bp, url_prefix="/employees")
    app.register_blueprint(schedule_bp, url_prefix="/schedule")
    app.register_blueprint(settings_bp, url_prefix="/settings")

    @app.route("/")
    def root_index():
        return "<h3>Welcome! Go to /employees or /schedule or /settings</h3>"

    return app

app = create_app()

