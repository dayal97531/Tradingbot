main.py
# STEP 1: 
BOT_TOKEN = "YOUR_NEW_TOKEN_HERE"   # ← paste your new token from BotFather
CHAT_ID = "7210100979"              # ← already filled in for you 

# STEP 2: Press the Play button and your bot will start!
# ============================================================

# Install packages
import subprocess
subprocess.run(["pip", "install", "python-telegram-bot==20.7", "requests", "yfinance", "-q"])

import asyncio
import logging
import json
import os
import random
import time
import threading
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── SETTINGS ─────────────────────────────────────────────
PAPER_TRADING    = True
STARTING_BALANCE = 1000.0
BUY_AMOUNT_USD   = 50.0
SELL_TARGET      = 2.0    # 2x = 100% profit
LOSS_ALERT_PCT   = -0.15  # alert at -15%
CHECK_EVERY      = 30     # seconds

# ─── COIN LIST ────────────────────────────────────────────
COINGECKO_IDS = {
    "BTC":"bitcoin","ETH":"ethereum","SOL":"solana",
    "DOGE":"dogecoin","SHIB":"shiba-inu","PEPE":"pepe",
    "FLOKI":"floki","BONK":"bonk","WIF":"dogwifcoin",
    "BRETT":"based-brett","MOG":"mog-coin","AVAX":"avalanche-2",
    "LINK":"chainlink","ARB":"arbitrum","XRP":"ripple",
}
WATCHLIST = list(COINGECKO_IDS.keys())

# ─── TRADE DATACLASS ──────────────────────────────────────
@dataclass
class Trade:
    symbol: str
    buy_price: float
    quantity: float
    cost: float
    buy_time: str = field(default_factory=lambda: datetime.now().isoformat())
    sell_price: Optional[float] = None
    pnl: float = 0.0
    pnl_pct: float = 0.0
    close_reason: str = ""
    alert_sent: bool = False
    is_open: bool = True

# ─── PORTFOLIO ────────────────────────────────────────────
class Portfolio:
    def __init__(self):
        self.balance = STARTING_BALANCE
        self._trades: List[Trade] = []

    def open_trade(self, symbol, price, quantity, cost):
        t = Trade(symbol=symbol, buy_price=price, quantity=quantity, cost=cost)
        self._trades.append(t)
        self.balance -= cost
        return t

    def close_trade(self, symbol, sell_price, reason=""):
        t = self.get_position(symbol)
        if not t: return 0, 0
        revenue = sell_price * t.quantity
        t.pnl = revenue - t.cost
        t.pnl_pct = (t.pnl / t.cost) * 100
        t.sell_price = sell_price
        t.close_reason = reason
        t.is_open = False
        self.balance += revenue
        return t.pnl, t.pnl_pct

    def has_position(self, symbol): return any(t.symbol==symbol and t.is_open for t in self._trades)
    def get_position(self, symbol): return next((t for t in self._trades if t.symbol==symbol and t.is_open), None)
    def get_open_trades(self): return [t for t in self._trades if t.is_open]
    def get_closed_trades(self): return [t for t in self._trades if not t.is_open]

    def get_stats(self):
        closed = self.get_closed_trades()
        wins = [t for t in closed if t.pnl > 0]
        return {
            "wins": len(wins), "losses": len(closed)-len(wins),
            "win_rate": (len(wins)/len(closed)*100) if closed else 0,
            "realized_pnl": sum(t.pnl for t in closed),
        }

    def total_value(self, prices):
        total = self.balance
        for t in self.get_open_trades():
            if t.symbol in prices:
                total += prices[t.symbol] * t.quantity
        return total

# ─── PRICE FETCHER ────────────────────────────────────────
_price_cache = {}
_cache_time = {}

def get_prices_bulk(symbols):
    ids = [COINGECKO_IDS[s] for s in symbols if s in COINGECKO_IDS]
    if not ids: return {}
    try:
        import requests
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": ",".join(ids), "vs_currencies": "usd", "include_24hr_change": "true"},
            timeout=10
        )
        data = r.json()
        result = {}
        for sym in symbols:
            cid = COINGECKO_IDS.get(sym)
            if cid and cid in data:
                result[sym] = {
                    "price": data[cid].get("usd", 0),
                    "change_24h": data[cid].get("usd_24h_change", 0),
                }
        return result
    except Exception as e:
        logger.warning(f"Price fetch failed: {e}")
        # Demo fallback
        base = {"BTC":95000,"ETH":3400,"SOL":180,"DOGE":0.38,"SHIB":0.000025,
                "PEPE":0.000018,"FLOKI":0.00018,"BONK":0.000035,"WIF":3.2,
                "BRETT":0.18,"MOG":0.0001,"AVAX":40,"LINK":15,"ARB":1.2,"XRP":0.6}
        return {s: {"price": base.get(s,1)*(1+random.uniform(-0.03,0.03)), "change_24h": random.uniform(-10,10)} for s in symbols}

def get_price(symbol):
    data = get_prices_bulk([symbol])
    return data.get(symbol, {}).get("price")

# ─── GLOBALS ──────────────────────────────────────────────
portfolio = Portfolio()

# ─── HELPERS ──────────────────────────────────────────────
async def do_buy(chat_id, symbol, context):
    price = get_price(symbol)
    if not price:
        await context.bot.send_message(chat_id, f" Can't get price for {symbol}")
        return
    if portfolio.balance < BUY_AMOUNT_USD:
        await context.bot.send_message(chat_id, f" Not enough balance! ${portfolio.balance:.2f} left")
        return
    if portfolio.has_position(symbol):
        await context.bot.send_message(chat_id, f" Already holding {symbol}")
        return
    qty = BUY_AMOUNT_USD / price
    portfolio.open_trade(symbol, price, qty, BUY_AMOUNT_USD)
    await context.bot.send_message(
        chat_id,
        f" *BOUGHT {symbol}* PAPER\n"
        f"━━━━━━━━━━━━━━━\n"
        f" Spent: ${BUY_AMOUNT_USD:.2f}\n"
        f" Price: ${price:.5f}\n"
        f" Sell at: ${price*SELL_TARGET:.5f} (+100%)\n"
        f" Alert if: ${price*(1+LOSS_ALERT_PCT):.5f} (-15%)",
        parse_mode="Markdown"
    )

async def do_sell(chat_id, symbol, context, reason="Manual"):
    trade = portfolio.get_position(symbol)
    if not trade:
        await context.bot.send_message(chat_id, f" No position in {symbol}")
        return
    price = get_price(symbol)
    if not price:
        await context.bot.send_message(chat_id, f" Can't get price for {symbol}")
        return
    pnl, pnl_pct = portfolio.close_trade(symbol, price, reason)
    emoji = "" if pnl >= 0 else ""
    await context.bot.send_message(
        chat_id,
        f"{emoji} *SOLD {symbol}*\n"
        f"━━━━━━━━━━━━━━━\n"
        f" Sold at: ${price:.5f}\n"
        f"P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%)\n"
        f" Reason: {reason}\n"
        f"Balance: ${portfolio.balance:,.2f}",
        parse_mode="Markdown"
    )

# ─── COMMAND HANDLERS ─────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *DAYAL'S DEGEN BOT* — Paper Mode\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        " Balance: $1,000 (fake money)\n"
        " Auto-sells at +100% profit\n"
        " Alerts you at -15% loss\n\n"
        "*Commands:*\n"
        "/status — Portfolio\n"
        "/trades — Open positions\n"
        "/buy DOGE — Buy a coin\n"
        "/sell DOGE — Sell a coin\n"
        "/scan — Find opportunities\n"
        "/history — Past trades",
        parse_mode="Markdown"
    )

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prices = get_prices_bulk(WATCHLIST)
    price_map = {s: prices[s]['price'] for s in prices}
    total = portfolio.total_value(price_map)
    pnl = total - STARTING_BALANCE
    pnl_pct = (pnl / STARTING_BALANCE) * 100
    stats = portfolio.get_stats()
    emoji = "" if pnl >= 0 else ""
    await update.message.reply_text(
        f"{emoji} *PORTFOLIO* —  PAPER\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f" Cash: ${portfolio.balance:,.2f}\n"
        f" Total: ${total:,.2f}\n"
        f" P&L: ${pnl:+,.2f} ({pnl_pct:+.1f}%)\n\n"
        f" Wins: {stats['wins']} |  Losses: {stats['losses']}\n"
        f" Win Rate: {stats['win_rate']:.0f}%\n"
        f" Realized: ${stats['realized_pnl']:+,.2f}",
        parse_mode="Markdown"
    )

async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    open_trades = portfolio.get_open_trades()
    if not open_trades:
        await update.message.reply_text(" No open positions.")
        return
    prices = get_prices_bulk([t.symbol for t in open_trades])
    text = " *OPEN POSITIONS*\n━━━━━━━━━━━━━━━\n"
    for t in open_trades:
        p = prices.get(t.symbol, {}).get('price', 0)
        if p:
            pct = ((p - t.buy_price) / t.buy_price) * 100
            usd = (p - t.buy_price) * t.quantity
            e = "" if pct >= 0 else ""
            text += f"\n{e} *{t.symbol}*\n   ${t.buy_price:.5f} → ${p:.5f}\n   P&L: ${usd:+.2f} ({pct:+.1f}%)\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /buy DOGE")
        return
    await do_buy(update.effective_chat.id, context.args[0].upper(), context)

async def cmd_sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /sell DOGE")
        return
    await do_sell(update.effective_chat.id, context.args[0].upper(), context)

async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(" Scanning...")
    prices = get_prices_bulk(WATCHLIST)
    opps = []
    for sym, data in prices.items():
        if portfolio.has_position(sym): continue
        chg = data.get('change_24h', 0)
        score = 5
        if -15 <= chg <= -3: score += 3
        if chg > 0: score += 1
        if score >= 7:
            opps.append((sym, data['price'], chg, score))
    opps.sort(key=lambda x: x[3], reverse=True)
    if not opps:
        await update.message.reply_text(" No strong signals right now.")
        return
    text =  *OPPORTUNITIES*\n━━━━━━━━━━━━━━━\n"
    keyboard = []
    for sym, price, chg, score in opps[:5]:
        text += f"\n *{sym}* — {chg:+.1f}% | Score {score}/10\n   Price: ${price:.5f}\n"
        keyboard.append([InlineKeyboardButton(f" Buy {sym}", callback_data=f"buy_{sym}")])
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    closed = portfolio.get_closed_trades()[-10:]
    if not closed:
        await update.message.reply_text(" No completed trades yet.")
        return
    text = " *TRADE HISTORY*\n━━━━━━━━━━━━━━━\n"
    for t in reversed(closed):
        e = "" if t.pnl >= 0 else ""
        text += f"{e} *{t.symbol}*: ${t.pnl:+.2f} ({t.pnl_pct:+.1f}%) — {t.close_reason}\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id
    if data.startswith("buy_"):
        sym = data[4:]
        await do_buy(chat_id, sym, context)
        await query.edit_message_text(f" Buying {sym}!")
    elif data.startswith("sell_"):
        sym = data[5:]
        await do_sell(chat_id, sym, context, "Sold via button")
        await query.edit_message_text(f" Sold {sym}!")
    elif data.startswith("keep_"):
        sym = data[5:]
        await query.edit_message_text(f" Keeping {sym} — HODL mode on!")

# ─── AUTO TRADING LOOP ────────────────────────────────────
async def auto_trade_loop(app):
    await asyncio.sleep(15)
    while True:
        try:
            open_trades = portfolio.get_open_trades()
            all_syms = list(set(WATCHLIST + [t.symbol for t in open_trades]))
            prices = get_prices_bulk(all_syms)

            for trade in open_trades:
                p = prices.get(trade.symbol, {}).get('price')
                if not p: continue
                pnl_pct = (p - trade.buy_price) / trade.buy_price

                # Auto sell at 2x
                if pnl_pct >= (SELL_TARGET - 1):
                    pnl, pct = portfolio.close_trade(trade.symbol, p, " Target +100%!")
                    await app.bot.send_message(
                        CHAT_ID,
                        f" *AUTO-SOLD {trade.symbol}!*\n"
                        f" Profit: ${pnl:+.2f} (+{pct:.0f}%)\n"
                        f" Balance: ${portfolio.balance:,.2f}",
                        parse_mode="Markdown"
                    )

                # Loss alert
                elif pnl_pct <= LOSS_ALERT_PCT and not trade.alert_sent:
                    trade.alert_sent = True
                    pnl_usd = (p - trade.buy_price) * trade.quantity
                    kb = [[
                        InlineKeyboardButton(" KEEP (HODL)", callback_data=f"keep_{trade.symbol}"),
                        InlineKeyboardButton(" SELL NOW", callback_data=f"sell_{trade.symbol}"),
                    ]]
                    await app.bot.send_message(
                        CHAT_ID,
                        f" *LOSS ALERT — {trade.symbol}*\n"
                        f"━━━━━━━━━━━━━━━━━\n"
                        f" Down {pnl_pct*100:.1f}% (${pnl_usd:+.2f})\n"
                        f"Bought at: ${trade.buy_price:.5f}\n"
                        f" Now: ${p:.5f}\n\n"
                        f"What do you want to do?",
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup(kb)
                    )

            # Auto buy if cash available and < 5 positions
            if portfolio.balance >= BUY_AMOUNT_USD and len(open_trades) < 5:
                for sym, data in prices.items():
                    if portfolio.has_position(sym): continue
                    chg = data.get('change_24h', 0)
                    if -15 <= chg <= -5:  # nice dip
                        score = 8
                        if score >= 8:
                            await app.bot.send_message(CHAT_ID, f" Auto-buying *{sym}* — dip of {chg:.1f}%!", parse_mode="Markdown")
                            price = data['price']
                            qty = BUY_AMOUNT_USD / price
                            portfolio.open_trade(sym, price, qty, BUY_AMOUNT_USD)
                            await app.bot.send_message(
                                CHAT_ID,
                                f" *BOUGHT {sym}*\n ${BUY_AMOUNT_USD} @ ${price:.5f}\n Target: ${price*SELL_TARGET:.5f}",
                                parse_mode="Markdown"
                            )
                            break  # one buy per cycle

        except Exception as e:
            logger.error(f"Auto trade loop error: {e}")

        await asyncio.sleep(CHECK_EVERY)

# ─── MAIN ─────────────────────────────────────────────────
async def main():
    if BOT_TOKEN == "YOUR_NEW_TOKEN_HERE":
        print(" You forgot to paste your bot token at the top!")
        print("   Edit BOT_TOKEN = '...' at line 10")
        return

    print(" Starting Dayal's Degen Bot...")
    print(f"Paper trading — ${STARTING_BALANCE} balance")
    print(f"Auto-sell at +100% | Alert at -15%")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("trades", cmd_trades))
    app.add_handler(CommandHandler("buy", cmd_buy))
    app.add_handler(CommandHandler("sell", cmd_sell))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CallbackQueryHandler(button_callback))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    print("Bot is LIVE! Open Telegram and message your bot /start")

    # Start auto trading loop
    await auto_trade_loop(app)

asyncio.run(main())
