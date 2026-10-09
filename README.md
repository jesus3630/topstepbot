# MES morning bot (Topstep practice / combine)

This is a small day-trading program for **MES** (Micro E-mini S&P 500). You run it yourself, on your own Windows or Mac computer, while you sit in front of it. It is built for a free Topstep **Practice** account first, and later a **50K Trading Combine**. It is not a server, and it does not belong on a VPS, a VPN, or a remote machine.

**This is educational software, not financial advice.** Futures trading can lose money, including the entire combine fee. You are responsible for every order the program sends. Topstep will not undo a bad fill because a bot did it.

The full course notes are in [docs/rulebook.md](docs/rulebook.md). Version 1 trades only two of those setups:

- **Setup A.** The 15-minute opening range (8:30–8:45 CT). The bot waits for a strong break, then a pullback that holds the broken side, then enters.
- **Setup B.** A break of a key level (overnight high/low, prior day high/low/close, pivot, VWAP) and a retest that holds. Only in the first hour.

Every number you might want to change is in **one file**: [config/settings.yaml](config/settings.yaml). If the rulebook says `[PROPOSED DEFAULT]`, that number is in the config, not buried in the code.

## What it will and will not do

- It looks for new trades only from **8:45 to 10:15 CT**.
- It flattens everything at **10:30 CT**, and it will not hold past Topstep's **3:10 PM CT** cutoff.
- It risks **$200** a trade, stops the day at **$400** (realized plus open P&L) or at **$800** profit, takes at most **3** trades, and stops after **2 losses in a row**.
- Size starts at **2 MES** and is reduced when the stop is wide: contracts = risk ÷ (stop distance × tick value + slippage + fees), then capped by `max_contracts`.
- It takes **half off** at the next key level when that level is at least 2:1, and trails the rest.
- **Moving the stop to breakeven after the first target is OFF.** Topstep forbids "tight brackets or auto-breakeven used to exploit sim fills." Leave `move_stop_to_breakeven_after_first_target` false unless you have decided, yourself, that you want it. The program will not turn it on for you.
- It does not trade a day you list under `blackout_dates`, and it does not trade when `no_trade_today` is true. It does **not** download an economic calendar. You type CPI, jobs-report (NFP), and FOMC days yourself.
- Every entry is sent with a **protective stop at the broker** (a bracket). In TopstepX, turn on **Auto OCO Brackets** under Settings → Risk Settings. If the account is in Position Brackets mode, the API rejects the bracket and this program cancels that order instead of leaving it naked.

## Install

You need Python 3.11 or newer.

**Windows.** Install Python from [python.org](https://www.python.org/downloads/windows/). On the first installer screen, check **Add python.exe to PATH**.

**Mac.** Install Python 3.11+ from [python.org](https://www.python.org/downloads/macos/) or with Homebrew (`brew install python`).

Then, in a terminal opened **in this folder**:

```bash
python -m venv .venv
```

Windows, every new terminal:

```bat
.venv\Scripts\activate
```

Mac:

```bash
source .venv/bin/activate
```

Then:

```bash
python -m pip install -r requirements.txt
```

If `python` is not found, use `python3` in every command below.

## API key

1. Sign in to TopstepX.
2. Open Settings → API: <https://topstepx.com/settings?tab=api>
3. Create a key and copy it once. Copy your **sign-in username** too. That is not your email and not the account number.
4. In this folder, copy `.env.example` to a file named `.env`.
5. Paste the username and key into `.env`:

```text
PROJECTX_USERNAME=your_username
PROJECTX_API_KEY=your_key
```

`.env` stays on your PC. It is listed in `.gitignore`. The program reads it and does not print it. Do not email it, do not put it in the config file, and do not paste it into a chat.

Leave the three URL lines as they are unless Topstep gives you different ones. They point at the public ProjectX gateway (`https://api.topstepx.com`) and the realtime hubs (`https://rtc.topstepx.com/hubs/user` and `.../market`).

## Check the connection (read-only)

Do this before you point the bot at a Combine. The check **cannot place, modify, or cancel orders**. It does not call any `/api/Order/` route. It is safe to run against the 50K Combine you already bought. It does not need a Practice account.

On a Mac, in a terminal opened in this folder. Reinstall once so the new `websockets` package is present (`signalrcore` is no longer used):

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m topstepbot check
```

That listens for MES quotes for about 20 seconds, then disconnects. To skip the quote listen:

```bash
python -m topstepbot check --no-signalr
```

What you should see:

1. **Auth.** `PASS  Logged in.` The API key and the session token are never printed. A wrong key prints `FAIL` and an error code, then stops.
2. **Accounts.** One line per active account: `id`, `name`, `balance`, `canTrade`, and `simulated` exactly as the API sent them (`field absent` if a field is missing). A line `50K Combine: id=... name=...` marks the account whose name looks like a 50K Combine (`50K` plus `TC` or `COMBINE`).
3. **Contracts.** Every contract returned for `MES`, with id, name, tick size, and tick value, then `Front month the bot would pick`.
4. **Bars.** The count of 5-minute MES bars from about the last day, the first and last timestamps **in the order they arrived**, and `newest-first` or `oldest-first`.
5. **SignalR.** The hub URL with no token (`wss://rtc.topstepx.com/hubs/market`), how many MES quotes arrived, and one sample (`lastPrice`, `bestBid`, `bestAsk`). While the CME equity-index session is open, **zero quotes is `FAIL`**, and `OVERALL` is `FAIL` too. `WARN` is only when that session is actually closed (the 4:00–5:00 PM CT halt, or the weekend). A handshake error is always `FAIL`.
6. A **SUMMARY** with `PASS`, `FAIL`, or `WARN` on each step. `OVERALL PASS` means login, accounts, contracts, and bars passed, and the quote step was not `FAIL`.

The raw responses, with the key and token replaced by `[REDACTED]`, are written to `logs/check-<timestamp>.json`. That file is how the `TODO-VERIFY` notes get checked against your real account. `logs/` is gitignored.

`account.kind` is `combine`, and `account.account_name_contains` is `50KTC`. A live check showed the account `50KTC-…` with `simulated=true` and `canTrade=true`. That account is an allowed target. A Live Funded account (`simulated=false`, or a name containing `LIVE`) is still refused. This check does not trade.

Your Standard 50K Combine has Topstep's **$1,000** Daily Loss Limit. The bot's own daily stop is still **$400**, inside that limit and inside the **$2,000** maximum loss.

Do not run `python -m topstepbot practice --arm` as part of this check. That command can send orders. It will stay flat unless a live MES quote arrives while the market is open.

Before any armed session, set the Combine's bracket mode to **Auto OCO Brackets** (TopstepX → Settings → Risk Settings). Position Brackets is the platform default. The bot sends `stopLossBracket` and `takeProfitBracket` on each entry. In Position Brackets mode the API rejects those fields with `Brackets cannot be used with Position Brackets. You must enable Auto OCO Brackets.` and still creates the order. Auto OCO Brackets is what attaches the stop and target to the order.

## Run a backtest

No account and no API key:

```bash
python -m topstepbot backtest
```

That replays `data/sample_mes_1m_synthetic.csv`. **Those prices are synthetic.** They were drawn so the backtest has something to do. They are not the real S&P, and a good result on them does not mean the strategy makes money.

The report shows trade count, win rate, average R, max drawdown, the worst day, and whether any day reached the **$400** daily stop or Topstep's **$2,000** maximum loss.

Your own file must look like this. Times can be Central time (with the offset) or UTC (`Z`). A line that starts with `#` is ignored.

```text
timestamp,open,high,low,close,volume
2026-01-06T08:30:00-06:00,5642.00,5644.00,5641.75,5643.25,120
```

```bash
python -m topstepbot backtest --csv data/downloads/mes_1m.csv
```

To download real MES 1-minute bars, use the read-only command below. It cannot place an order. `download-bars` still exists, but that path constructs the trading client, so do not use it for a history pull.

## Real MES history

Run this on the Mac that has the ProjectX key in `.env`. This environment does not have that key. The command logs in with the read-only client (`History/retrieveBars` and `Contract/search` only), asks for one Central-time day at a time, and writes:

```text
timestamp,open,high,low,close,volume,contract
```

Timestamps are Central time. The default asks for 365 days, which is the aim (about 6 to 12 months, and a bit more). How far ProjectX actually returns is not known until this command runs. The oldest bar it prints is the limit.

```bash
cd /Users/heyzeus/topstepbot
source .venv/bin/activate
python -m topstepbot fetch-history --days 365 --out data/mes_1m_real.csv
```

A shorter pull is the same command with a smaller `--days`. If the command stops, run it again with the same `--out`. Days already in the file are skipped, except the last day, which is always refreshed. Bars are deduped by contract and minute.

Known ProjectX limits, from the public docs and the client already in this repo:

- `retrieveBars` accepts at most 20,000 bars per call. One day of MES 1-minute bars is under that. If a response still hits the cap, that slice is split in half and requested again.
- History calls are paced at 50 every 30 seconds. HTTP 429 waits, honors a numeric `Retry-After` when ProjectX sends one, and stops after 5 failures or 30 seconds of failures. The file already written is kept.
- The documented sample lists bars newest-first. This command does not depend on that order. It keys each bar by its timestamp.
- `live: false` is the sim data feed, the same flag the 2026-10-07 check used.
- Expired quarters are searched as `MES` plus codes such as `MESU6` and `MESU26` (and `MESM`, `MESH`, `MESZ` for the months in the window). `Contract/available` is not called, because the read-only client is not allowed to call it. If search does not return a quarter, that code is printed and skipped. The file then contains only the contracts search did return.
- The command calls `place_order` once, on purpose, and continues only when that raises the read-only refusal. A client that can place an order is rejected before any download request.

After the CSV is on disk:

```bash
python -m topstepbot history-report --csv data/mes_1m_real.csv
```

That replays the live strategy with the paper broker's costs (1 tick of slippage on the entry and again on the exit, plus the config fee of $1.40 per contract round turn). The fee is still the placeholder. The report splits the first two thirds of sessions from the last third, compares drawdown with the $2,000 trailing maximum loss and the $1,000 daily loss limit, counts sessions to a $3,000 target, and checks the 40% consistency rule. It also says, for each filter, how many breakouts it blocked and what those blocked trades returned. A later section replays five entry definitions that were written down in advance (the live 2-tick retest, an 8-tick retest, a retest of 0.25 times the opening-range width, a breakout-close entry with the stop at the range midpoint, and a retest whose stop sits beyond the pullback swing). Those runs are not saved. It does not change `config/settings.yaml`. The text is printed and saved to `logs/history-report.txt`.

## Run in paper mode

Paper mode replays a CSV through a simulated account. Nothing is sent to Topstep. You still have to arm it, so a double-click does not trade by surprise:

```bash
python -m topstepbot paper --arm --speed 0.2
```

`--speed 0.2` pauses a fifth of a second on each minute so you can try the kill switch. Use `--speed 0` to run it as fast as the backtest.

## Run on a Topstep Practice account

Do this only while you are sitting at **this** computer. If you do not have a Practice account, do not run this command. Run the read-only check above instead. Practice mode can send orders to whichever simulated account the config selects.

When you do have a Practice account (or you have finished the checklist and you mean to trade the Combine), confirm the account in TopstepX and turn on **Auto OCO Brackets**.

In `config/settings.yaml`:

- `account.kind` is `combine` and `account.account_name_contains` is `50KTC`, matching the Combine from the read-only check. Change these only if you mean a different account.
- `news.no_trade_today` is `false` only on a day you actually want to trade.
- Put today's date in `blackout_dates` if it is CPI, NFP / jobs, or FOMC. Example: `"2026-03-06"`.
- Leave `runtime.armed` false. Pass `--arm` on the command instead, so you make the choice that morning.

```bash
python -m topstepbot practice --arm
```

The program logs in, picks the one simulated account that matches your settings, and **refuses to trade** if the account looks like a Topstep **Live Funded** account. ProjectX API trading is not allowed there. The only way past that refusal is to type this exact line into `compliance.allow_live_funded_account_override` in the config:

```text
I_UNDERSTAND_PROJECTX_CANNOT_TRADE_TOPSTEP_LIVE_FUNDED_ACCOUNTS
```

Typing that does **not** make live API trading allowed by Topstep. Leave the line empty.

Stay at the PC from 8:30 to 10:30 CT. Read the log. If anything looks wrong, stop it.

The bot looks up your account and the MES contract once at startup, prints any open positions and working orders, and does not search for them again on every bar. If ProjectX answers HTTP 429 (too many requests) it waits, with a longer pause after each failure, and then tries the read again. If the failures keep going (5 in a row, or 30 seconds of them), it stops trading, prints the open positions and orders one time, and exits. It will not fill the terminal with the same error. After you pull this fix, confirm the account is flat in TopstepX and Auto OCO Brackets is on, then start again with:

```bash
source .venv/bin/activate
python -m topstepbot practice --arm
```

## Command center

The trading window prints short sentences. The long version of each line stays in `logs/bot-<date>.log`. For a screen you can read from across the desk, open the command center in a **second** terminal. Both terminals must be in this same folder (`topstepbot`), so the kill button and the bot share the `KILL` file.

Terminal 1 — the dashboard. Leave it open. It opens a browser on this Mac only (`127.0.0.1`). It does not log in and it cannot place a trade:

```bash
source .venv/bin/activate
python -m topstepbot dashboard
```

Terminal 2 — the bot, after Auto OCO Brackets is on and you are at this computer:

```bash
source .venv/bin/activate
python -m topstepbot practice --arm
```

The page shows armed / watching / in a trade / flat for the day, Central time, the countdown to the 10:30 CT flatten, the MES price, the opening range, today's result, and a plain-English event list. The chart is 1-minute prices. The bot decides on 5-minute bars, and the page says so. If the bot process stops sending updates, the page says **The bot process stopped**. If the bot is still running and only the live quotes go quiet, the banner says **QUOTES STALE** and the rest of the page stays up.

The trading window prints a "Still running" line about once a minute, so a quiet stretch is not a mystery. Once a minute is also how often a dropped quote stream tries to reconnect. A 5-minute bar that does not become a trade is written down, including when price closed through the opening range but a filter said no.

## Double-click on the Mac

From the repo folder, copy the two launchers to the Desktop and mark them runnable. Do this once:

```bash
cp "Start Bot.command" "Stop Bot.command" ~/Desktop/
chmod +x ~/Desktop/"Start Bot.command" ~/Desktop/"Stop Bot.command"
```

Then stay at the Mac. Double-click **Start Bot**. It opens two Terminal windows in `/Users/heyzeus/topstepbot`. The first runs `git pull`, activates `.venv`, and starts the dashboard. The second activates `.venv` and runs `python -m topstepbot practice --arm`. That `--arm` is the same "I am here" switch as typing the command yourself. Nothing is scheduled. If you have to leave, double-click **Stop Bot**. It creates the `KILL` file, which flattens and stops the bot.

The red **KILL** button asks you to confirm, then creates the `KILL` file. That is the same flatten as typing `kill` in the bot window. It is the only thing the dashboard writes.

A read-only check also updates the page while it runs:

```bash
python -m topstepbot check
```

## How to stop it

Any one of these cancels working orders and flattens:

- Type `kill` and press Enter in the same terminal.
- Create a file named `KILL` in this folder (the name is set by `runtime.kill_switch_file`).
- Press Ctrl+C.

A kill switch ignores the minimum hold time. It is an emergency exit, not a strategy. Delete the `KILL` file before you start the next session. The program removes a leftover `KILL` file when it starts, then watches for a new one.

There is also a minimum time between orders (`compliance.min_seconds_between_orders`, default 60 seconds) and a minimum hold (`min_hold_seconds`, default 120 seconds) so the program is not a high-frequency scalper. The two bracket orders that make "sell half / trail the rest" are one decision, sent together. The protective stop already resting at the broker can still fill at any time. That is the point of the stop.

Logs go to the terminal and to `logs/bot-YYYY-MM-DD.log`. Signals, orders, fills, and P&L are written there. Secrets are not.

## Before a paid Combine

Do not point this at a paid combine until you can check every line.

- [ ] You have read [docs/rulebook.md](docs/rulebook.md) and you agree with the config values, especially risk, the two setups, and breakeven left **off**.
- [ ] The backtest on **your** downloaded data, not the synthetic file, has enough trades that you understand the losers. The course suggests a long sample before real risk. The synthetic file is only a demo.
- [ ] You have watched it for many sessions on the **Practice** account, sitting at the PC, and the fills match what you expected.
- [ ] Auto OCO Brackets is on. You have seen a protective stop on every entry in the TopstepX order book.
- [ ] `round_turn_fee_per_contract` matches your statement. The 1.40 in the config is a placeholder from a public API example, not a promise of your fee.
- [ ] CPI, NFP, and FOMC days are in `blackout_dates`. You re-check Topstep's rule pages; they change.
- [ ] You set a Personal Daily Loss Limit in TopstepX as a backstop (the bot's $400 stop is not a substitute for theirs).
- [ ] The account name is the Combine, not a Live Funded account.
- [ ] You know how to type `kill`, and you will be at the PC the whole window.
- [ ] You accept that a bad day can fail the combine. This program does not prevent that.

## Topstep rules that apply to this program

Checked against Topstep's help pages when the rulebook was written (7 Oct 2026). Re-read them yourself before you rely on them:

- Bots are allowed on eligible **simulated** accounts through the TopstepX / ProjectX API. You own the bot and you are solely responsible for it. High-frequency trading is prohibited.
- Trading has to come from your own PC. No VPS, VPN, or remote server placing, changing, or cancelling orders.
- **Live Funded Accounts cannot trade through the ProjectX API.** Practice, the Trading Combine, and (under the same written rules) an Express Funded Account are the simulated accounts this is meant for.
- The 50K combine fails at a **$2,000** trailing maximum loss, including open P&L. The bot's $400 day stop is meant to sit well inside that. It is not the combine rule itself.
- Topstep also forbids exploiting sim fills, including tight brackets or auto-breakeven used for that purpose. That is why breakeven is off unless you turn it on.

Help pages named in the rulebook: TopstepX API Access, Trading Combine parameters, maximum loss limit, daily loss limit, prohibited strategies, live funded account parameters.

## What is not verified about the ProjectX API

These pieces follow the public docs at <https://gateway.docs.projectx.com> and the swagger at <https://api.topstepx.com/swagger/index.html> (read 7 Oct 2026). The trading client has not been called from this repo, because that would require your key and could send orders. The read-only check above is the call that records a real response without placing an order. Where a detail is still unconfirmed on a live payload, it is marked `TODO-VERIFY`. The paper broker and the backtest do not need any of it.

Verified from the docs and implemented:

- Login: `POST /api/Auth/loginKey` with `userName` and `apiKey`. The token lasts 24 hours. Later calls send `Authorization: Bearer`.
- `POST /api/Auth/validate`, `POST /api/Account/search`, `POST /api/Contract/available`, `POST /api/Contract/search`.
- `POST /api/History/retrieveBars` (units include minute = 2, max 20,000 bars).
- `POST /api/Order/place` with `stopLossBracket` / `takeProfitBracket`, plus cancel, modify, and `searchOpen`.
- `POST /api/Position/searchOpen` and `POST /api/Position/closeContract`.
- `POST /api/Trade/search`.
- SignalR user hub and market hub URLs, event names (`GatewayTrade`, `GatewayQuote`, `GatewayUserOrder`, and the account/position/trade events), and the order enums (buy = 0, sell = 1, market = 2, stop = 4, limit = 1).

Not verified, so treat practice mode as something **you** test on a Practice account before you trust it:

- **Confirmed 2026-10-07** on this Combine: `Account/search` returned `simulated=true`, `canTrade=true`, and `balance`. The name started with `50KTC`. If a future payload omits `simulated`, the bot still requires the name to match `account.kind`.
- **Confirmed 2026-10-07:** the active MES contract was `id=CON.F.US.MES.Z26`, `name=MESZ6`, `symbolId=F.US.MES`, `tickSize=0.25`, `tickValue=1.25`. The bot picks the first `activeContract: true` MES match from `Contract/search`.
- **Confirmed 2026-10-07:** `retrieveBars` returned 5-minute bars newest-first (276 bars). The trading client sorts them oldest-first. `live: false` returned those bars for this Combine.
- The market hub URL is `wss://rtc.topstepx.com/hubs/market` (the docs write `https`; WebSockets use `wss`, with `skipNegotiation`). Subscribe with `SubscribeContractQuotes`. `signalrcore` 1.0.2 reads the frame header with a 2-byte SSL `recv`, and on Python 3.14 that raises `SSL: BAD_LENGTH` and then delivers no quotes. The bot uses a small SignalR JSON client on the `websockets` package instead. If no quote arrives within `quote_timeout_seconds` (20) while MES is in session, practice mode logs that and stays flat.
- **TODO-VERIFY:** `POST /api/Position/partialCloseContract` (listed in the docs index; the page body was not in the fetch). Version 1 does not need it for the normal "sell half" path. Half and the runner are two separate bracket orders, each with its own stop.
- **TODO-VERIFY:** Bracket child-order ids. `Order/place` returns the parent id. After a fill, trailing looks for the working stop with `searchOpen`. If it cannot find one, it will not pretend the trail worked.
- **TODO-VERIFY:** Your real round-turn MES fee. `1.40` is only a starting number from a public trade example.
- **TODO-VERIFY:** Whether a later contract roll still uses the same id shape (`CON.F.US.MES.` + month). The 2026-10-07 front month was `CON.F.US.MES.Z26`. Leave `instrument.contract_id` blank unless search picks the wrong one.
- Leave `broker.projectx_use_live_data` false. That is the flag the 2026-10-07 history call used.
- **Not known until `fetch-history` is run on the Mac:** how many days back `retrieveBars` will actually return, and whether search returns expired quarter codes (`MESU26`, `MESM26`, and the rest). The command prints both.

If a bracket is rejected because the account is in Position Brackets mode, the docs say the order can still be created. This client cancels that order id. Confirm the cancel on your Practice account the first time you connect.

## Layout

| Path | What it is |
|---|---|
| `config/settings.yaml` | The only settings file |
| `docs/rulebook.md` | The course rulebook |
| `topstepbot/strategy/engine.py` | Setup A and Setup B, shared by backtest and live |
| `topstepbot/broker/paper.py` | Offline fills |
| `topstepbot/broker/projectx.py` | ProjectX gateway client (can send orders) |
| `topstepbot/broker/readonly.py` | Read-only client used by `check` and `fetch-history` |
| `topstepbot/historyfetch.py` | Resume-safe MES history download. Cannot place orders |
| `topstepbot/historyreport.py` | Strategy report on a real CSV. Does not change settings |
| `data/sample_mes_1m_synthetic.csv` | Fake bars so the backtest runs immediately |
| `data/mes_1m_real.csv` | Created on the Mac by `fetch-history`. Gitignored |
| `tests/` | Unit tests |

```bash
python -m pytest
```

## License of the idea

The trading rules are a structured reading of a course and an e-book, plus proposed defaults, plus Topstep's published rules. See the source tags in the rulebook. Nothing in this folder is a promise of profit.
