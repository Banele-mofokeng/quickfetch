from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql://quickfetch:quickfetch@localhost:5432/quickfetch"

    # Redis (conversation state, caching)
    REDIS_URL: str = "redis://localhost:6379"

    # Evolution API (WhatsApp)
    EVOLUTION_API_URL: str = "http://localhost:8080"
    EVOLUTION_API_KEY: str = "change-me"
    EVOLUTION_INSTANCE: str = "quickfetch"

    # Paystack
    PAYSTACK_SECRET_KEY: str = "sk_test_change_me"
    PAYSTACK_PUBLIC_KEY: str = "pk_test_change_me"

    # Google Maps
    GOOGLE_MAPS_API_KEY: str = "change-me"

    # App
    SECRET_KEY: str = "change-me-in-production"
    FRONTEND_URL: str = "http://localhost:3000"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True

    # --- Business rules (operator-configurable) ---
    # Flat delivery fee charged on every order, in Rands.
    DELIVERY_FEE: float = 25.0
    # Cash-on-delivery is only offered when the order budget is at or below this
    # amount. Anything above must be prepaid, so the operator never fronts more
    # than this in unsecured cash. Set per the operator's risk appetite.
    CASH_THRESHOLD: float = 200.0

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
