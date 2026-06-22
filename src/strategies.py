import os
import json
import time
from typing import Dict, List, Any
from src.api_client import api_client
from src.portfolio import portfolio_manager

class TossTradingStrategies:
    """
    통계적 차익거래(Arbitrage) 및 스캘핑(Scalping) 매매 전략 구현 클래스
    """
    def __init__(self):
        self.config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
        self.load_config()
        
        # 스캘핑 손익절 추적을 위한 상태 저장 (보유 중인 스캘핑 종목의 가상 진입가 관리)
        self.scalping_positions: Dict[str, Dict[str, float]] = {}

    def load_config(self):
        """설정 파일에서 전략 매개변수를 로드합니다."""
        with open(self.config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        self.arbi_cfg = config["strategies"]["arbitrage"]
        self.scalp_cfg = config["strategies"]["scalping"]

    def run_arbitrage_strategy(self, current_prices: Dict[str, float]):
        """
        [전략 1] 통계적 차익거래 (가상 계좌 A)
        지정한 연관 종목 쌍(예: 삼성전자, SK하이닉스)의 괴리율을 계산해 교차 가상 매매를 집행합니다.
        """
        self.load_config()
        symbols = self.arbi_cfg["symbols"]
        if len(symbols) < 2:
            return
            
        sym_a, sym_b = symbols[0], symbols[1]
        price_a = current_prices.get(sym_a)
        price_b = current_prices.get(sym_b)
        
        if not price_a or not price_b:
            return  # 두 종목의 가격 정보가 모두 필요
            
        # 두 종목의 가격 비율 계산
        price_ratio = price_a / price_b
        
        # 예시 타겟 비율 (기준값): 2026년 역사적 평균 비율 또는 고정비율 설정
        # 뼈대 코드이므로, 0.55 근처를 기준으로 삼아 2% 이상 괴리 시 교차 매매
        target_ratio = 0.55
        threshold = self.arbi_cfg["threshold"]  # 0.02
        spread = (price_ratio - target_ratio) / target_ratio
        
        # 현재 보유 내역 확인
        positions = {p["symbol"]: p for p in portfolio_manager.get_positions('A')}
        trade_amount = self.arbi_cfg["trade_amount_krw"]  # 1,000,000 KRW
        
        # 괴리가 임계치를 초과하여 발생한 경우 (A가 고평가, B가 저평가 -> A 매도 / B 매수)
        if spread > threshold:
            print(f"[전략 A-차익거래] Spread 고평가 감지! ({spread*100:.2f}% > {threshold*100:.2f}%)")
            # sym_a(고평가) 매도 & sym_b(저평가) 매수
            if sym_a in positions:
                qty_to_sell = positions[sym_a]["quantity"]
                if qty_to_sell > 0:
                    portfolio_manager.process_virtual_order(
                        account_id='A', symbol=sym_a, side='SELL', order_type='MARKET',
                        price=price_a, quantity=qty_to_sell
                    )
                    print(f" -> [차익거래] {sym_a} {qty_to_sell}주 가상 매도 완료")
            
            if sym_b not in positions:
                qty_to_buy = trade_amount / price_b
                portfolio_manager.process_virtual_order(
                    account_id='A', symbol=sym_b, side='BUY', order_type='MARKET',
                    price=price_b, quantity=qty_to_buy
                )
                print(f" -> [차익거래] {sym_b} {qty_to_buy:.2f}주 가상 매수 완료")

        # 괴리가 반대로 발생한 경우 (A가 저평가, B가 고평가 -> A 매수 / B 매도)
        elif spread < -threshold:
            print(f"[전략 A-차익거래] Spread 저평가 감지! ({spread*100:.2f}% < {-threshold*100:.2f}%)")
            # sym_a(저평가) 매수 & sym_b(고평가) 매도
            if sym_b in positions:
                qty_to_sell = positions[sym_b]["quantity"]
                if qty_to_sell > 0:
                    portfolio_manager.process_virtual_order(
                        account_id='A', symbol=sym_b, side='SELL', order_type='MARKET',
                        price=price_b, quantity=qty_to_sell
                    )
                    print(f" -> [차익거래] {sym_b} {qty_to_sell}주 가상 매도 완료")
            
            if sym_a not in positions:
                qty_to_buy = trade_amount / price_a
                portfolio_manager.process_virtual_order(
                    account_id='A', symbol=sym_a, side='BUY', order_type='MARKET',
                    price=price_a, quantity=qty_to_buy
                )
                print(f" -> [차익거래] {sym_a} {qty_to_buy:.2f}주 가상 매수 완료")

    def run_scalping_strategy(self, current_prices: Dict[str, float]):
        """
        [전략 2] 1분봉 추세 추종 스캘핑 (가상 계좌 B)
        1분봉을 개별 호출하여 거래량이 직전 대비 300% 이상 급증한 양봉일 때 매수하며,
        동시에 실시간 현재가로 +0.5% 익절 / -0.3% 손절을 관리합니다.
        """
        self.load_config()
        symbols = self.scalp_cfg["symbols"]
        
        # 1. 기존 보유한 스캘핑 종목의 실시간 손익절 감시 및 대응
        self._check_scalping_exits(current_prices)
        
        # 2. 감시 종목별 1분봉 조회 및 조건 탐색
        # 한도 방어를 위해 호출 간 시차(Sleep)를 부여하는 분산 호출 메커니즘 적용!
        for sym in symbols:
            # 포지션이 이미 있으면 신규 매수 감시 생략 (스캘핑 중복 방지)
            if self._has_position('B', sym):
                continue
                
            try:
                # 1분봉 호출 한도 분산: 종목별 호출 전 300ms 대기하여 429 방어
                time.sleep(0.3)
                
                # 1분봉 캔들 최근 3개만 조회
                candle_res = api_client.get_candles(symbol=sym, interval="1m", count=3, adjusted=True)
                candles = candle_res.get("candles", [])
                
                if len(candles) < 3:
                    continue
                
                # candles[0]은 현재 미완성 1분봉, candles[1]이 직전 완성 1분봉, candles[2]는 그 전 완성 1분봉
                prev_candle = candles[1]
                prev_prev_candle = candles[2]
                
                prev_volume = float(prev_candle["volume"])
                prev_prev_volume = float(prev_prev_candle["volume"])
                
                prev_open = float(prev_candle["openPrice"])
                prev_close = float(prev_candle["closePrice"])
                
                # 조건 A: 직전 분봉 대비 거래량이 300% 이상 폭발 (volume_multiplier가 3.0 이면 300%)
                vol_ratio = (prev_volume / prev_prev_volume) if prev_prev_volume > 0 else 0
                is_vol_explode = vol_ratio >= self.scalp_cfg["volume_multiplier"]
                
                # 조건 B: 직전 분봉이 양봉 (종가 > 시가)
                is_bull_candle = prev_close > prev_open
                
                if is_vol_explode and is_bull_candle:
                    print(f"[전략 B-스캘핑] 매수 조건 충족! 종목: {sym} (거래량폭발: {vol_ratio*100:.1f}%, 양봉)")
                    
                    cur_price = current_prices.get(sym)
                    if not cur_price:
                        # 캔들의 종가를 진입가로 사용
                        cur_price = prev_close
                        
                    # 미국 주식인 경우 금액 기반 주문(orderAmount)으로 쏩니다.
                    is_us_stock = not sym.isdigit()
                    trade_amount_usd = self.scalp_cfg["trade_amount_usd"]  # 200.0 USD
                    
                    if is_us_stock:
                        # 미국 주식 금액 주문 가상 체결 호출
                        order_res = portfolio_manager.process_virtual_order(
                            account_id='B',
                            symbol=sym,
                            side='BUY',
                            order_type='MARKET',
                            price=cur_price,
                            order_amount=trade_amount_usd
                        )
                    else:
                        # 국내 주식은 원화로 환산하여 수량 주문
                        fixed_rate = portfolio_manager.fixed_usd_krw_rate
                        trade_amount_krw = trade_amount_usd * fixed_rate
                        qty_to_buy = round(trade_amount_krw / cur_price)
                        
                        order_res = portfolio_manager.process_virtual_order(
                            account_id='B',
                            symbol=sym,
                            side='BUY',
                            order_type='MARKET',
                            price=cur_price,
                            quantity=qty_to_buy
                        )
                        
                    if order_res.get("status") == "FILLED":
                        # 실제 가상 체결 정보 기록
                        exec_qty = order_res["execQty"]
                        exec_price = order_res["execPrice"]
                        self.scalping_positions[sym] = {
                            "entry_price": exec_price,
                            "quantity": exec_qty
                        }
                        print(f" -> [스캘핑] {sym} {exec_qty:.4f}주 가상 매수 진입 완료 (평단: {exec_price})")
                        
            except Exception as e:
                print(f"[전략 B ERROR] 종목 {sym} 스캘핑 분석 중 오류: {e}")

    def _check_scalping_exits(self, current_prices: Dict[str, float]):
        """보유 중인 스캘핑 포지션에 대해 실시간 익절/손절을 체크하고 매도합니다."""
        positions = portfolio_manager.get_positions('B')
        tp_rate = self.scalp_cfg["take_profit_rate"]  # 0.005 (0.5%)
        sl_rate = self.scalp_cfg["stop_loss_rate"]   # 0.003 (0.3%)
        
        for pos in positions:
            sym = pos["symbol"]
            qty = pos["quantity"]
            entry_price = pos["average_price"]
            
            cur_price = current_prices.get(sym)
            if not cur_price or qty <= 0:
                continue
                
            return_rate = (cur_price - entry_price) / entry_price
            
            # 익절선 또는 손절선 도달 체크
            is_take_profit = return_rate >= tp_rate
            is_stop_loss = return_rate <= -sl_rate
            
            if is_take_profit or is_stop_loss:
                reason = "익절(TakeProfit)" if is_take_profit else "손절(StopLoss)"
                print(f"[전략 B-스캘핑] {reason} 신호 감지! 종목: {sym} (수익률: {return_rate*100:.2f}%)")
                
                # 시장가 전량 매도 주문 실행
                # 미국 주식은 금액 기반 주문(orderAmount)으로 매도 처리할 수도 있고, 수량 기반으로도 가능
                # 여기서는 보유한 수량(qty)을 모두 매도하므로 수량 기반 매도 실행
                order_res = portfolio_manager.process_virtual_order(
                    account_id='B',
                    symbol=sym,
                    side='SELL',
                    order_type='MARKET',
                    price=cur_price,
                    quantity=qty
                )
                
                if order_res.get("status") == "FILLED":
                    if sym in self.scalping_positions:
                        del self.scalping_positions[sym]
                    print(f" -> [스캘핑] {sym} {qty:.4f}주 가상 매도 청산 완료 (체결가: {cur_price})")

    def _has_position(self, account_id: str, symbol: str) -> bool:
        """가상 장부에 해당 종목이 있는지 검사합니다."""
        positions = portfolio_manager.get_positions(account_id)
        return any(pos["symbol"] == symbol for pos in positions)

# 전역 전략 인스턴스
strategies = TossTradingStrategies()

if __name__ == "__main__":
    # 가상 매매 테스트
    dummy_prices = {"AAPL": 180.0, "TSLA": 170.0, "005930": 72000.0, "000660": 130000.0}
    strategies.run_arbitrage_strategy(dummy_prices)
    strategies.run_scalping_strategy(dummy_prices)
