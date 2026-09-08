import os

# Ensure DB dir exists and force sqlite file for tests
os.makedirs("data", exist_ok=True)

# Import after env setup
from app.db import init_db, Base, engine

# Create tables before any test
init_db()

# Clean tables before each test session
from sqlalchemy.orm import Session

def _clean():
    from app.models import ReviewSnapshot, Lead, Search, ApiUsage, PlaceCache, LeadScore, WebhookDelivery

    with Session(engine) as s:
        s.query(WebhookDelivery).delete()
        s.query(LeadScore).delete()
        s.query(ApiUsage).delete()
        s.query(PlaceCache).delete()
        s.query(ReviewSnapshot).delete()
        s.query(Lead).delete()
        s.query(Search).delete()
        s.commit()

_clean()
