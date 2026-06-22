import os
import sys
import time
import json
from datetime import datetime
from dotenv import load_dotenv

# Rich 라이브러리 임포트 (화려한 터미널 UI 데코레이션을 위해 사용)
try:
    from rich.console import Console
    from rich.table import Table
    from rich.live import Live
    from rich.layout import Layout
    from rich.panel import Panel
except ImportError:
    print("[Warning] 'rich' 라이브러리가 설치되어 있지 않습니다. 자동 설치를 제안할 수 있습니다.")
    # 기본 출력을 위한 Fallback 코드나 사용자 안내 필요

from src.db_manager import init_db
from src.api_client import api_client
from src.portfolio import portfolio_manager
from src.strategies import strategies

load_dotenv()

def get_all_symbols(config_path: str) -> list[str]:
    """설정 파일에서 감시할 모든 종목 리스트를 추출합니다."""
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    
    arbi_syms = config["strategies"]["arbitrage"]["symbols"]
    scalp_syms = config["strategies"]["scalping"]["symbols"]
    
    # 중복 제거 후 리스트 반환
    return list(set(arbi_syms + scalp_syms))

def make_dashboard(current_prices: dict) -> Table:
    """Rich 라이브러리를 이용해 가상 계좌 A와 B의 실시간 포트폴리오 현황 표를 생성합니다."""
    # 환율
    fixed_rate = portfolio_manager.fixed_usd_krw_rate
    
    # A, B 계좌 평가 결과 조회
    val_a = portfolio_manager.get_portfolio_valuation('A', current_prices)
    val_b = portfolio_manager.get_portfolio_valuation('B', current_prices)
    
    # 메인 통합 테이블 구성
    main_table = Table(title="📊 [bold magenta]토스증권 OpenAPI 가상 매매 시스템 실시간 대시보드[/bold magenta]", expand=True)
    main_table.add_column("구분", justify="center", style="cyan", no_wrap=True)
    main_table.add_column("가상 계좌 A (차익거래 전략)", justify="left", style="green")
    main_table.add_column("가상 계좌 B (스캘핑 전략)", justify="left", style="yellow")
    
    # 1. 기본 정보 행 추가
    main_table.add_row(
        "전략 개요",
        "통계적 차익거래 (투자금: 7,000,000원)",
        "1분봉 추세 추종 스캘핑 (투자금: 3,000,000원)"
    )
    main_table.add_row(
        "예수금 (원화)",
        f"{val_a['krw_balance']:,.0f} 원",
        f"{val_b['krw_balance']:,.0f} 원"
    )
    main_table.add_row(
        "예수금 (달러)",
        f"$ {val_a['usd_balance']:,.2f}",
        f"$ {val_b['usd_balance']:,.2f}"
    )
    main_table.add_row(
        "주식 평가액 (원화)",
        f"{val_a['eval_stock_krw']:,.0f} 원",
        f"{val_b['eval_stock_krw']:,.0f} 원"
    )
    main_table.add_row(
        "주식 평가액 (달러)",
        f"$ {val_a['eval_stock_usd']:,.2f}",
        f"$ {val_b['eval_stock_usd']:,.2f}"
    )
    
    # 총 평가액 (원화 환산)
    main_table.add_row(
        "총 자산 가치 (원화)",
        f"[bold]{val_a['total_eval_krw']:,.0f} 원[/bold]",
        f"[bold]{val_b['total_eval_krw']:,.0f} 원[/bold]"
    )
    
    # 수익률 행 색상 데코레이션
    profit_a = val_a['total_profit_krw']
    profit_b = val_b['total_profit_krw']
    
    color_a = "red" if profit_a > 0 else "blue" if profit_a < 0 else "white"
    color_b = "red" if profit_b > 0 else "blue" if profit_b < 0 else "white"
    
    main_table.add_row(
        "누적 손익 (원화)",
        f"[{color_a}]{profit_a:+,.0f} 원 ({val_a['total_return_rate']:+.2f}%)[/{color_a}]",
        f"[{color_b}]{profit_b:+,.0f} 원 ({val_b['total_return_rate']:+.2f}%)[/{color_b}]"
    )
    
    # 2. 보유 종목 리스트 테이블 임베딩
    def get_positions_subtable(val_data) -> str:
        if not val_data["positions"]:
            return "[gray]보유 종목 없음[/gray]"
        
        lines = []
        for p in val_data["positions"]:
            sign = "+" if p['profit'] > 0 else ""
            color = "red" if p['profit'] > 0 else "blue" if p['profit'] < 0 else "white"
            
            if p['currency'] == "KRW":
                lines.append(
                    f"• {p['symbol']}: {p['quantity']:.0f}주 | 평단 {p['average_price']:,.0f} | 현재가 {p['current_price']:,.0f} | "
                    f"[{color}]손익 {sign}{p['profit']:+,.0f} ({p['profit_rate']:+.2f}%)[/{color}]"
                )
            else:
                lines.append(
                    f"• {p['symbol']}: {p['quantity']:.4f}주 | 평단 ${p['average_price']:,.2f} | 현재가 ${p['current_price']:,.2f} | "
                    f"[{color}]손익 ${sign}{p['profit']:+,.2f} ({p['profit_rate']:+.2f}%)[/{color}]"
                )
        return "\n".join(lines)

    main_table.add_row(
        "보유 포지션 상세",
        get_positions_subtable(val_a),
        get_positions_subtable(val_b)
    )
    
    return main_table

def main():
    # 1. DB 초기화
    print("[System] 데이터베이스 초기화 진행 중...")
    init_db()
    
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    all_symbols = get_all_symbols(config_path)
    
    run_mode = os.getenv("RUN_MODE", "MOCK").upper()
    print(f"[System] 시스템이 기동되었습니다. (실행 모드: {run_mode})")
    print(f"[System] 감시 종목군: {all_symbols}")
    
    loop_count = 0
    current_prices = {}
    
    # 콘솔 라이브 업데이트용 객체 생성
    console = Console()
    
    try:
        # Live 디스플레이를 활용해 터미널 화면을 부드럽게 갱신
        with Live(Console().render(Panel("[bold yellow]시세 데이터를 가져오고 있습니다...[/bold yellow]")), auto_refresh=False, screen=True) as live:
            while True:
                # A. 실시간 현재가 다건 조회 (최대 200개 종목을 1초 주기로 '딱 한 번'만 감시)
                try:
                    price_res = api_client.get_prices(all_symbols)
                    
                    # 수집된 현재가를 딕셔너리로 맵핑
                    for item in price_res:
                        sym = item["symbol"]
                        price = float(item["lastPrice"])
                        current_prices[sym] = price
                        
                except Exception as e:
                    # API 호출에 일시적인 장애가 나도 시스템이 죽지 않도록 방어
                    pass
                
                # B. 매매 전략 핵심 알고리즘 병렬적/독립적 가상 구동
                if current_prices:
                    # 전략 1: 통계적 차익거래 (가상 계좌 A)
                    strategies.run_arbitrage_strategy(current_prices)
                    
                    # 전략 2: 1분봉 추세 추종 스캘핑 (가상 계좌 B)
                    strategies.run_scalping_strategy(current_prices)
                
                # C. 5초마다 실시간 가상 포트폴리오 대시보드 업데이트
                # 루프 사이클을 1초로 잡고, 5회째에 화면을 업데이트
                if loop_count % 5 == 0:
                    if current_prices:
                        dashboard_table = make_dashboard(current_prices)
                        # live display 갱신
                        live.update(dashboard_table, refresh=True)
                
                loop_count += 1
                time.sleep(1.0)  # 1초 주기 감시 루프
                
    except KeyboardInterrupt:
        print("\n[System] 사용자에 의해 가상 매매 시스템이 종료되었습니다.")
        sys.exit(0)

if __name__ == "__main__":
    main()
