import os

class Settings:
    PROJECT_NAME = "KwanzaControl CFO AI"
    VERSION = "1.0.0"

    DATABASE_URL = os.getenv(
        "DATABASE_URL",
        "sqlite:///./cfo.db"
    )

settings = Settings()