"""
Bybit 펀딩비 텔레그램 봇 - Railway 배포용
"""
import os
import time
import json
import hmac
import hashlib
import logging
import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 환경변수
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
BYBIT_API_KEY = os.environ.get("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.environ.get("BYBIT_API_SECRET", "")


# ============ Bybit API ============

def bybit_public(endpoint, params=None):
    """Bybit 공개 API"""
    url = f"https://api.bybit.com{endpoint}"
    response = requests.get(url, params=params, timeout=10)
    logger.info(f"Bybit status: {response.status_code}")
    if response.status_code != 200:
        raise Exception(f"HTTP {response.status_code}: {response.text[:200]}")
    data = response.json()
    if data.get("retCode") != 0:
        raise Exception(data.get("retMsg"))
    return data.get("result", {})


def bybit_private(endpoint, params):
    """Bybit 비공개 API"""
    if not BYBIT_API_KEY or not BYBIT_API_SECRET:
        raise Exception("API 키 미설정")

    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    param_str = "&".join(f"{k}={v}" for k, v in sorted(params.items()))

    sign_str = f"{timestamp}{BYBIT_API_KEY}{recv_window}{param_str}"
    signature = hmac.new(
        BYBIT_API_SECRET.encode(), sign_str.encode(), hashlib.sha256
    ).hexdigest()

    headers = {
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-SIGN": signature,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window
    }

    url = f"https://api.bybit.com{endpoint}"
    response = requests.get(url, params=params, headers=headers, timeout=10)
    data = response.json()
    if data.get("retCode") != 0:
        raise Exception(data.get("retMsg"))
    return data.get("result", {})


def get_funding_rates(limit=50):
    """펀딩비 조회"""
    result = bybit_public("/v5/market/tickers", {"category": "linear"})
    tickers = result.get("list", [])

    funding_list = []
    for ticker in tickers:
        rate = ticker.get("fundingRate")
        if rate:
            r = float(rate)
            funding_list.append({
                "symbol": ticker.get("symbol", ""),
                "rate": r,
                "rate_pct": r * 100,
                "abs_rate": abs(r)
            })

    funding_list.sort(key=lambda x: x["abs_rate"], reverse=True)
    return funding_list[:limit]


# ============ Telegram ============

def send_message(chat_id, text):
    """텔레그램 메시지 전송"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }, timeout=10)
    except Exception as e:
        logger.error(f"Telegram error: {e}")


def get_updates(offset=0):
    """텔레그램 업데이트 가져오기"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    try:
        response = requests.get(url, params={
            "offset": offset,
            "timeout": 30
        }, timeout=40)
        data = response.json()
        return data.get("result", [])
    except:
        return []


# ============ Commands ============

def cmd_help(chat_id):
    send_message(chat_id, """<b>Bybit 펀딩비 봇</b>

/funding [N] - 펀딩비 상위 N개 (기본 20)
/f [N] - /funding 단축어
/top [N] - 양수 펀딩비 (롱 과열)
/bottom [N] - 음수 펀딩비 (숏 과열)
/portfolio - 포트폴리오 조회
/p - /portfolio 단축어
/help - 도움말""")


def cmd_funding(chat_id, args):
    try:
        limit = int(args) if args.isdigit() else 20
        limit = min(limit, 50)

        funding = get_funding_rates(limit)
        lines = [f"<b>펀딩비 상위 {limit}개</b>\n"]

        for i, f in enumerate(funding, 1):
            r = f["rate_pct"]
            sign = "+" if r > 0 else ""
            emoji = "🔴" if r < 0 else "🟢"
            lines.append(f"{i}. {emoji} <code>{f['symbol']:<12}</code> {sign}{r:.4f}%")

        pos = sum(1 for f in funding if f["rate"] > 0)
        neg = limit - pos
        lines.append(f"\n🟢 롱과열: {pos}개 | 🔴 숏과열: {neg}개")

        send_message(chat_id, "\n".join(lines))
    except Exception as e:
        send_message(chat_id, f"오류: {e}")


def cmd_top_bottom(chat_id, args, positive):
    try:
        limit = int(args) if args.isdigit() else 10
        limit = min(limit, 30)

        funding = get_funding_rates(200)

        if positive:
            filtered = [f for f in funding if f["rate"] > 0]
            title = f"🟢 <b>양수 펀딩비 상위 {limit}개</b>"
        else:
            filtered = [f for f in funding if f["rate"] < 0]
            title = f"🔴 <b>음수 펀딩비 상위 {limit}개</b>"

        lines = [title + "\n"]
        for i, f in enumerate(filtered[:limit], 1):
            r = f["rate_pct"]
            sign = "+" if r > 0 else ""
            lines.append(f"{i}. <code>{f['symbol']:<12}</code> {sign}{r:.4f}%")

        send_message(chat_id, "\n".join(lines))
    except Exception as e:
        send_message(chat_id, f"오류: {e}")


def cmd_portfolio(chat_id):
    try:
        wallet = bybit_private("/v5/account/wallet-balance", {"accountType": "UNIFIED"})
        positions = bybit_private("/v5/position/list", {"category": "linear"}).get("list", [])

        lines = ["<b>📊 포트폴리오</b>\n"]

        if wallet.get("list"):
            for coin in wallet["list"][0].get("coin", []):
                if coin.get("coin") == "USDT":
                    equity = float(coin.get("equity", 0))
                    avail = float(coin.get("availableToWithdraw", 0))
                    lines.append(f"💵 총자산: {equity:.2f} USDT")
                    lines.append(f"💵 가용: {avail:.2f} USDT\n")
                    break

        active = [p for p in positions if float(p.get("size", 0)) > 0]
        if active:
            lines.append(f"<b>포지션 ({len(active)}개)</b>")
            for pos in active:
                symbol = pos.get("symbol", "")
                side = "🟢L" if pos.get("side") == "Buy" else "🔴S"
                pnl = float(pos.get("unrealisedPnl", 0))
                lev = pos.get("leverage", "1")
                sign = "+" if pnl >= 0 else ""
                lines.append(f"<code>{symbol}</code> {side} x{lev} | {sign}{pnl:.2f}")
        else:
            lines.append("포지션 없음")

        send_message(chat_id, "\n".join(lines))
    except Exception as e:
        send_message(chat_id, f"오류: {e}")


def handle_message(message):
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "")

    if not chat_id or not text:
        return

    parts = text.split(maxsplit=1)
    cmd = parts[0].lower().split("@")[0]
    args = parts[1] if len(parts) > 1 else ""

    if cmd in ["/start", "/help"]:
        cmd_help(chat_id)
    elif cmd in ["/funding", "/f"]:
        cmd_funding(chat_id, args)
    elif cmd == "/top":
        cmd_top_bottom(chat_id, args, True)
    elif cmd == "/bottom":
        cmd_top_bottom(chat_id, args, False)
    elif cmd in ["/portfolio", "/p"]:
        cmd_portfolio(chat_id)


# ============ Main ============

def main():
    if not TELEGRAM_BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN 환경변수 필요")
        return

    logger.info("봇 시작...")
    offset = 0

    while True:
        try:
            updates = get_updates(offset)
            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                if msg:
                    logger.info(f"메시지: {msg.get('text', '')}")
                    handle_message(msg)
        except KeyboardInterrupt:
            logger.info("봇 종료")
            break
        except Exception as e:
            logger.error(f"오류: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
