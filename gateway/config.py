from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str
    REDIS_URL: str
    UPSTREAM_URL: str

    RATE_LIMIT_FREE: int = 10
    RATE_LIMIT_PRO: int = 60

    class Config:
        env_file = ".env"


settings = Settings()
