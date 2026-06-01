import os

from fastapi import Security, HTTPException, status

from fastapi.security.api_key import APIKeyHeader


from dotenv import load_dotenv

load_dotenv()

# Expected API key from environment, with a default for development only
SMS_GATEWAY_API_KEY = os.getenv("SMS_GATEWAY_API_KEY", "dev-secret-sms-key-360")
API_KEY_NAME = "X-API-Key"

api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

def verify_sms_api_key(api_key_header: str = Security(api_key_header)):
    if api_key_header == SMS_GATEWAY_API_KEY:
        return api_key_header
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Could not validate API KEY",
    )
