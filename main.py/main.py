main.pyimport asyncio
import logging
import random
import os
import subprocess
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, List

subprocess.run(["pip", "install", "python-telegram-bot==20.7", "requests", "-q"])

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_TOKEN_HERE")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7210100979")

STARTING_BALANCE = 1000.0
BUY_AMOUNT_USD = 50.0
SELL_TARGET = 2.0
LOSS_ALERT_PCT = -0.15
CHECK_EVERY = 30

COINGECKO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "DOGE": "dogecoin",
    "SHIB": "shiba-inu",
    "PEPE": "pepe",
    "FLOKI": "floki",
    "BONK": "bonk",
    "WIF": "dogwifcoin",
    "AVAX": "avalanche-2",
    "LINK": "chainlink",
    "ARB": "arbitrum",
    "XRP": "ripple",
}
WATCHLIST = list(COINGECKO_IDS.keys())


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
        if not t:
            return 0, 0
        revenue = sell_price * t.quantity
        t.pnl = revenue - t.cost
        t.pnl_pct = (t.pnl / t.cost) * 100
        t.sell_price = sell_price
        t.close_reason = reason
        t.is_open = False
        self.balance += revenue
        return t.pnl, t.pnl_pct

    def has_position(self, symbol):
        return any(t.symbol == symbol and t.is_open for t in self._trades)

    def get_position(self, symbol):
        return next((t for t in self._trades if t.symbol == symbol and t.is_open), None)

    def get_open_trades(self):
        return [t for t in self._trades if t.is_open]

    def get_closed_trades(self):
        return [t for t in self._trades if not t.is_open]

    def get_stats(self):
        closed = self.get_closed_trades()
        wins = [t for t in closed if t.pnl > 0]
        return {
            "wins": len(wins),
            "losses": len(closed) - len(wins),
            "win_rate": (len(wins) / len(closed) * 100) if closed else 0,
            "realized_pnl": sum(t.pnl for t in closed),
        }

    def total_value(self, prices):
        total = self.balance
        for t in self.get_open_trades():
            if t.symbol in prices:
                total += prices[t.symbol] * t.quantity
        return total


portfolio = Portfolio()


def get_prices_bulk(symbols):
    import requests
    ids = [COINGECKO_IDS[s] for s in symbols if s in COINGECKO_IDS]
    if not ids:
        return {}
    try:
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
        logger.warning("Price fetch failed: " + str(e))
        base = {
            "BTC": 95000, "ETH": 3400, "SOL": 180, "DOGE": 0.38,
            "SHIB": 0.000025, "PEPE": 0.000018, "FLOKI": 0.00018,
            "BONK": 0.000035, "WIF": 3.2, "AVAX": 40,
            "LINK": 15, "ARB": 1.2, "XRP": 0.6
        }
        return {
            s: {
                "price": base.get(s, 1) * (1 + random.uniform(-0.03, 0.03)),
                "change_24h": random.uniform(-10, 10)
            }
            for s in symbols
        }


def get_price(symbol):
    data = get_prices_bulk([symbol])
    return data.get(symbol, {}).get("price")


async def do_buy(chat_id, symbol, context):
    price = get_price(symbol)
    if not price:
        await context.bot.send_message(chat_id, "Cannot get price for " + symbol)
        return
    if portfolio.balance < BUY_AMOUNT_USD:
        await context.bot.send_message(chat_id, "Not enough balance! $" + str(round(portfolio.balance, 2)) + " left")
        return
    if portfolio.has_position(symbol):
        await context.bot.send_message(chat_id, "Already holding " + symbol)
        return
    qty = BUY_AMOUNT_USD / price
    portfolio.open_trade(symbol, price, qty, BUY_AMOUNT_USD)
    target = round(price * SELL_TARGET, 6)
    alert_price = round(price * (1 + LOSS_ALERT_PCT), 6)
    msg = (
        "BOUGHT " + symbol + " - PAPER TRADE\n"
        "Spent: $" + str(BUY_AMOUNT_USD) + "\n"
        "Price: $" + str(round(price, 6)) + "\n"
        "Sell target: $" + str(target) + " (+100%)\n"
        "Loss alert at: $" + str(alert_price) + " (-15%)"
    )
    await context.bot.send_message(chat_id, msg)


async def do_sell(chat_id, symbol, context, reason="Manual"):
    trade = portfolio.get_position(symbol)
    if not trade:
        await context.bot.send_message(chat_id, "No position in " + symbol)
        return
    price = get_price(symbol)
    if not price:
        await context.bot.send_message(chat_id, "Cannot get price for " + symbol)
        return
    pnl, pnl_pct = portfolio.close_trade(symbol, price, reason)
    result = "PROFIT" if pnl >= 0 else "LOSS"
    msg = (
        "SOLD " + symbol + " - " + result + "\n"
        "Sold at: $" + str(round(price, 6)) + "\n"
        "PnL: $" + str(round(pnl, 2)) + " (" + str(round(pnl_pct, 1)) + "%)\n"
        "Reason: " + reason + "\n"
        "Balance: $" + str(round(portfolio.balance, 2))
    )
    await context.bot.send_message(chat_id, msg)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "DAYAL'S TRADING BOT - Paper Mode\n"
        "Balance: $1000 fake money\n"
        "Auto-sells at +100% profit\n"
        "Alerts you at -15% loss\n\n"
        "Commands:\n"
        "/status - Portfolio\n"
        "/trades - Open positions\n"
        "/buy DOGE - Buy a coin\n"
        "/sell DOGE - Sell a coin\n"
        "/scan - Find opportunities\n"
        "/history - Past trades"
    )
    await update.message.reply_text(msg)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prices = get_prices_bulk(WATCHLIST)
    price_map = {s: prices[s]['price'] for s in prices}
    total = portfolio.total_value(price_map)
    pnl = total - STARTING_BALANCE
    pnl_pct = round((pnl / STARTING_BALANCE) * 100, 1)
    stats = portfolio.get_stats()
    direction = "UP" if pnl >= 0 else "DOWN"
    msg = (
        "PORTFOLIO - PAPER TRADING\n"
        "Cash: $" + str(round(portfolio.balance, 2)) + "\n"
        "Total Value: $" + str(round(total, 2)) + "\n"
        "PnL: $" + str(round(pnl, 2)) + " (" + str(pnl_pct) + "%) " + direction + "\n\n"
        "Wins: " + str(stats['wins']) + " | Losses: " + str(stats['losses']) + "\n"
        "Win Rate: " + str(round(stats['win_rate'], 0)) + "%\n"
        "Realized PnL: $" + str(round(stats['realized_pnl'], 2))
    )
    await update.message.reply_text(msg)


async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    open_trades = portfolio.get_open_trades()
    if not open_trades:
        await update.message.reply_text("No open positions.")
        return
    prices = get_prices_bulk([t.symbol for t in open_trades])
    text = "OPEN POSITIONS\n\n"
    for t in open_trades:
        p = prices.get(t.symbol, {}).get('price', 0)
        if p:
            pct = round(((p - t.buy_price) / t.buy_price) * 100, 1)
            usd = round((p - t.buy_price) * t.quantity, 2)
            direction = "UP" if pct >= 0 else "DOWN"
            text += t.symbol + ": " + direction + " " + str(pct) + "% ($" + str(usd) + ")\n"
            text += "  Bought: $" + str(round(t.buy_price, 6)) + " | Now: $" + str(round(p, 6)) + "\n\n"
    await update.message.reply_text(text)


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
    await update.message.reply_text("Scanning markets...")
    prices = get_prices_bulk(WATCHLIST)
    opps = []
    for sym, data in prices.items():
        if portfolio.has_position(sym):
            continue
        chg = data.get('change_24h', 0)
        score = 5
        if -15 <= chg <= -3:
            score += 3
        if chg > 0:
            score += 1
        if score >= 7:
            opps.append((sym, data['price'], chg, score))
    opps.sort(key=lambda x: x[3], reverse=True)
    if not opps:
        await update.message.reply_text("No strong signals right now.")
        return
    text = "OPPORTUNITIES FOUND\n\n"
    keyboard = []
    for sym, price, chg, score in opps[:5]:
        text += sym + ": " + str(round(chg, 1)) + "% | Score " + str(score) + "/10 | $" + str(round(price, 6)) + "\n"
        keyboard.append([InlineKeyboardButton("BUY " + sym, callback_data="buy_" + sym)])
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    closed = portfolio.get_closed_trades()[-10:]
    if not closed:
        await update.message.reply_text("No completed trades yet.")
        return
    text = "TRADE HISTORY\n\n"
    for t in reversed(closed):
        result = "WIN" if t.pnl >= 0 else "LOSS"
        text += result + " " + t.symbol + ": $" + str(round(t.pnl, 2)) + " (" + str(round(t.pnl_pct, 1)) + "%) - " + t.close_reason + "\n"
    await update.message.reply_text(text)


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id
    if data.startswith("buy_"):
        sym = data[4:]
        await do_buy(chat_id, sym, context)
        await query.edit_message_text("Buying " + sym)
    elif data.startswith("sell_"):
        sym = data[5:]
        await do_sell(chat_id, sym, context, "Sold via button")
        await query.edit_message_text("Sold " + sym)
    elif data.startswith("keep_"):
        sym = data[5:]
        await query.edit_message_text("Keeping " + sym + " - holding on!")


async def auto_trade_loop(app):
    await asyncio.sleep(15)
    while True:
        try:
            open_trades = portfolio.get_open_trades()
            all_syms = list(set(WATCHLIST + [t.symbol for t in open_trades]))
            prices = get_prices_bulk(all_syms)

            for trade in open_trades:
                p = prices.get(trade.symbol, {}).get('price')
                if not p:
                    continue
                pnl_pct = (p - trade.buy_price) / trade.buy_price

                if pnl_pct >= (SELL_TARGET - 1):
                    pnl, pct = portfolio.close_trade(trade.symbol, p, "Target +100% hit")
                    msg = (
                        "AUTO-SOLD " + trade.symbol + "\n"
                        "Profit: $" + str(round(pnl, 2)) + " (+" + str(round(pct, 0)) + "%)\n"
                        "Balance: $" + str(round(portfolio.balance, 2))
                    )
                    await app.bot.send_message(CHAT_ID, msg)

                elif pnl_pct <= LOSS_ALERT_PCT and not trade.alert_sent:
                    trade.alert_sent = True
                    pnl_usd = round((p - trade.buy_price) * trade.quantity, 2)
                    pct_str = str(round(pnl_pct * 100, 1))
                    kb = [[
                        InlineKeyboardButton("KEEP (HODL)", callback_data="keep_" + trade.symbol),
                        InlineKeyboardButton("SELL NOW", callback_data="sell_" + trade.symbol),
                    ]]
                    msg = (
                        "LOSS ALERT - " + trade.symbol + "\n"
                        "Down " + pct_str + "% ($" + str(pnl_usd) + ")\n"
                        "Bought at: $" + str(round(trade.buy_price, 6)) + "\n"
                        "Now: $" + str(round(p, 6)) + "\n\n"
                        "What do you want to do?"
                    )
                    await app.bot.send_message(CHAT_ID, msg, reply_markup=InlineKeyboardMarkup(kb))

            if portfolio.balance >= BUY_AMOUNT_USD and len(open_trades) < 5:
                for sym, data in prices.items():
                    if portfolio.has_position(sym):
                        continue
                    chg = data.get('change_24h', 0)
                    if -15 <= chg <= -5:
                        price = data['price']
                        qty = BUY_AMOUNT_USD / price
                        portfolio.open_trade(sym, price, qty, BUY_AMOUNT_USD)
                        msg = (
                            "AUTO-BOUGHT " + sym + "\n"
                            "Dip of " + str(round(chg, 1)) + "%\n"
                            "$" + str(BUY_AMOUNT_USD) + " at $" + str(round(price, 6)) + "\n"
                            "Target: $" + str(round(price * SELL_TARGET, 6))
                        )
                        await app.bot.send_message(CHAT_ID, msg)
                        break

        except Exception as e:
            logger.error("Auto trade error: " + str(e))

        await asyncio.sleep(CHECK_EVERY)


async def main():
    if BOT_TOKEN == "YOUR_TOKEN_HERE":
        print("ERROR: Set your BOT_TOKEN environment variable!")
        return

    print("Starting Dayal Trading Bot...")
    print("Paper trading - $" + str(STARTING_BALANCE) + " balance")

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

    print("Bot is LIVE! Message your bot /start on Telegram")
    await auto_trade_loop(app)


asyncio.run(main())
