# models.py
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Column, Integer, String
from sqlalchemy.types import TypeDecorator, TEXT
import json

db = SQLAlchemy()

class SafeJSONList(TypeDecorator):
    impl = TEXT
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return json.dumps([])
        return json.dumps(value)

    def process_result_value(self, value, dialect):
        if not value or value.strip() == "":
            return []
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return []

class SafeJSONDict(TypeDecorator):
    impl = TEXT
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return json.dumps({})
        return json.dumps(value)

    def process_result_value(self, value, dialect):
        if not value or value.strip() == "":
            return {}
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}

class Employee(db.Model):
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    shift_type = Column(String(10), nullable=False)  # "8-hour" or "6-hour"

    preferred_day_off = Column(SafeJSONList, nullable=True, default=[])
    manual_days_off = Column(SafeJSONList, nullable=True, default=[])
    shift_requests = Column(SafeJSONDict, nullable=True, default={})

    def __repr__(self):
        return f"<Employee {self.id} - {self.name}>"
