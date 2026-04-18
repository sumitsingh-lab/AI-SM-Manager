from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, status

from app.config import settings


class TokenCrypto:
    def __init__(self, key: str | None) -> None:
        self._key = key
        self._fernet: Fernet | None = None

    @property
    def fernet(self) -> Fernet:
        if self._fernet is None:
            if not self._key:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="TOKEN_ENCRYPTION_KEY is not configured.",
                )
            try:
                self._fernet = Fernet(self._key.encode("utf-8"))
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="TOKEN_ENCRYPTION_KEY must be a valid Fernet key.",
                ) from exc
        return self._fernet

    def encrypt(self, value: str | None) -> str | None:
        if value is None:
            return None
        return self.fernet.encrypt(value.encode("utf-8")).decode("utf-8")

    def decrypt(self, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return self.fernet.decrypt(value.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Could not decrypt stored token") from exc


token_crypto = TokenCrypto(settings.token_encryption_key)
