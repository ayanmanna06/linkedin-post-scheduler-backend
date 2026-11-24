import pytest
from cryptography.fernet import Fernet

from backend.services.token_service import CryptoError, TokenEncryptor


def test_token_encryption_and_decryption_round_trip():
    test_key = Fernet.generate_key().decode()
    encryptor = TokenEncryptor(test_key)

    original_token = "AQV7MockAccessTokenFromLinkedIn-LongSecureString-123456789"
    encrypted = encryptor.encrypt(original_token)

    assert encrypted is not None
    assert encrypted != original_token
    assert len(encrypted) > 20

    decrypted = encryptor.decrypt(encrypted)
    assert decrypted == original_token


def test_empty_and_none_tokens():
    test_key = Fernet.generate_key().decode()
    encryptor = TokenEncryptor(test_key)

    assert encryptor.encrypt(None) is None
    assert encryptor.decrypt(None) is None
    assert encryptor.encrypt("") == ""
    assert encryptor.decrypt("") == ""


def test_invalid_cipher_text_raises_crypto_error():
    test_key = Fernet.generate_key().decode()
    encryptor = TokenEncryptor(test_key)

    corrupted_cipher = "gAAAAABl-not-a-valid-fernet-token-12345"
    with pytest.raises(CryptoError) as exc_info:
        encryptor.decrypt(corrupted_cipher)

    assert "Token is invalid" in str(exc_info.value)
