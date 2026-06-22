import os
import time
import random
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from src.auth import auth_manager

class TossApiClient:
    """
    Rate Limit 방어 및 Mock 데이터 모드를 지원하는 토스증권 API 클라이언트
    """
    def __init__(self):
        self.base_url = "https://openapi.tossinvest.com"
        self.account_seq = os.getenv("TOSS_ACCOUNT_SEQ", "ac_01HXYZABC")
        self.run_mode = os.getenv("RUN_MODE", "MOCK").upper()
        self.fixed_rate = float(os.getenv("FIXED_USD_KRW_RATE", "1380.0"))
        
        # Mock 데이터를 위한 상태 관리 (이전 캔들 및 가격 정보를 저장하여 자연스러운 시세 흐름 유지)
        self._mock_last_prices: Dict[str, float] = {}
        self._mock_candle_history: Dict[str, List[Dict[str, Any]]] = {}

    def _check_rate_limit(self, headers: Dict[str, str]):
        """
        Rate Limit 방어 로직: 
        응답 헤더의 X-RateLimit-Remaining가 1 이하로 떨어지면 X-RateLimit-Reset 시간 동안 강제 휴식
        """
        remaining = headers.get("X-RateLimit-Remaining")
        reset_val = headers.get("X-RateLimit-Reset")
        
        if remaining is not None:
            try:
                remaining_int = int(remaining)
                if remaining_int <= 1 and reset_val is not None:
                    reset_time = float(reset_val)
                    current_time = time.time()
                    
                    # 만약 reset_time이 Unix timestamp 형태라면 차이만큼 대기
                    if reset_time > 1000000000:
                        sleep_sec = max(reset_time - current_time + 0.1, 0.1)
                    else:
                        # 상대적인 대기 시간(초)인 경우
                        sleep_sec = reset_time + 0.1
                        
                    print(f"[Rate Limit 방어] 남은 한도가 부족하여 {sleep_sec:.2f}초 동안 대기합니다...")
                    time.sleep(sleep_sec)
            except ValueError:
                pass

    def _request(self, method: str, path: str, need_account: bool = False, **kwargs) -> Any:
        """공통 HTTP 요청 핸들러 (Bearer 토큰 및 Account 헤더 주입, Rate Limit 체크 및 Envelope 언랩)"""
        url = f"{self.base_url}{path}"
        
        # 1. 헤더 준비
        headers = kwargs.pop("headers", {})
        token = auth_manager.get_access_token()
        headers["Authorization"] = f"Bearer {token}"
        
        if need_account:
            headers["X-Tossinvest-Account"] = self.account_seq
            
        # 2. 요청 실행
        response = requests.request(method, url, headers=headers, **kwargs)
        
        # 3. Rate Limit 체크
        self._check_rate_limit(response.headers)
        
        # 4. 결과 분석 및 BFF Envelope 처리
        if response.status_code == 200:
            res_json = response.json()
            # BFF 공통 envelope인 result 필드를 한 번 벗겨서 반환
            if "result" in res_json:
                return res_json["result"]
            return res_json
        else:
            print(f"[API ERROR] {path} 호출 에러 (Status Code: {response.status_code}): {response.text}")
            response.raise_for_status()

    # --- 실시간 시세 관련 API ---
    def get_prices(self, symbols: List[str]) -> List[Dict[str, Any]]:
        """
        현재가 다건 조회 (최대 200건)
        GET /api/v1/prices?symbols=005930,000660
        """
        if self.run_mode == "MOCK":
            return self._generate_mock_prices(symbols)
            
        # 콤마로 구분하여 호출
        symbols_str = ",".join(symbols)
        return self._request("GET", "/api/v1/prices", params={"symbols": symbols_str})

    def get_candles(self, symbol: str, interval: str = "1m", count: int = 100, adjusted: bool = True) -> Dict[str, Any]:
        """
        캔들 차트 조회 (최대 200개 봉)
        GET /api/v1/candles?symbol=...&interval=...&count=...&adjusted=...
        """
        if self.run_mode == "MOCK":
            return self._generate_mock_candles(symbol, interval, count)
            
        params = {
            "symbol": symbol,
            "interval": interval,
            "count": count,
            "adjusted": str(adjusted).lower()
        }
        return self._request("GET", "/api/v1/candles", params=params)

    def get_commissions(self) -> List[Dict[str, Any]]:
        """
        시장별 매매 수수료 조회
        GET /api/v1/commissions
        """
        if self.run_mode == "MOCK":
            # Mock 수수료 데이터 반환
            return [
                {
                    "marketCountry": "KR",
                    "commissionRate": "0.00015",
                    "startDate": "2026-01-01",
                    "endDate": None
                },
                {
                    "marketCountry": "US",
                    "commissionRate": "0.001",
                    "startDate": "2026-01-01",
                    "endDate": None
                }
            ]
        return self._request("GET", "/api/v1/commissions", need_account=True)

    # --- 가상 Mock 데이터 생성 로직 ---
    def _generate_mock_prices(self, symbols: List[str]) -> List[Dict[str, Any]]:
        """실시간 랜덤 시세 데이터 생성 (24시간 가상 매매 테스트용)"""
        result = []
        for sym in symbols:
            # 1. 초기 기준가 설정
            if sym not in self._mock_last_prices:
                if sym.isdigit():  # 국내주식 (예: 6자리 숫자)
                    self._mock_last_prices[sym] = float(random.randint(50000, 150000))
                else:  # 미국주식
                    self._mock_last_prices[sym] = float(random.randint(100, 300))
            
            # 2. 가격 변동성 부여 (-1.5% ~ +1.5%)
            change_pct = random.uniform(-0.015, 0.015)
            last_price = self._mock_last_prices[sym]
            new_price = last_price * (1 + change_pct)
            
            # 소수점 정리 (국내 주식은 10원/50원/100원 단위 호가 반올림 처리, 미국 주식은 소수점 둘째자리)
            if sym.isdigit():
                new_price = round(new_price, -1)  # 10원 단위 반올림
                currency = "KRW"
            else:
                new_price = round(new_price, 2)
                currency = "USD"
                
            self._mock_last_prices[sym] = new_price
            
            result.append({
                "symbol": sym,
                "timestamp": datetime.now().isoformat(),
                "lastPrice": str(new_price),
                "currency": currency
            })
        return result

    def _generate_mock_candles(self, symbol: str, interval: str, count: int) -> Dict[str, Any]:
        """
        가상 캔들 데이터 생성
        스캘핑 전략의 300% 거래량 폭발 양봉 조건을 만족할 수 있는 캔들을 무작위 생성합니다.
        """
        # 해당 종목의 현재 가격 가져오기
        if symbol not in self._mock_last_prices:
            self._generate_mock_prices([symbol])
        current_price = self._mock_last_prices[symbol]
        currency = "KRW" if symbol.isdigit() else "USD"
        
        # 캐시된 캔들이 없으면 초기 생성
        if symbol not in self._mock_candle_history:
            self._mock_candle_history[symbol] = []
            
        history = self._mock_candle_history[symbol]
        
        # 만약 캐시된 캔들이 부족하면 count 개수만큼 채움
        if len(history) < count:
            base_time = datetime.now() - timedelta(minutes=count)
            temp_price = current_price * 0.95  # 초기 가격은 조금 낮게 설정
            
            for i in range(count):
                candle_time = base_time + timedelta(minutes=i)
                # 일반적인 거래량 생성 (국내/해외 다르게)
                base_volume = random.randint(1000, 5000)
                
                # 가끔 거래량 300% 이상 폭발 및 양봉 생성 (15% 확률)
                is_scalping_signal = random.random() < 0.15
                if is_scalping_signal:
                    volume = base_volume * random.randint(3, 5)  # 300% ~ 500% 폭발
                    open_p = temp_price
                    close_p = temp_price * (1 + random.uniform(0.005, 0.015))  # 무조건 양봉
                else:
                    volume = base_volume
                    open_p = temp_price
                    close_p = temp_price * (1 + random.uniform(-0.004, 0.004))
                
                high_p = max(open_p, close_p) * (1 + random.uniform(0.0, 0.002))
                low_p = min(open_p, close_p) * (1 - random.uniform(0.0, 0.002))
                
                # 라운딩
                if currency == "KRW":
                    open_p, close_p, high_p, low_p = round(open_p, -1), round(close_p, -1), round(high_p, -1), round(low_p, -1)
                else:
                    open_p, close_p, high_p, low_p = round(open_p, 2), round(close_p, 2), round(high_p, 2), round(low_p, 2)
                
                history.append({
                    "timestamp": candle_time.isoformat(),
                    "openPrice": str(open_p),
                    "highPrice": str(high_p),
                    "lowPrice": str(low_p),
                    "closePrice": str(close_p),
                    "volume": str(volume),
                    "currency": currency
                })
                temp_price = close_p
                
        # 실시간 캔들 업데이트 (최근 1분봉 갱신)
        # 매번 조회 시 가장 최신 봉을 현재가 기준으로 살짝 업데이트하고, 1분이 지나면 새로운 봉을 밀어넣음
        last_candle = history[-1]
        last_time = datetime.fromisoformat(last_candle["timestamp"])
        now_time = datetime.now()
        
        if now_time - last_time >= timedelta(minutes=1):
            # 1분 경과: 새로운 봉 추가 및 오래된 봉 제거
            new_candle_time = last_time + timedelta(minutes=1)
            base_volume = random.randint(1000, 5000)
            
            # 15% 확률로 거래량 폭발 양봉 조건 충족
            is_scalping_signal = random.random() < 0.15
            open_p = float(last_candle["closePrice"])
            
            if is_scalping_signal:
                volume = base_volume * random.randint(3, 5)
                close_p = open_p * (1 + random.uniform(0.006, 0.015))
            else:
                volume = base_volume
                close_p = current_price  # 현재 실시간 현재가를 종가로 매칭
                
            high_p = max(open_p, close_p) * (1 + random.uniform(0.0, 0.002))
            low_p = min(open_p, close_p) * (1 - random.uniform(0.0, 0.002))
            
            if currency == "KRW":
                open_p, close_p, high_p, low_p = round(open_p, -1), round(close_p, -1), round(high_p, -1), round(low_p, -1)
            else:
                open_p, close_p, high_p, low_p = round(open_p, 2), round(close_p, 2), round(high_p, 2), round(low_p, 2)
                
            new_candle = {
                "timestamp": new_candle_time.isoformat(),
                "openPrice": str(open_p),
                "highPrice": str(high_p),
                "lowPrice": str(low_p),
                "closePrice": str(close_p),
                "volume": str(volume),
                "currency": currency
            }
            history.append(new_candle)
            if len(history) > count:
                history.pop(0)
        else:
            # 1분 미만 경과: 마지막 봉의 종가를 현재가로 동적으로 업데이트
            last_candle["closePrice"] = str(current_price)
            last_candle["highPrice"] = str(max(float(last_candle["highPrice"]), current_price))
            last_candle["lowPrice"] = str(min(float(last_candle["lowPrice"]), current_price))
            
        # 최신 순(내림차순)으로 정렬하여 반환 (명세서 응답 예시는 보통 최신순인 경우가 많으므로 내림차순 리턴)
        sorted_candles = sorted(history, key=lambda x: x["timestamp"], reverse=True)[:count]
        
        # nextBefore는 다음 페이지 조회를 위한 키값 (가장 오래된 봉의 timestamp)
        next_before = sorted_candles[-1]["timestamp"] if sorted_candles else None
        
        return {
            "candles": sorted_candles,
            "nextBefore": next_before
        }

# 전역 싱글톤 API 클라이언트 인스턴스
api_client = TossApiClient()

if __name__ == "__main__":
    # 시세 조회 테스트
    print("RUN_MODE:", api_client.run_mode)
    prices = api_client.get_prices(["005930", "AAPL"])
    print("Prices:", prices)
    
    candles = api_client.get_candles("AAPL", count=5)
    print("Candles (AAPL):", candles)
