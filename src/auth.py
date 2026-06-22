import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

class TossAuthManager:
    """
    토스증권 Open API OAuth 토큰 관리 모듈
    24시간 유효기간을 고려하여 만료 전 토큰을 자동으로 재발급/유지합니다.
    """
    def __init__(self):
        self.client_id = os.getenv("TOSS_CLIENT_ID")
        self.client_secret = os.getenv("TOSS_CLIENT_SECRET")
        self.run_mode = os.getenv("RUN_MODE", "MOCK").upper()
        
        self._access_token = None
        self._expires_at = 0  # 토큰 만료 절대 시간 (Unix Timestamp)
        
    def get_access_token(self) -> str:
        """
        현재 유효한 액세스 토큰을 반환합니다. 
        만료되었거나 만료가 임박한 경우(10분 미만) 자동으로 갱신을 진행합니다.
        """
        if self.run_mode == "MOCK":
            return "mock_access_token_1234567890"

        # 토큰이 없거나 만료 10분 전이면 재발급
        if not self._access_token or (self._expires_at - time.time() < 600):
            self._refresh_token()
            
        return self._access_token

    def _refresh_token(self):
        """실제 토스 API 서버에 요청하여 토큰을 발급받습니다."""
        url = "https://openapi.tossinvest.com/oauth2/token"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded"
        }
        data = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret
        }
        
        try:
            print(f"[Auth] OAuth 토큰 발급 요청 중...")
            response = requests.post(url, headers=headers, data=data, timeout=10.0)
            
            # Rate Limit 헤더 확인 (Auth 그룹)
            remaining = response.headers.get("X-RateLimit-Remaining")
            if remaining is not None:
                print(f"[Auth Rate Limit] Remaining: {remaining}")
                
            if response.status_code == 200:
                res_data = response.json()
                self._access_token = res_data["access_token"]
                # 만료 시간 설정 (expires_in 초)
                expires_in = int(res_data.get("expires_in", 86400))
                self._expires_at = time.time() + expires_in
                print(f"[Auth] OAuth 토큰이 성공적으로 발급되었습니다. (만료: {expires_in}초 후)")
            else:
                print(f"[Auth ERROR] 토큰 발급 실패. Status Code: {response.status_code}, Response: {response.text}")
                # 실패 시 예외를 던져 프로그램이 오동작하는 것을 막음
                response.raise_for_status()
                
        except Exception as e:
            print(f"[Auth ERROR] OAuth 토큰 발급 중 예외 발생: {e}")
            raise e

# 전역 싱글톤 인스턴스 제공
auth_manager = TossAuthManager()

if __name__ == "__main__":
    # 간단한 테스트 실행
    print("Run Mode:", auth_manager.run_mode)
    token = auth_manager.get_access_token()
    print("Token:", token)
