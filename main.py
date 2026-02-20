main.py"""
🚀 DEGEN TRADING BOT - Paper Trading Mode
Telegram bot that day trades memecoins, crypto & stocks
Auto-sells at +100% profit | Alerts you when losing with Keep/Sell buttons
"""

import asyncio
import logging
import json
import os
import random
from datetime import datetime, timedelta
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, JobQueue
)

from price_fetcher import PriceFetcher
from portfolio import Portfolio, Trade

# ─── CONFIG ───────────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")  # Your chat ID for alerts

PAPER_TRADING = True       # Set to False for real money (future)
STARTING_BALANCE = 1000.0  # Paper trading starting balance in USD
BUY_AMOUNT_USD = 50.0      # How much USD to spend per trade
SELL_TARGET_MULTIPLIER = 2.0  # Sell at 2x (1x profit = 100% gain)
LOSS_ALERT_THRESHOLD = -0.15   # Alert if down -15%
CHECK_INTERVAL_SECONDS = 30    # How often to check prices

# ─── LOGGING ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── GLOBALS ──────────────────────────────────────────────────────────────────
price_fetcher = PriceFetcher()
portfolio = Portfolio(starting_balance=STARTING_BALANCE)


# ═══════════════════════════════════════════════════════════════════════════════
#  COMMAND HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message"""
    chat_id = update.effective_chat.id
    text = (
        f"🤖 *DEGEN TRADING BOT* — Paper Mode Active\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Starting Balance: ${STARTING_BALANCE:,.2f}\n"
        f"📈 Buy Amount: ${BUY_AMOUNT_USD} per trade\n"
        f"🎯 Auto-Sell Target: +100% (2x)\n"
        f"🚨 Loss Alert: {LOSS_ALERT_THRESHOLD*100:.0f}%\n\n"
        f"*Commands:*\n"
        f"/status — Portfolio overview\n"
        f"/trades — Open positions\n"
        f"/buy <symbol> — Manually buy a coin/stock\n"
        f"/sell <symbol> — Manually sell a position\n"
        f"/scan — Scan for opportunities\n"
        f"/history — Trade history\n"
        f"/setreal — Switch to real trading (⚠️ dangerous)\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Portfolio status"""
    stats = portfolio.get_stats()
    balance = portfolio.balance
    total_value = portfolio.total_value(price_fetcher)
    pnl = total_value - STARTING_BALANCE
    pnl_pct = (pnl / STARTING_BALANCE) * 100

    emoji = "🟢" if pnl >= 0 else "🔴"
    mode = "📄 PAPER" if PAPER_TRADING else "💸 REAL MONEY"

    text = (
        f"{emoji} *PORTFOLIO STATUS* — {mode}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 Cash: ${balance:,.2f}\n"
        f"📊 Positions Value: ${total_value - balance:,.2f}\n"
        f"🏦 Total Value: ${total_value:,.2f}\n"
        f"📈 Total P&L: ${pnl:+,.2f} ({pnl_pct:+.1f}%)\n\n"
        f"✅ Wins: {stats['wins']} | ❌ Losses: {stats['losses']}\n"
        f"🎯 Win Rate: {stats['win_rate']:.0f}%\n"
        f"💰 Realized P&L: ${stats['realized_pnl']:+,.2f}\n"
        f"📅 {datetime.now().strftime('%H:%M:%S')}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show open positions"""
    open_trades = portfolio.get_open_trades()

    if not open_trades:
        await update.message.reply_text("📭 No open positions right now.")
        return

    text = "📊 *OPEN POSITIONS*\n━━━━━━━━━━━━━━━━━\n"
    for t in open_trades:
        current_price = price_fetcher.get_price(t.symbol)
        if current_price:
            pnl_pct = ((current_price - t.buy_price) / t.buy_price) * 100
            pnl_usd = (current_price - t.buy_price) * t.quantity
            emoji = "🟢" if pnl_pct >= 0 else "🔴"
            text += (
                f"\n{emoji} *{t.symbol}*\n"
                f"   Buy: ${t.buy_price:.4f} → Now: ${current_price:.4f}\n"
                f"   P&L: ${pnl_usd:+.2f} ({pnl_pct:+.1f}%)\n"
                f"   Target: ${t.buy_price * SELL_TARGET_MULTIPLIER:.4f} (2x)\n"
            )

    await update.message.reply_text(text, parse_mode="Markdown")


async def buy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually buy a symbol: /buy BTC"""
    if not context.args:
        await update.message.reply_text("Usage: /buy <SYMBOL>\nExample: /buy DOGE")
        return

    symbol = context.args[0].upper()
    await execute_buy(update.effective_chat.id, symbol, context)


async def sell_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually sell a position: /sell BTC"""
    if not context.args:
        await update.message.reply_text("Usage: /sell <SYMBOL>\nExample: /sell DOGE")
        return

    symbol = context.args[0].upper()
    await execute_sell(update.effective_chat.id, symbol, context, reason="Manual sell")


async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Scan for trading opportunities"""
    await update.message.reply_text("🔍 Scanning markets... please wait")
    opportunities = await find_opportunities()

    if not opportunities:
        await update.message.reply_text("😴 No strong opportunities found right now. Market is flat.")
        return

    text = "🔥 *OPPORTUNITIES FOUND*\n━━━━━━━━━━━━━━━━━\n"
    keyboard = []
    for opp in opportunities[:5]:
        signal = opp['signal']
        text += f"\n⚡ *{opp['symbol']}* — {signal}\n   Price: ${opp['price']:.4f} | Score: {opp['score']}/10\n"
        keyboard.append([InlineKeyboardButton(f"🟢 Buy {opp['symbol']}", callback_data=f"buy_{opp['symbol']}")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show trade history"""
    closed = portfolio.get_closed_trades()[-10:]  # Last 10 trades

    if not closed:
        await update.message.reply_text("📋 No completed trades yet.")
        return

    text = "📋 *TRADE HISTORY* (Last 10)\n━━━━━━━━━━━━━━━━━\n"
    for t in reversed(closed):
        emoji = "✅" if t.pnl >= 0 else "❌"
        text += f"{emoji} *{t.symbol}*: ${t.pnl:+.2f} ({t.pnl_pct:+.1f}%) — {t.close_reason}\n"

    await update.message.reply_text(text, parse_mode="Markdown")


# ═══════════════════════════════════════════════════════════════════════════════
#  CALLBACK HANDLERS (Button presses)
# ═══════════════════════════════════════════════════════════════════════════════

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline keyboard button presses"""
    query = update.callback_query
    await query.answer()

    data = query.data
    chat_id = query.message.chat_id

    if data.startswith("buy_"):
        symbol = data.split("_", 1)[1]
        await execute_buy(chat_id, symbol, context)
        await query.edit_message_text(f"✅ Buy order placed for {symbol}!")

    elif data.startswith("sell_"):
        symbol = data.split("_", 1)[1]
        await execute_sell(chat_id, symbol, context, reason="Manual sell via button")
        await query.edit_message_text(f"✅ Sold {symbol}!")

    elif data.startswith("keep_"):
        symbol = data.split("_", 1)[1]
        await query.edit_message_text(
            f"🤞 Keeping *{symbol}* — holding on!\n"
            f"Bot will alert you again if it drops further.",
            parse_mode="Markdown"
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  TRADING LOGIC
# ═══════════════════════════════════════════════════════════════════════════════

async def execute_buy(chat_id: int, symbol: str, context: ContextTypes.DEFAULT_TYPE):
    """Execute a buy order"""
    price = price_fetcher.get_price(symbol)
    if not price:
        await context.bot.send_message(chat_id, f"❌ Could not get price for {symbol}")
        return

    if portfolio.balance < BUY_AMOUNT_USD:
        await context.bot.send_message(chat_id, f"❌ Not enough balance! Have ${portfolio.balance:.2f}, need ${BUY_AMOUNT_USD}")
        return

    # Check if already holding this
    if portfolio.has_position(symbol):
        await context.bot.send_message(chat_id, f"⚠️ Already holding {symbol}. Use /sell {symbol} first.")
        return

    quantity = BUY_AMOUNT_USD / price
    trade = portfolio.open_trade(symbol, price, quantity, BUY_AMOUNT_USD)

    mode_tag = "📄 PAPER" if PAPER_TRADING else "💸 REAL"
    await context.bot.send_message(
        chat_id,
        f"🟢 *BUY ORDER EXECUTED* {mode_tag}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🪙 *{symbol}*\n"
        f"💰 Spent: ${BUY_AMOUNT_USD:.2f}\n"
        f"📊 Price: ${price:.4f}\n"
        f"🔢 Qty: {quantity:.4f}\n"
        f"🎯 Sell Target: ${price * SELL_TARGET_MULTIPLIER:.4f} (+100%)\n"
        f"🚨 Loss Alert: ${price * (1 + LOSS_ALERT_THRESHOLD):.4f} ({LOSS_ALERT_THRESHOLD*100:.0f}%)",
        parse_mode="Markdown"
    )


async def execute_sell(chat_id: int, symbol: str, context: ContextTypes.DEFAULT_TYPE, reason: str = "Auto"):
    """Execute a sell order"""
    trade = portfolio.get_position(symbol)
    if not trade:
        await context.bot.send_message(chat_id, f"❌ No open position for {symbol}")
        return

    price = price_fetcher.get_price(symbol)
    if not price:
        await context.bot.send_message(chat_id, f"❌ Could not get price for {symbol}")
        return

    pnl, pnl_pct = portfolio.close_trade(symbol, price, reason)

    emoji = "🟢✅" if pnl >= 0 else "🔴❌"
    await context.bot.send_message(
        chat_id,
        f"{emoji} *SELL ORDER EXECUTED*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🪙 *{symbol}*\n"
        f"📊 Sell Price: ${price:.4f}\n"
        f"💰 P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%)\n"
        f"📝 Reason: {reason}\n"
        f"💵 New Balance: ${portfolio.balance:,.2f}",
        parse_mode="Markdown"
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  AUTO TRADING JOB (runs every X seconds)
# ═══════════════════════════════════════════════════════════════════════════════

async def auto_trade_job(context: ContextTypes.DEFAULT_TYPE):
    """Main auto-trading loop - checks prices and executes trades"""
    chat_id = CHAT_ID or context.job.chat_id
    if not chat_id:
        return

    open_trades = portfolio.get_open_trades()

    # ── Check existing positions ──────────────────────────────────────────────
    for trade in open_trades:
        price = price_fetcher.get_price(trade.symbol)
        if not price:
            continue

        pnl_pct = (price - trade.buy_price) / trade.buy_price

        # AUTO SELL: Hit 2x target (1x profit)
        if pnl_pct >= (SELL_TARGET_MULTIPLIER - 1):
            await execute_sell(chat_id, trade.symbol, context, reason="🎯 Target hit (+100%)")

        # LOSS ALERT: Send Keep/Sell buttons
        elif pnl_pct <= LOSS_ALERT_THRESHOLD and not trade.alert_sent:
            trade.alert_sent = True
            pnl_usd = (price - trade.buy_price) * trade.quantity
            keyboard = [
                [
                    InlineKeyboardButton("💎 KEEP (HODL)", callback_data=f"keep_{trade.symbol}"),
                    InlineKeyboardButton("🔴 SELL NOW", callback_data=f"sell_{trade.symbol}"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(
                chat_id,
                f"🚨 *LOSS ALERT — {trade.symbol}*\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"📉 Down {pnl_pct*100:.1f}% (${pnl_usd:+.2f})\n"
                f"💸 Buy Price: ${trade.buy_price:.4f}\n"
                f"📊 Current: ${price:.4f}\n\n"
                f"What do you want to do?",
                parse_mode="Markdown",
                reply_markup=reply_markup
            )

    # ── Look for new opportunities ────────────────────────────────────────────
    # Only buy if we have cash and fewer than 5 positions
    if portfolio.balance >= BUY_AMOUNT_USD and len(open_trades) < 5:
        opportunities = await find_opportunities()
        if opportunities:
            best = opportunities[0]
            # Only auto-buy if score is very high
            if best['score'] >= 8:
                symbol = best['symbol']
                if not portfolio.has_position(symbol):
                    await context.bot.send_message(
                        chat_id,
                        f"🤖 *AUTO-BUY SIGNAL*\n{best['signal']}\nScore: {best['score']}/10",
                        parse_mode="Markdown"
                    )
                    await execute_buy(chat_id, symbol, context)


async def find_opportunities() -> list:
    """Scan watchlist for trading opportunities"""
    WATCHLIST = [
        # Memecoins
        "DOGE", "SHIB", "PEPE", "FLOKI", "BONK", "WIF", "BRETT", "MOG",
        # Crypto
        "BTC", "ETH", "SOL", "AVAX", "MATIC", "LINK", "ARB",
        # (Stocks are harder without a brokerage API - using crypto for now)
    ]

    opportunities = []
    for symbol in WATCHLIST:
        if portfolio.has_position(symbol):
            continue

        price_data = price_fetcher.get_price_with_change(symbol)
        if not price_data:
            continue

        price = price_data['price']
        change_1h = price_data.get('change_1h', 0)
        change_24h = price_data.get('change_24h', 0)
        volume_change = price_data.get('volume_change', 0)

        # Simple scoring: looking for dips with volume (buy low)
        score = 5  # baseline
        signal_parts = []

        # Recent dip = buying opportunity
        if -15 <= change_1h <= -3:
            score += 2
            signal_parts.append(f"1h dip {change_1h:.1f}%")
        if -20 <= change_24h <= -5:
            score += 1
            signal_parts.append(f"24h dip {change_24h:.1f}%")

        # High volume = momentum
        if volume_change > 50:
            score += 2
            signal_parts.append(f"volume +{volume_change:.0f}%")

        # Slight upward turn after dip (reversal signal)
        if change_1h > 0 and change_24h < -5:
            score += 1
            signal_parts.append("reversal signal")

        if score >= 6:
            opportunities.append({
                'symbol': symbol,
                'price': price,
                'score': min(score, 10),
                'signal': f"{symbol}: {', '.join(signal_parts) or 'momentum detected'}"
            })

    opportunities.sort(key=lambda x: x['score'], reverse=True)
    return opportunities


# ═══════════════════════════════════════════════════════════════════════════════
#  REAL MONEY WARNING
# ═══════════════════════════════════════════════════════════════════════════════

async def setreal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global PAPER_TRADING
    keyboard = [[
        InlineKeyboardButton("⚠️ YES, USE REAL MONEY", callback_data="confirm_real"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel_real"),
    ]]
    await update.message.reply_text(
        "⚠️ *WARNING: SWITCHING TO REAL MONEY*\n\n"
        "This will use ACTUAL funds from your exchange.\n"
        "You can and WILL lose real money.\n\n"
        "Make sure you have configured your exchange API keys in `.env`\n\n"
        "Are you sure?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Set your TELEGRAM_BOT_TOKEN in .env file!")
        print("   Get one from @BotFather on Telegram")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("trades", trades))
    app.add_handler(CommandHandler("buy", buy_command))
    app.add_handler(CommandHandler("sell", sell_command))
    app.add_handler(CommandHandler("scan", scan_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("setreal", setreal_command))

    # Button callbacks
    app.add_handler(CallbackQueryHandler(button_callback))

    # Auto-trading job
    job_queue = app.job_queue
    if CHAT_ID:
        job_queue.run_repeating(
            auto_trade_job,
            interval=CHECK_INTERVAL_SECONDS,
            first=10,
            chat_id=int(CHAT_ID),
            name="auto_trade"
        )
        print(f"✅ Auto-trading enabled — checking every {CHECK_INTERVAL_SECONDS}s")
    else:
        print("⚠️  TELEGRAM_CHAT_ID not set — auto-trading disabled")
        print("   Get your chat ID by messaging @userinfobot on Telegram")

    print(f"🤖 DEGEN BOT started! Mode: {'📄 PAPER' if PAPER_TRADING else '💸 REAL MONEY'}")
    print(f"💰 Starting balance: ${STARTING_BALANCE}")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
