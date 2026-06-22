import os
import json
import uuid
from datetime import datetime
from typing import Dict, Any, Tuple
from src.db_manager import get_connection

class TossPortfolioManager:
    """
    가상 포트폴리오 관리, 환전, 수수료, 세금 및 소수점 체결 엔진
    """
    def __init__(self):
        self.config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
        self.load_config()

    def load_config(self):
        """config.json 파일에서 수수료 및 환율 설정을 로드합니다."""
        with open(self.config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        self.fee_rates = config["fee_rates"]
        self.fixed_usd_krw_rate = self.fee_rates.get("fixed_usd_krw_rate", 1380.0)

    def get_account_summary(self, account_id: str) -> Dict[str, Any]:
        """특정 가상 계좌의 예수금 정보를 조회합니다."""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT krw_balance, usd_balance, strategy_name FROM accounts WHERE account_id = ?", (account_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return {
                "account_id": account_id,
                "strategy_name": row[2],
                "krw_balance": row[0],
                "usd_balance": row[1]
            }
        raise ValueError(f"계좌 ID {account_id}를 찾을 수 없습니다.")

    def get_positions(self, account_id: str) -> list[Dict[str, Any]]:
        """특정 가상 계좌의 보유 종목 목록을 조회합니다."""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT symbol, quantity, average_price, currency FROM positions WHERE account_id = ? AND quantity > 0", 
            (account_id,)
        )
        rows = cursor.fetchall()
        conn.close()
        
        return [
            {
                "symbol": r[0],
                "quantity": r[1],
                "average_price": r[2],
                "currency": r[3]
            } for r in rows
        ]

    def process_virtual_order(self, account_id: str, symbol: str, side: str, order_type: str, 
                              price: float, quantity: float = 0.0, order_amount: float = 0.0,
                              client_order_id: str = None) -> Dict[str, Any]:
        """
        가상 체결 엔진: 주문을 가상으로 처리하고 계좌 잔고 및 포트폴리오를 업데이트합니다.
        
        - side: 'BUY' or 'SELL'
        - order_type: 'LIMIT' or 'MARKET'
        - price: 체결 단가 (시장가 또는 지정가)
        - quantity: 수량 (수량 기반 주문 시)
        - order_amount: 주문 금액 (미국 주식 금액 기반 주문 시, USD 단위)
        """
        self.load_config()  # 실시간 설정 반영을 위해 매 요청 시 로드
        
        is_us_stock = not symbol.isdigit()
        currency = "USD" if is_us_stock else "KRW"
        
        if not client_order_id:
            # client_order_id가 없으면 접두사 + uuid로 생성
            prefix = "ARBI_" if account_id == 'A' else "SCAL_"
            client_order_id = f"{prefix}{uuid.uuid4().hex[:16]}"

        conn = get_connection()
        cursor = conn.cursor()
        
        try:
            # 1. 현재 계좌 잔고 가져오기 (Lock을 위해 트랜잭션 내에서 처리)
            cursor.execute("SELECT krw_balance, usd_balance FROM accounts WHERE account_id = ?", (account_id,))
            krw_bal, usd_bal = cursor.fetchone()
            
            # 수수료율 세팅
            rates = self.fee_rates
            
            # 2. 거래 계산
            if side == "BUY":
                if order_amount > 0:
                    # [미국 주식 금액 기반 주문 - BUY]
                    if not is_us_stock:
                        raise ValueError("금액 기반 주문(orderAmount)은 미국 주식 전용입니다.")
                    
                    # 1달러당 고정 환율 및 환전 스프레드 적용
                    fixed_rate = self.fixed_usd_krw_rate
                    exch_rate = rates["exchange_fee_rate"]
                    
                    # 달러 잔고가 부족하면 원화 잔고에서 가상 환전
                    if usd_bal < order_amount:
                        needed_usd = order_amount - usd_bal
                        required_krw = needed_usd * fixed_rate * (1 + exch_rate)
                        
                        if krw_bal < required_krw:
                            return {"status": "REJECTED", "reason": "insufficient-buying-power", "message": "원화 예수금이 부족하여 환전 및 매수가 불가합니다."}
                        
                        # 가상 환전 실행
                        krw_bal -= required_krw
                        usd_bal += needed_usd
                        cursor.execute("UPDATE accounts SET krw_balance = ?, usd_balance = ? WHERE account_id = ?", (krw_bal, usd_bal, account_id))
                    
                    # 달러 잔고에서 order_amount 차감
                    usd_bal -= order_amount
                    
                    # 수수료를 제하고 실제 매수에 쓰인 금액 계산
                    # gross_amount = net_amount + fee = net_amount * (1 + usd_buy_fee)
                    usd_buy_fee_rate = rates["usd_buy_fee"]
                    net_buy_amount = order_amount / (1 + usd_buy_fee_rate)
                    fee = order_amount - net_buy_amount
                    tax = 0.0
                    
                    # 소수점 수량 계산
                    exec_qty = net_buy_amount / price
                    exec_price = price
                    
                else:
                    # [수량 기반 주문 - BUY]
                    exec_qty = quantity
                    exec_price = price
                    gross_amount = exec_qty * exec_price
                    
                    if is_us_stock:
                        fee_rate = rates["usd_buy_fee"]
                        fee = gross_amount * fee_rate
                        tax = 0.0
                        total_cost = gross_amount + fee
                        
                        # 달러 잔고 부족 시 가상 환전
                        if usd_bal < total_cost:
                            needed_usd = total_cost - usd_bal
                            fixed_rate = self.fixed_usd_krw_rate
                            exch_rate = rates["exchange_fee_rate"]
                            required_krw = needed_usd * fixed_rate * (1 + exch_rate)
                            
                            if krw_bal < required_krw:
                                return {"status": "REJECTED", "reason": "insufficient-buying-power", "message": "원화 예수금이 부족하여 환전 및 매수가 불가합니다."}
                            
                            krw_bal -= required_krw
                            usd_bal += needed_usd
                            cursor.execute("UPDATE accounts SET krw_balance = ?, usd_balance = ? WHERE account_id = ?", (krw_bal, usd_bal, account_id))
                        
                        usd_bal -= total_cost
                    else:
                        fee_rate = rates["krw_buy_fee"]
                        fee = gross_amount * fee_rate
                        tax = 0.0
                        total_cost = gross_amount + fee
                        
                        if krw_bal < total_cost:
                            return {"status": "REJECTED", "reason": "insufficient-buying-power", "message": "원화 예수금이 부족하여 매수가 불가합니다."}
                        
                        krw_bal -= total_cost

                # 3. BUY 처리: positions 테이블 갱신 (평단가 및 수량 가중 평균 계산)
                cursor.execute("SELECT quantity, average_price FROM positions WHERE account_id = ? AND symbol = ?", (account_id, symbol))
                pos_row = cursor.fetchone()
                
                if pos_row:
                    old_qty, old_avg_p = pos_row
                    new_qty = old_qty + exec_qty
                    new_avg_p = ((old_qty * old_avg_p) + (exec_qty * exec_price)) / new_qty
                    cursor.execute(
                        "UPDATE positions SET quantity = ?, average_price = ? WHERE account_id = ? AND symbol = ?",
                        (new_qty, new_avg_p, account_id, symbol)
                    )
                else:
                    cursor.execute(
                        "INSERT INTO positions (account_id, symbol, quantity, average_price, currency) VALUES (?, ?, ?, ?, ?)",
                        (account_id, symbol, exec_qty, exec_price, currency)
                    )

            elif side == "SELL":
                # 보유 종목 체크
                cursor.execute("SELECT quantity, average_price FROM positions WHERE account_id = ? AND symbol = ?", (account_id, symbol))
                pos_row = cursor.fetchone()
                
                if not pos_row or pos_row[0] <= 0:
                    return {"status": "REJECTED", "reason": "no-position", "message": "해당 종목의 보유 포지션이 없습니다."}
                
                old_qty, old_avg_p = pos_row
                
                if order_amount > 0:
                    # [미국 주식 금액 기반 주문 - SELL]
                    if not is_us_stock:
                        raise ValueError("금액 기반 주문은 미국 주식 전용입니다.")
                    
                    # 체결 수량 계산
                    exec_qty = order_amount / price
                    
                    # 보유량 한도 체크 (보유량보다 매도량이 많으면 전량 매도로 보정)
                    if exec_qty > old_qty:
                        exec_qty = old_qty
                        gross_sell_amount = exec_qty * price
                    else:
                        gross_sell_amount = order_amount
                        
                    exec_price = price
                    
                    # 매도 시 수수료 및 세금 (SEC Fee 등) 차감
                    fee = gross_sell_amount * rates["usd_sell_fee"]
                    tax = gross_sell_amount * rates["usd_sell_tax"]
                    net_receive = gross_sell_amount - fee - tax
                    
                    usd_bal += net_receive
                    
                else:
                    # [수량 기반 주문 - SELL]
                    exec_qty = quantity
                    if exec_qty > old_qty:
                        return {"status": "REJECTED", "reason": "insufficient-position-quantity", "message": f"매도 요청 수량({exec_qty})이 보유 수량({old_qty})을 초과합니다."}
                    
                    exec_price = price
                    gross_sell_amount = exec_qty * exec_price
                    
                    if is_us_stock:
                        fee = gross_sell_amount * rates["usd_sell_fee"]
                        tax = gross_sell_amount * rates["usd_sell_tax"]
                        net_receive = gross_sell_amount - fee - tax
                        usd_bal += net_receive
                    else:
                        fee = gross_sell_amount * rates["krw_sell_fee"]
                        tax = gross_sell_amount * rates["krw_sell_tax"]
                        net_receive = gross_sell_amount - fee - tax
                        krw_bal += net_receive

                # SELL 처리: positions 테이블 갱신
                new_qty = old_qty - exec_qty
                if new_qty <= 0.000001:  # 부동소수점 오차 감안
                    cursor.execute("DELETE FROM positions WHERE account_id = ? AND symbol = ?", (account_id, symbol))
                else:
                    cursor.execute(
                        "UPDATE positions SET quantity = ? WHERE account_id = ? AND symbol = ?",
                        (new_qty, account_id, symbol)
                    )
            
            # 4. 계좌 테이블 업데이트
            cursor.execute("UPDATE accounts SET krw_balance = ?, usd_balance = ? WHERE account_id = ?", (krw_bal, usd_bal, account_id))
            
            # 5. 주문 체결 내역 테이블에 기록
            cursor.execute(
                "INSERT INTO orders (client_order_id, account_id, symbol, order_type, quantity, price, currency, fee, tax, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (client_order_id, account_id, symbol, side, exec_qty, exec_price, currency, fee, tax, datetime.now().isoformat())
            )
            
            conn.commit()
            
            return {
                "status": "FILLED",
                "clientOrderId": client_order_id,
                "symbol": symbol,
                "side": side,
                "execPrice": exec_price,
                "execQty": exec_qty,
                "fee": fee,
                "tax": tax,
                "krw_balance": krw_bal,
                "usd_balance": usd_bal
            }
            
        except Exception as e:
            conn.rollback()
            print(f"[체결 엔진 ERROR] 주문 처리 중 에러 발생: {e}")
            return {"status": "ERROR", "reason": "system-error", "message": str(e)}
            
        finally:
            conn.close()

    def get_portfolio_valuation(self, account_id: str, current_prices: Dict[str, float]) -> Dict[str, Any]:
        """
        가상 계좌 전체 자산 가치 평가 (예수금, 평가 금액, 누적 수익률 계산)
        
        - current_prices: {'005930': 72000.0, 'AAPL': 185.7} 형식의 실시간 종목별 가격 정보
        """
        summary = self.get_account_summary(account_id)
        positions = self.get_positions(account_id)
        
        krw_balance = summary["krw_balance"]
        usd_balance = summary["usd_balance"]
        
        # 초기 투자금 정의 (수익률 계산용)
        initial_investment_krw = 7000000.0 if account_id == 'A' else 3000000.0
        
        eval_stock_krw = 0.0
        eval_stock_usd = 0.0
        
        updated_positions = []
        
        for pos in positions:
            symbol = pos["symbol"]
            qty = pos["quantity"]
            avg_price = pos["average_price"]
            curr = pos["currency"]
            
            # 실시간 가격이 들어오지 않으면 평단가로 평가
            cur_price = current_prices.get(symbol, avg_price)
            
            eval_value = qty * cur_price
            purchase_value = qty * avg_price
            profit = eval_value - purchase_value
            profit_rate = (profit / purchase_value) * 100 if purchase_value > 0 else 0.0
            
            if curr == "KRW":
                eval_stock_krw += eval_value
            else:
                eval_stock_usd += eval_value
                
            updated_positions.append({
                "symbol": symbol,
                "quantity": qty,
                "average_price": avg_price,
                "current_price": cur_price,
                "eval_value": eval_value,
                "profit": profit,
                "profit_rate": profit_rate,
                "currency": curr
            })

        # 달러 자산 및 예수금을 원화로 환산하여 합산 요약 계산
        fixed_rate = self.fixed_usd_krw_rate
        total_eval_krw = krw_balance + eval_stock_krw + (usd_balance * fixed_rate) + (eval_stock_usd * fixed_rate)
        total_profit_krw = total_eval_krw - initial_investment_krw
        total_return_rate = (total_profit_krw / initial_investment_krw) * 100
        
        return {
            "account_id": account_id,
            "strategy_name": summary["strategy_name"],
            "krw_balance": krw_balance,
            "usd_balance": usd_balance,
            "eval_stock_krw": eval_stock_krw,
            "eval_stock_usd": eval_stock_usd,
            "total_eval_krw": total_eval_krw,
            "total_profit_krw": total_profit_krw,
            "total_return_rate": total_return_rate,
            "positions": updated_positions
        }

# 전역 싱글톤 포트폴리오 관리 인스턴스
portfolio_manager = TossPortfolioManager()

if __name__ == "__main__":
    # 데이터베이스 초기화가 필요한 경우
    from src.db_manager import init_db
    init_db()
    
    # 가상 매수 테스트 (미국 주식 금액 주문)
    res = portfolio_manager.process_virtual_order(
        account_id='B',
        symbol='AAPL',
        side='BUY',
        order_type='MARKET',
        price=180.0,
        order_amount=500.0  # 500달러치 매수
    )
    print("Virtual Order Buy Result:", res)
    
    # 평가액 조회
    val = portfolio_manager.get_portfolio_valuation('B', {'AAPL': 190.0})
    print("Portfolio Valuation:", val)
