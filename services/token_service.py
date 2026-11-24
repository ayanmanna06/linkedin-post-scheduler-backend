from cryptography.fernet import Fernet, InvalidToken

from backend.config import settings
from backend.services.exceptions import AppException


class CryptoError(AppException):
    default_code = "CRYPTO_ERROR"
    default_status_code = 500
    default_message = "Cryptographic operation failed."


class TokenService:
    def __init__(self, key: str | None = None):
        self._custom_key = key
        self._cipher: Fernet | None = None

    @property
    def cipher(self) -> Fernet:
        if self._cipher is None:
            raw_key = self._custom_key or settings.TOKEN_ENCRYPTION_KEY
            if not raw_key:
                raise CryptoError("TOKEN_ENCRYPTION_KEY is not configured.")
            try:
                key_bytes = raw_key.encode("utf-8") if isinstance(raw_key, str) else raw_key
                self._cipher = Fernet(key_bytes)
            except Exception as e:
                raise CryptoError(f"Invalid TOKEN_ENCRYPTION_KEY: {str(e)}") from e
        return self._cipher

    def encrypt_token(self, plain_text: str | None) -> str | None:
        if plain_text is None:
            return None
        if not plain_text:
            return ""
        try:
            return self.cipher.encrypt(plain_text.encode("utf-8")).decode("utf-8")
        except Exception as e:
            raise CryptoError(f"Encryption failed: {str(e)}") from e

    def decrypt_token(self, cipher_text: str | None) -> str | None:
        if cipher_text is None:
            return None
        if not cipher_text:
            return ""
        try:
            return self.cipher.decrypt(cipher_text.encode("utf-8")).decode("utf-8")
        except InvalidToken as e:
            raise CryptoError("Decryption failed: Token is invalid or corrupted.") from e
        except Exception as e:
            raise CryptoError(f"Decryption error: {str(e)}") from e

    # Aliases for backward compatibility and convenience
    encrypt = encrypt_token
    decrypt = decrypt_token


token_service = TokenService()
TokenEncryptor = TokenService
token_encryptor = token_service
