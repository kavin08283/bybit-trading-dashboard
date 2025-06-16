# -*- coding: utf-8 -*-
import streamlit as st
import pandas as pd
import json
import time
import hmac
import hashlib
import urllib.parse
import urllib.request
import logging
from math import floor
from datetime import datetime

# 페이지 설정
st.set_page_config(
    page_title="🚀 Bybit 자동매매 대시보드",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 로거 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# pybit 동적 임포트 (설치 안 되어있으면 안내)
try:
    from pybit.unified_trading import HTTP
    PYBIT_AVAILABLE = True
except ImportError:
    PYBIT_AVAILABLE = False
    st.error("⚠️ pybit 패키지가 설치되지 않았습니다. requirements.txt를 확인해주세요.")

# 세션 상태 초기화
if 'client' not in st.session_state:
    st.session_state.client = None
if 'last_update' not in st.session_state:
    st.session_state.last_update = None
if 'connected' not in st.session_state:
    st.session_state.connected = False

# 상수
TRADE_CATEGORY = "linear"

# 스타일링
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: bold;
        text-align: center;
        margin-bottom: 2rem;
        background: linear-gradient(90deg, #FF6B6B, #4ECDC4);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    .metric-card {
        background: white;
        padding: 1rem;
        border-radius: 10px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        border: 1px solid #ddd;
    }
    .success-msg {
        background: #d4edda;
        color: #155724;
        padding: 0.75rem;
        border-radius: 5px;
        border: 1px solid #c3e6cb;
    }
    .error-msg {
        background: #f8d7da;
        color: #721c24;
        padding: 0.75rem;
        border-radius: 5px;
        border: 1px solid #f5c6cb;
    }
</style>
""", unsafe_allow_html=True)

# ── 텔레그램 알림 ──
def send_telegram(text: str, tg_token: str, tg_chat_id: str):
    if not (tg_token and tg_chat_id): 
        return False
    data = urllib.parse.urlencode({"chat_id": tg_chat_id, "text": text}).encode()
    try:
        urllib.request.urlopen(
            urllib.request.Request(
                f"https://api.telegram.org/bot{tg_token}/sendMessage",
                data=data, method="POST"
            ),
            timeout=5
        )
        return True
    except Exception as e:
        st.error(f"📱 Telegram 전송 실패: {e}")
        return False

# ── 잔고 조회 ──
def get_usdt_balance(client) -> float:
    try:
        resp = client.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        result = resp.get("result", {})
        
        if isinstance(result.get("list"), list):
            for account in result["list"]:
                coins = account.get("coin", [])
                if isinstance(coins, list):
                    for coin_info in coins:
                        if coin_info.get("coin") == "USDT":
                            return float(coin_info.get("walletBalance", 0))
        else:
            usdt_info = result.get("USDT", {})
            if usdt_info:
                return float(usdt_info.get("walletBalance", 0))
                
        return 190.0  # 기본값
        
    except Exception as e:
        st.error(f"💰 잔고 조회 실패: {e}")
        return 190.0

# ── 포지션 조회 ──
def get_positions(client, symbol=None):
    try:
        resp = client.get_positions(category=TRADE_CATEGORY, symbol=symbol)
        positions = resp.get("result", {}).get("list", [])
        
        active_positions = []
        for p in positions:
            if float(p.get("size", 0)) > 0:
                unrealized_pnl = float(p.get("unrealisedPnl", 0))
                avg_price = float(p.get("avgPrice", 1))
                percentage = (unrealized_pnl / (avg_price * float(p.get("size", 1)))) * 100 if avg_price > 0 else 0
                
                active_positions.append({
                    "심볼": p.get("symbol"),
                    "방향": "🟢 롱" if p.get("side") == "Buy" else "🔴 숏",
                    "수량": f"{float(p.get('size', 0)):.4f}",
                    "평균가": f"${float(p.get('avgPrice', 0)):.4f}",
                    "현재가": f"${float(p.get('markPrice', 0)):.4f}",
                    "손익(USDT)": f"{unrealized_pnl:.2f}",
                    "손익(%)": f"{percentage:.2f}%"
                })
        
        return active_positions
    except Exception as e:
        st.error(f"📊 포지션 조회 실패: {e}")
        return []

# ── 미체결 주문 조회 ──
def get_open_orders(client, symbol=None):
    try:
        resp = client.get_open_orders(category=TRADE_CATEGORY, symbol=symbol)
        orders = resp.get("result", {}).get("list", [])
        
        order_list = []
        for order in orders:
            order_list.append({
                "주문ID": order.get("orderId", "")[:8] + "...",
                "심볼": order.get("symbol"),
                "방향": "🟢 Buy" if order.get("side") == "Buy" else "🔴 Sell",
                "타입": order.get("orderType"),
                "수량": f"{float(order.get('qty', 0)):.4f}",
                "가격": f"${float(order.get('price', 0)):.4f}",
                "상태": order.get("orderStatus"),
            })
        
        return order_list
    except Exception as e:
        st.error(f"📋 미체결 주문 조회 실패: {e}")
        return []

# ── 현재가 조회 ──
def get_current_price(client, symbol: str):
    try:
        ticker = client.get_tickers(category=TRADE_CATEGORY, symbol=symbol)
        return float(ticker["result"]["list"][0]["lastPrice"])
    except Exception as e:
        st.error(f"💹 현재가 조회 실패: {e}")
        return 0

# ── 전체 주문 취소 ──
def cancel_all_orders(client, symbol: str):
    try:
        result = client.cancel_all_orders(category=TRADE_CATEGORY, symbol=symbol)
        return result.get("retCode", 0) == 0
    except Exception as e:
        st.error(f"❌ 주문 취소 실패: {e}")
        return False

# ── 심볼 정보 조회 ──
def get_order_unit(client, symbol: str):
    try:
        resp = client.get_instruments_info(category=TRADE_CATEGORY, symbol=symbol)
        info = resp["result"]["list"][0]
        pf = info["priceFilter"]
        lf = info["lotSizeFilter"]
        tick = float(pf["tickSize"])
        min_q = float(lf["minOrderQty"])
        max_q = float(lf["maxOrderQty"])
        step = float(lf.get("qtyStep", min_q))
        dec = len(str(pf["tickSize"]).split('.')[-1]) if '.' in str(pf["tickSize"]) else 0
        return min_q, max_q, step, tick, dec
    except Exception as e:
        st.error(f"🔍 심볼 정보 조회 실패: {e}")
        return 0.001, 10000, 0.001, 0.01, 2

# ── 시장가 주문 ──
def place_market_order(client, symbol: str, side: str, pct: float, balance: float):
    try:
        current_price = get_current_price(client, symbol)
        if current_price <= 0:
            return False, "현재가 조회 실패"
            
        min_q, max_q, step, tick, dec = get_order_unit(client, symbol)
        
        order_value_usdt = balance * pct / 100
        raw_qty = order_value_usdt / current_price
        qty = max(min_q, floor(min(raw_qty, max_q) / step) * step)
        
        qty_decimals = len(str(step).split('.')[-1]) if '.' in str(step) else 0
        qty = round(qty, qty_decimals)
        
        final_order_value = qty * current_price
        
        if final_order_value < 5.0:
            return False, f"⚠️ 주문 금액이 최소값 미달! 필요: 5 USDT, 계산: {final_order_value:.2f} USDT"
        
        if final_order_value > balance:
            return False, f"⚠️ 잔고 부족! 필요: {final_order_value:.2f} USDT, 잔고: {balance:.2f} USDT"
        
        res = client.place_order(
            category=TRADE_CATEGORY,
            symbol=symbol,
            side=side,
            orderType="Market",
            qty=str(qty),
            timeInForce="IOC",
            reduceOnly=False
        )
        
        if res.get("retCode", 0) == 0:
            return True, f"✅ 시장가 주문 성공: {side} {qty}@${current_price:.4f} = {final_order_value:.2f} USDT"
        else:
            return False, f"❌ 주문 실패: {res.get('retMsg', 'Unknown error')}"
            
    except Exception as e:
        return False, f"❌ 주문 실패: {str(e)}"

# ── 리밋 주문 ──
def place_limit_order(client, symbol: str, side: str, pct: float, price: float, balance: float):
    try:
        min_q, max_q, step, tick, dec = get_order_unit(client, symbol)
        
        order_value_usdt = balance * pct / 100
        raw_qty = order_value_usdt / price
        qty = max(min_q, floor(min(raw_qty, max_q) / step) * step)
        
        qty_decimals = len(str(step).split('.')[-1]) if '.' in str(step) else 0
        qty = round(qty, qty_decimals)
        
        final_order_value = qty * price
        
        if final_order_value < 5.0:
            return False, f"⚠️ 주문 금액이 최소값 미달! 필요: 5 USDT, 계산: {final_order_value:.2f} USDT"
        
        price_adj = round(round(price / tick) * tick, dec)
        
        res = client.place_order(
            category=TRADE_CATEGORY,
            symbol=symbol,
            side=side,
            orderType="Limit",
            qty=str(qty),
            price=str(price_adj),
            timeInForce="GTC",
            reduceOnly=False
        )
        
        if res.get("retCode", 0) == 0:
            return True, f"✅ 리밋 주문 성공: {side} {qty}@${price_adj:.4f} = {final_order_value:.2f} USDT"
        else:
            return False, f"❌ 주문 실패: {res.get('retMsg', 'Unknown error')}"
            
    except Exception as e:
        return False, f"❌ 주문 실패: {str(e)}"

# ── 메인 대시보드 ──
def main():
    # 헤더
    st.markdown('<div class="main-header">🚀 Bybit 자동매매 대시보드</div>', unsafe_allow_html=True)
    
    if not PYBIT_AVAILABLE:
        st.error("🚨 pybit 패키지가 설치되지 않았습니다. requirements.txt를 확인하고 다시 배포해주세요.")
        st.stop()
    
    # 사이드바 - API 설정
    with st.sidebar:
        st.header("🔑 API 설정")
        
        api_key = st.text_input("API Key", type="password", help="Bybit API Key를 입력하세요")
        api_secret = st.text_input("API Secret", type="password", help="Bybit API Secret을 입력하세요")
        testnet = st.checkbox("🧪 테스트넷 사용", value=False, help="실제 거래 전 테스트넷에서 먼저 테스트하세요")
        
        st.divider()
        
        st.header("📱 텔레그램 설정")
        tg_token = st.text_input("텔레그램 Bot Token", type="password", help="@BotFather에서 생성한 토큰")
        tg_chat_id = st.text_input("텔레그램 Chat ID", help="본인의 텔레그램 Chat ID")
        
        if st.button("📱 텔레그램 테스트"):
            if tg_token and tg_chat_id:
                success = send_telegram("🚀 Bybit 대시보드 연결 테스트!", tg_token, tg_chat_id)
                if success:
                    st.success("✅ 텔레그램 전송 성공!")
                else:
                    st.error("❌ 텔레그램 전송 실패!")
            else:
                st.warning("⚠️ 텔레그램 정보를 입력해주세요.")
        
        st.divider()
        
        st.header("⚙️ 거래 설정")
        max_position_pct = st.slider("최대 포지션 비율 (%)", 10, 100, 100, 5, help="전체 잔고 대비 사용할 비율")
        leverage = st.selectbox("레버리지", [1, 2, 5, 10, 12.5, 15, 20, 25], index=4, help="거래 레버리지 설정")
        
        if st.button("💾 설정 저장", type="primary"):
            if api_key and api_secret:
                try:
                    client = HTTP(
                        api_key=api_key,
                        api_secret=api_secret,
                        testnet=testnet
                    )
                    # 연결 테스트
                    test_balance = get_usdt_balance(client)
                    
                    st.session_state.client = client
                    st.session_state.api_key = api_key
                    st.session_state.api_secret = api_secret
                    st.session_state.testnet = testnet
                    st.session_state.tg_token = tg_token
                    st.session_state.tg_chat_id = tg_chat_id
                    st.session_state.max_position_pct = max_position_pct
                    st.session_state.leverage = leverage
                    st.session_state.connected = True
                    
                    st.success(f"✅ API 연결 성공! 잔고: {test_balance:.2f} USDT")
                    st.balloons()
                except Exception as e:
                    st.error(f"❌ API 연결 실패: {e}")
                    st.session_state.connected = False
            else:
                st.warning("⚠️ API Key와 Secret을 입력해주세요.")
    
    # API 연결 확인
    if not st.session_state.get('connected', False):
        st.warning("⚠️ 사이드바에서 API 설정을 완료해주세요.")
        
        # 데모 정보 표시
        st.info("🔥 **데모 모드**: API 연결 후 실제 데이터를 확인할 수 있습니다.")
        
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("💰 USDT 잔고", "--- USDT")
        with col2:
            st.metric("📊 포지션 수", "---")
        with col3:
            st.metric("📋 미체결 주문", "---")
        with col4:
            st.metric("💹 총 손익", "--- USDT")
        
        st.markdown("### 🎯 **시작하기**")
        st.markdown("""
        1. **사이드바**에서 Bybit API Key와 Secret 입력
        2. **텔레그램** 설정 (선택사항)
        3. **"설정 저장"** 버튼 클릭
        4. **대시보드 사용 시작!** 🚀
        """)
        
        return
    
    client = st.session_state.client
    
    # 실시간 정보 업데이트
    col_refresh1, col_refresh2 = st.columns([1, 4])
    with col_refresh1:
        if st.button("🔄 새로고침", type="secondary"):
            st.session_state.last_update = time.time()
            st.rerun()
    
    with col_refresh2:
        if st.session_state.get('last_update'):
            last_update_time = datetime.fromtimestamp(st.session_state.last_update).strftime("%H:%M:%S")
            st.caption(f"마지막 업데이트: {last_update_time}")
    
    if st.session_state.last_update is None or (time.time() - st.session_state.last_update) > 30:
        with st.spinner("🔄 데이터 업데이트 중..."):
            st.session_state.last_update = time.time()
            
            # 잔고 조회
            balance = get_usdt_balance(client)
            st.session_state.balance = balance
            
            # 포지션 조회
            positions = get_positions(client)
            st.session_state.positions = positions
            
            # 미체결 주문 조회
            open_orders = get_open_orders(client)
            st.session_state.open_orders = open_orders
    
    # 상단 메트릭
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        balance = st.session_state.get('balance', 0)
        st.metric("💰 USDT 잔고", f"{balance:.2f} USDT")
    
    with col2:
        total_positions = len(st.session_state.get('positions', []))
        st.metric("📊 포지션 수", total_positions)
    
    with col3:
        total_orders = len(st.session_state.get('open_orders', []))
        st.metric("📋 미체결 주문", total_orders)
    
    with col4:
        # 총 손익 계산
        positions = st.session_state.get('positions', [])
        total_pnl = 0
        for pos in positions:
            try:
                pnl_str = pos.get('손익(USDT)', '0').replace(' USDT', '')
                total_pnl += float(pnl_str)
            except:
                pass
        
        delta_color = "normal"
        if total_pnl > 0:
            delta_color = "normal"
        elif total_pnl < 0:
            delta_color = "inverse"
            
        st.metric("💹 총 손익", f"{total_pnl:.2f} USDT", delta=f"{total_pnl:.2f}")
    
    # 탭 구성
    tab1, tab2, tab3, tab4 = st.tabs(["📊 포지션 관리", "🚀 수동 매매", "📋 주문 관리", "⚙️ 도구"])
    
    with tab1:
        st.header("📊 현재 포지션")
        
        if st.session_state.get('positions'):
            df_positions = pd.DataFrame(st.session_state['positions'])
            
            # 데이터프레임 스타일링
            def highlight_pnl(val):
                if isinstance(val, str) and '%' in val:
                    try:
                        num = float(val.replace('%', ''))
                        if num > 0:
                            return 'background-color: #d4edda; color: #155724'
                        elif num < 0:
                            return 'background-color: #f8d7da; color: #721c24'
                    except:
                        pass
                return ''
            
            styled_df = df_positions.style.applymap(highlight_pnl, subset=['손익(%)'])
            st.dataframe(styled_df, use_container_width=True, hide_index=True)
            
        else:
            st.info("📭 현재 보유 중인 포지션이 없습니다.")
            st.markdown("**💡 포지션을 시작하려면 '수동 매매' 탭을 이용하세요!**")
    
    with tab2:
        st.header("🚀 수동 매매")
        
        col_trade1, col_trade2 = st.columns(2)
        
        with col_trade1:
            st.subheader("📈 진입")
            symbol_entry = st.text_input("🎯 거래 심볼", value="BTCUSDT", key="entry_symbol").upper()
            
            # 현재가 자동 조회
            if symbol_entry and st.session_state.get('connected'):
                try:
                    current_price = get_current_price(client, symbol_entry)
                    st.info(f"💹 현재가: ${current_price:.4f}")
                except:
                    current_price = 0
                    st.warning("⚠️ 현재가 조회 실패")
            else:
                current_price = 0
            
            price_entry = st.number_input(
                "💰 진입 가격", 
                value=float(current_price) if current_price > 0 else 0.0, 
                format="%.6f", 
                key="entry_price",
                help="분할 진입의 기준 가격"
            )
            
            st.markdown("---")
            
            if st.button("🟢 **롱 진입 (L)**", type="primary", use_container_width=True):
                if symbol_entry and price_entry > 0:
                    balance = st.session_state.get('balance', 190)
                    max_pct = st.session_state.get('max_position_pct', 100)
                    
                    with st.status("🚀 롱 포지션 진입 중...", expanded=True) as status:
                        # 1차 진입 (45% 시장가)
                        st.write("📈 1차 진입 (45% 시장가)...")
                        success, msg = place_market_order(client, symbol_entry, "Buy", max_pct * 0.45, balance)
                        st.write(msg)
                        
                        # 2-4차 진입 (리밋)
                        tier_pcts = [(0.02, max_pct * 0.20), (0.03, max_pct * 0.20), (0.04, max_pct * 0.15)]
                        for i, (off, pct) in enumerate(tier_pcts):
                            limit_price = price_entry * (1 - off)
                            st.write(f"📊 {i+2}차 진입 ({pct:.1f}% @ ${limit_price:.4f})...")
                            success, msg = place_limit_order(client, symbol_entry, "Buy", pct, limit_price, balance)
                            st.write(msg)
                        
                        status.update(label="✅ 롱 포지션 진입 완료!", state="complete")
                        
                    # 텔레그램 알림
                    if st.session_state.get('tg_token'):
                        send_telegram(f"🟢 [{symbol_entry}] 롱 포지션 진입 완료!", 
                                    st.session_state['tg_token'], st.session_state['tg_chat_id'])
            
            if st.button("🔴 **숏 진입 (S)**", type="secondary", use_container_width=True):
                if symbol_entry and price_entry > 0:
                    balance = st.session_state.get('balance', 190)
                    max_pct = st.session_state.get('max_position_pct', 100)
                    
                    with st.status("🚀 숏 포지션 진입 중...", expanded=True) as status:
                        # 1차 진입 (45% 시장가)
                        st.write("📉 1차 진입 (45% 시장가)...")
                        success, msg = place_market_order(client, symbol_entry, "Sell", max_pct * 0.45, balance)
                        st.write(msg)
                        
                        # 2-4차 진입 (리밋)
                        tier_pcts = [(0.02, max_pct * 0.20), (0.03, max_pct * 0.20), (0.04, max_pct * 0.15)]
                        for i, (off, pct) in enumerate(tier_pcts):
                            limit_price = price_entry * (1 + off)
                            st.write(f"📊 {i+2}차 진입 ({pct:.1f}% @ ${limit_price:.4f})...")
                            success, msg = place_limit_order(client, symbol_entry, "Sell", pct, limit_price, balance)
                            st.write(msg)
                        
                        status.update(label="✅ 숏 포지션 진입 완료!", state="complete")
                    
                    # 텔레그램 알림
                    if st.session_state.get('tg_token'):
                        send_telegram(f"🔴 [{symbol_entry}] 숏 포지션 진입 완료!", 
                                    st.session_state['tg_token'], st.session_state['tg_chat_id'])
        
        with col_trade2:
            st.subheader("🚪 청산")
            symbol_exit = st.text_input("🎯 청산할 심볼", value="BTCUSDT", key="exit_symbol").upper()
            
            st.markdown("---")
            
            if st.button("📤 **롱 청산 (LT)**", type="primary", use_container_width=True):
                if symbol_exit:
                    with st.status("📤 롱 포지션 청산 중...", expanded=True) as status:
                        # 미체결 주문 취소
                        st.write("❌ 미체결 주문 취소 중...")
                        cancel_success = cancel_all_orders(client, symbol_exit)
                        st.write("✅ 미체결 주문 취소 완료" if cancel_success else "⚠️ 미체결 주문 취소 실패")
                        
                        # 포지션 조회 및 청산
                        st.write("📊 포지션 조회 중...")
                        positions = get_positions(client, symbol_exit)
                        
                        closed_any = False
                        for pos in positions:
                            if '롱' in pos.get('방향', ''):
                                st.write(f"📤 롱 포지션 청산: {pos['수량']}")
                                # 시장가로 즉시 청산
                                try:
                                    size = float(pos['수량'])
                                    price = float(pos['현재가'].replace('$', ''))
                                    success, msg = place_market_order(client, symbol_exit, "Sell", 100, size * price)
                                    st.write(msg)
                                    closed_any = True
                                except Exception as e:
                                    st.write(f"❌ 청산 실패: {e}")
                        
                        if not closed_any:
                            st.write("📭 청산할 롱 포지션이 없습니다.")
                        
                        status.update(label="✅ 롱 포지션 청산 완료!", state="complete")
                    
                    # 텔레그램 알림
                    if st.session_state.get('tg_token'):
                        send_telegram(f"📤 [{symbol_exit}] 롱 포지션 청산 완료!", 
                                    st.session_state['tg_token'], st.session_state['tg_chat_id'])
            
            if st.button("📤 **숏 청산 (ST)**", type="secondary", use_container_width=True):
                if symbol_exit:
                    with st.status("📤 숏 포지션 청산 중...", expanded=True) as status:
                        # 미체결 주문 취소
                        st.write("❌ 미체결 주문 취소 중...")
                        cancel_success = cancel_all_orders(client, symbol_exit)
                        st.write("✅ 미체결 주문 취소 완료" if cancel_success else "⚠️ 미체결 주문 취소 실패")
                        
                        # 포지션 조회 및 청산
                        st.write("📊 포지션 조회 중...")
                        positions = get_positions(client, symbol_exit)
                        
                        closed_any = False
                        for pos in positions:
                            if '숏' in pos.get('방향', ''):
                                st.write(f"📤 숏 포지션 청산: {pos['수량']}")
                                # 시장가로 즉시 청산
                                try:
                                    size = float(pos['수량'])
                                    price = float(pos['현재가'].replace('$', ''))
                                    success, msg = place_market_order(client, symbol_exit, "Buy", 100, size * price)
                                    st.write(msg)
                                    closed_any = True
                                except Exception as e:
                                    st.write(f"❌ 청산 실패: {e}")
                        
                        if not closed_any:
                            st.write("📭 청산할 숏 포지션이 없습니다.")
                        
                        status.update(label="✅ 숏 포지션 청산 완료!", state="complete")
                    
                    # 텔레그램 알림
                    if st.session_state.get('tg_token'):
                        send_telegram(f"📤 [{symbol_exit}] 숏 포지션 청산 완료!", 
                                    st.session_state['tg_token'], st.session_state['tg_chat_id'])
    
    with tab3:
        st.header("📋 미체결 주문 관리")
        
        if st.session_state.get('open_orders'):
            df_orders = pd.DataFrame(st.session_state['open_orders'])
            st.dataframe(df_orders, use_container_width=True, hide_index=True)
            
            st.markdown("---")
            
            # 주문 취소 섹션
            col_cancel1, col_cancel2 = st.columns(2)
            
            with col_cancel1:
                cancel_symbol = st.text_input("🎯 취소할 심볼", placeholder="예: BTCUSDT (전체: ALL)")
            
            with col_cancel2:
                st.write("")  # 공간 맞춤
                if st.button("❌ **주문 취소**", type="secondary", use_container_width=True):
                    if cancel_symbol == "ALL":
                        with st.spinner("❌ 모든 미체결 주문 취소 중..."):
                            symbols = set([order['심볼'] for order in st.session_state['open_orders']])
                            for symbol in symbols:
                                cancel_all_orders(client, symbol)
                            st.success("✅ 모든 미체결 주문을 취소했습니다.")
                            st.rerun()
                    elif cancel_symbol:
                        with st.spinner(f"❌ {cancel_symbol} 주문 취소 중..."):
                            cancel_all_orders(client, cancel_symbol.upper())
                            st.success(f"✅ {cancel_symbol} 미체결 주문을 취소했습니다.")
                            st.rerun()
                    else:
                        st.warning("⚠️ 취소할 심볼을 입력하거나 'ALL'을 입력하세요.")
        else:
            st.info("📭 현재 미체결 주문이 없습니다.")
    
    with tab4:
        st.header("⚙️ 도구 및 테스트")
        
        col_tool1, col_tool2 = st.columns(2)
        
        with col_tool1:
            st.subheader("🧪 연결 테스트")
            
            if st.button("🔗 **API 연결 테스트**", use_container_width=True):
                try:
                    with st.spinner("🔍 API 연결 확인 중..."):
                        balance = get_usdt_balance(client)
                        positions = get_positions(client)
                        
                    st.success(f"✅ API 연결 성공!")
                    st.info(f"💰 잔고: {balance:.2f} USDT")
                    st.info(f"📊 포지션: {len(positions)}개")
                    
                except Exception as e:
                    st.error(f"❌ API 연결 실패: {e}")
        
        with col_tool2:
            st.subheader("📱 알림 테스트")
            
            test_message = st.text_input("📝 테스트 메시지", value="🚀 대시보드 테스트 메시지입니다!")
            
            if st.button("📤 **텔레그램 전송**", use_container_width=True):
                if st.session_state.get('tg_token') and st.session_state.get('tg_chat_id'):
                    with st.spinner("📱 텔레그램 전송 중..."):
                        success = send_telegram(test_message, st.session_state['tg_token'], st.session_state['tg_chat_id'])
                    
                    if success:
                        st.success("✅ 텔레그램 전송 성공!")
                    else:
                        st.error("❌ 텔레그램 전송 실패!")
                else:
                    st.warning("⚠️ 텔레그램 설정이 필요합니다.")
        
        st.markdown("---")
        
        # 시스템 정보
        st.subheader("📊 시스템 정보")
        
        col_info1, col_info2, col_info3 = st.columns(3)
        
        with col_info1:
            st.metric("🌐 네트워크", "테스트넷" if st.session_state.get('testnet') else "메인넷")
        
        with col_info2:
            st.metric("⚡ 레버리지", f"{st.session_state.get('leverage', 12.5)}x")
        
        with col_info3:
            st.metric("📈 포지션 비율", f"{st.session_state.get('max_position_pct', 100)}%")
    
    # 자동 새로고침 옵션
    st.markdown("---")
    col_auto1, col_auto2 = st.columns([1, 3])
    
    with col_auto1:
        auto_refresh = st.checkbox("🔄 자동 새로고침 (30초)", value=False)
    
    with col_auto2:
        if auto_refresh:
            st.caption("⏰ 30초마다 자동으로 데이터를 업데이트합니다.")
    
    if auto_refresh:
        time.sleep(30)
        st.rerun()

if __name__ == "__main__":
    main()
