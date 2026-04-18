from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    api_base_url: str = "http://localhost:8000"
    gcs_bucket_name: str | None = None
    gcs_project_id: str | None = None
    cors_origins: str = "http://localhost:3000,http://localhost:3001"
    gcs_signed_url_minutes: int = 60
    gcs_make_public: bool = False
    max_upload_bytes: int = 100 * 1024 * 1024
    max_request_body_bytes: int = 150 * 1024 * 1024
    token_encryption_key: str | None = None
    oauth_redirect_base_url: str = "http://localhost:8000"
    oauth_success_redirect_url: str = "http://localhost:3000/settings/integrations?status=connected"
    oauth_error_redirect_url: str = "http://localhost:3000/settings/integrations?status=error"
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_scopes: str = "openid email profile https://www.googleapis.com/auth/youtube.upload"
    twitter_client_id: str | None = None
    twitter_client_secret: str | None = None
    twitter_scopes: str = "tweet.read tweet.write users.read offline.access media.write"
    openai_api_key: str | None = None
    ai_copywriter_model: str = "gpt-4.1-mini"
    ai_reviewer_model: str = "gpt-4.1-mini"
    image_output_format: str = "PNG"
    scheduler_poll_seconds: int = 30
    meta_access_token: str | None = None
    meta_page_id: str | None = None
    meta_instagram_user_id: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()


def cors_origin_list() -> list[str]:
    return [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
