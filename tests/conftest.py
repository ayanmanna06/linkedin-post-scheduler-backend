import os
import sys
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Ensure project root and backend dir are on sys.path
backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
project_root = os.path.dirname(backend_dir)
for p in (project_root, backend_dir):
    if p not in sys.path:
        sys.path.insert(0, p)

TEST_FERNET_KEY = Fernet.generate_key().decode()
os.environ["FLASK_ENV"] = "testing"
os.environ["SECRET_KEY"] = "super-secret-test-key-32-chars-long!"
os.environ["TOKEN_ENCRYPTION_KEY"] = TEST_FERNET_KEY
os.environ["DB_NAME"] = "linkedin_scheduler_test"

import backend.db as db_module
from backend.app import create_app
from backend.models import Base, LinkedInConnection, User
from backend.services.auth_service import auth_service
from backend.services.token_service import token_encryptor

# In-memory SQLite for isolated tests
TEST_ENGINE = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=TEST_ENGINE)

# Point backend.db to the test engine and session factory
db_module._engine = TEST_ENGINE
db_module._SessionFactory = TestingSessionLocal


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """Initializes schema in the test database."""
    Base.metadata.create_all(bind=TEST_ENGINE)
    token_encryptor._cipher = Fernet(TEST_FERNET_KEY.encode())
    yield
    Base.metadata.drop_all(bind=TEST_ENGINE)


@pytest.fixture
def db_session():
    """Provides a fresh database session for a test and cleans up afterwards."""
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        with TEST_ENGINE.begin() as conn:
            for table in reversed(Base.metadata.sorted_tables):
                conn.execute(table.delete())


@pytest.fixture
def test_user(db_session):
    """Creates a sample test user with an active encrypted LinkedIn connection."""
    user = User(
        linkedin_sub="test-sub-12345",
        display_name="Ayan Enterprise Tester",
        email="ayan@test.example.com",
    )
    db_session.add(user)
    db_session.commit()

    conn = LinkedInConnection(
        user_id=user.id,
        author_urn="urn:li:person:test-sub-12345",
        access_token_encrypted=token_encryptor.encrypt("mock-oauth-token-val"),
        expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(days=60),
        scopes="openid profile w_member_social",
    )
    db_session.add(conn)
    db_session.commit()
    return user


@pytest.fixture
def app():
    """Creates a Flask test application instance."""
    app_instance = create_app({"TESTING": True})
    return app_instance


@pytest.fixture
def client(app, test_user):
    """Provides Flask test client with an authenticated session cookie."""
    jwt_token = auth_service.create_jwt_token(test_user.id)
    test_client = app.test_client()
    test_client.set_cookie(
        domain="localhost",
        key=auth_service.COOKIE_NAME,
        value=jwt_token,
    )
    return test_client


@pytest.fixture
def unauthenticated_client(app):
    """Provides Flask test client without credentials."""
    return app.test_client()
