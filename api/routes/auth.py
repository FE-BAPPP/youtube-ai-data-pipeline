"""
Router cho OAuth2 Authentication.

Xử lý luồng OAuth2 với Google để kết nối kênh YouTube.
Trích xuất từ oauth_server.py gốc.
"""

import os
import json

from fastapi import APIRouter, Request

from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow

from utils.logger import get_logger
from utils.config import config

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Cấu hình OAuth 2.0 chuẩn
CLIENT_SECRETS_FILE = getattr(config, "OAUTH_CLIENT_SECRETS_FILE", "client_secrets.json")
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]

# Tạm thời lưu token vào file (Thực tế có thể lưu vào DB)
TOKEN_FILE = "user_token.json"


def _get_redirect_uri() -> str:
    """Tạo redirect URI dựa trên cấu hình port."""
    port = getattr(config, "APP_PORT", 8080)
    return f"http://localhost:{port}/auth/callback"


@router.get("/login")
def login() -> RedirectResponse:
    """Tạo ra đường link 'Log in with Google'.

    Đọc file client_secrets và chuyển hướng người dùng sang trang Đăng nhập Google.
    access_type='offline' và prompt='consent' RẤT QUAN TRỌNG: Để Google nhả ra Refresh Token.
    """
    redirect_uri = _get_redirect_uri()
    logger.info("Khởi tạo OAuth flow, redirect_uri=%s", redirect_uri)

    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=redirect_uri,
    )
    # access_type='offline' và prompt='consent' RẤT QUAN TRỌNG: Để Google nhả ra Refresh Token
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")

    # Chuyển hướng người dùng thẳng sang trang Đăng nhập Google
    return RedirectResponse(auth_url)


@router.get("/callback")
def oauth2_callback(request: Request) -> dict:
    """Điểm hứng Token khi khách hàng bấm 'Cho phép' (Allow).

    Dùng Code đổi lấy Token, lưu vào file để xài trọn đời.
    """
    code = request.query_params.get("code")
    if not code:
        logger.warning("OAuth callback không nhận được mã xác thực (code)")
        return {"error": "Không nhận được mã xác thực (Code) từ Google"}

    redirect_uri = _get_redirect_uri()

    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        redirect_uri=redirect_uri,
    )

    # Ép kiểu URL thành HTTP (Vì localhost chạy HTTP nhưng Google đôi khi gắt gao vụ HTTPS)
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    flow.fetch_token(authorization_response=str(request.url))
    credentials = flow.credentials

    # Lưu cục Token này vào máy để xài trọn đời
    token_data = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": list(credentials.scopes) if credentials.scopes else [],
    }

    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(token_data, f, indent=4)

    logger.info("OAuth2 token đã được lưu thành công vào %s", TOKEN_FILE)
    return {
        "message": "✅ XONG! KẾT NỐI KÊNH YOUTUBE THÀNH CÔNG RỰC RỠ! "
        "Sếp có thể tắt cái tab trình duyệt này đi được rồi."
    }
