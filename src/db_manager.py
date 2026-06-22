import os
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "database.db")

def get_connection():
    """데이터베이스 연결 객체를 반환합니다. 멀티스레드 환경을 위해 timeout을 넉넉히 설정합니다."""
    return sqlite3.connect(DB_PATH, timeout=10.0)

def init_db():
    """데이터베이스 테이블을 생성하고 초기 자산을 설정합니다."""
    conn = get_connection()
    cursor = conn.cursor()

    # 1. 가상 계좌 테이블 생성
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            account_id TEXT PRIMARY KEY,
            strategy_name TEXT NOT NULL,
            krw_balance REAL NOT NULL,
            usd_balance REAL NOT NULL
        )
    """)

    # 2. 보유 종목 테이블 생성
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            account_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            quantity REAL NOT NULL,
            average_price REAL NOT NULL,
            currency TEXT NOT NULL,
            PRIMARY KEY (account_id, symbol)
        )
    """)

    # 3. 가상 주문 및 체결 내역 테이블 생성
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            client_order_id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            order_type TEXT NOT NULL,  -- BUY, SELL
            quantity REAL NOT NULL,
            price REAL NOT NULL,
            currency TEXT NOT NULL,
            fee REAL NOT NULL,
            tax REAL NOT NULL,
            timestamp DATETIME NOT NULL
        )
    """)

    # 4. 초기 가상 계좌 데이터 세팅 (없는 경우에만 삽입)
    # 계좌 A: 통계적 차익거래 (투자금 7,000,000원)
    # 계좌 B: 1분봉 추세 추종 스캘핑 (투자금 3,000,000원)
    cursor.execute("SELECT COUNT(*) FROM accounts")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO accounts VALUES (?, ?, ?, ?)", ('A', 'Statistical Arbitrage', 7000000.0, 0.0))
        cursor.execute("INSERT INTO accounts VALUES (?, ?, ?, ?)", ('B', 'Scalping', 3000000.0, 0.0))
        conn.commit()
        print("[DB] 초기 가상 계좌가 성공적으로 생성되었습니다. (A: 700만 원, B: 300만 원)")
    else:
        print("[DB] 이미 존재하는 가상 계좌 데이터를 사용합니다.")

    conn.close()

if __name__ == "__main__":
    init_db()
