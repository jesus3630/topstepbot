# Bot Rulebook: "The 2-Hour Trading Day" (OnlyPropFirms / BDH Trading)

Prepared for Jesus Gonzalez. All times are **Central Time (CT, America/Chicago)**. The course writes times in "EST"; its open time (9:30 AM ET) is the U.S. stock-market cash open, which is always **8:30 AM CT** (ET minus 1 hour all year).

Educational only, not financial advice. Trading futures can lose money, and a bot can lose it faster.

## How to read this document

- **[Source: Lxx]** means the rule comes from course lesson number xx (see the lesson key below). **[Source: EB ch.N p.P]** means it comes from the *Funded Futures* e-book by Patrick Wieland, chapter N, page P.
- **[PROPOSED DEFAULT]** means the course gives no number or rule here, so I picked a starting value. It is **not from the course**. Jesus should confirm it, change it, or backtest it.
- **[CONTRACT SPEC]** means an exchange fact (CME contract specs), not course content.
- **[TOPSTEP]** means a Topstep rule, checked on help.topstep.com on Oct 7, 2026 (links in Section 7).

Lesson key (from lessons.tsv): L1 Welcome; L11 Intro to Market Structure; L12 Market Structure; L14 Indicators; L15 21 EMA; L17 Momentum Trading Strategy; L18/L19 Bear & Bull Flags; L20/L21 Gap Fills; L22/L23 15 Minute Opening Range; L24/L25 Breakouts; L26/L27 Patterns; L28 Entering & Exiting; L29 Timing Entries; L30 Managing Trades; L31 Little Yellow Line; L32 Planning Exits; L33 Identifying Consolidation; L35 How to Manage Risk; L36 Contract Sizing; L38-L40 Psychology/Patience/Emotion; L42 Create a Trading Plan; L44 Backtesting Strategies; L52 10 Step Morning Game Plan. Chapter 10 (L45-L51, live trade breakdowns) is video only, with no text, so nothing here comes from it.

---

## 1. The course in one paragraph

The course teaches a simple **momentum day-trading** style for index futures. You trade only the busiest part of the morning, only in the direction the market is already moving, and only after it confirms (you don't predict tops or bottoms). Before the open you mark key price levels: yesterday's high, low and close, the overnight high and low, pivot points, and VWAP. You then wait for the first 15-minute candle to set the opening range, and you trade when price **breaks a key level with strong volume**. Patrick's preferred entry is the **pullback that retests and holds the broken level**. The stop always goes where the trade idea would be wrong, never just a tight random number. You aim for at least 2:1 reward-to-risk, take partial profit at the next key level, trail the rest, and stop trading for the day at a preset daily loss. The setups taught are opening-range breakouts, key-level breakout-and-retest, bull/bear flags, gap fills, 21 EMA trend pullbacks, and breakouts from consolidation. Patience, sitting out choppy markets, and sticking to the plan are repeated in almost every lesson. [Source: L17, L23, L29, L32, L35, L52; EB ch.6 p.60-62, ch.10 p.96-99, ch.11 p.104-106]

---

## 2. Instruments and session

### 2.1 Instruments

1. The course's worked examples use **ES (E-mini S&P 500)**. The morning routine says to check **ES, NQ, YM and DXY** for agreement. [Source: L23, L52 step 4]
2. The e-book says to start with **micro** contracts and move up to minis as skill and account grow. [Source: EB ch.5 p.53-55; L36]
3. The course also gives crude oil and gold examples, but only to illustrate. The opening times it quotes for those (9:00 AM and 8:20 AM "EST") are old pit-session times, so don't use them. [Source: L23]
4. **[PROPOSED DEFAULT]** Trade **one instrument at a time**, starting with **MES**. MNQ is the alternative if Jesus prefers Nasdaq. Use ES and NQ data (and YM if available) only as **confirmation feeds**, not for trading. Use the front-month contract with the highest volume, and switch to the next contract in roll week once its volume is higher.
5. **[CONTRACT SPEC]** Tick values for sizing: MES 0.25 pt = $1.25 ($5/pt); ES 0.25 pt = $12.50 ($50/pt); MNQ 0.25 pt = $0.50 ($2/pt); NQ 0.25 pt = $5.00 ($20/pt).

### 2.2 The "2-hour" window

The course and e-book promise trading "less than 2 hours a day" but **never give exact start and end times**. Here is what they do say: the open has the most volatility and volume [L17]; the opening range is the first 15 minutes from 9:30 ET [L23]; Patrick is "particularly interested in the first hour of trading" [EB ch.10 p.98]; and the morning routine ends with the first 15-minute candle [L52 step 10].

| Time (CT) | What happens | Source |
|---|---|---|
| 07:00-08:15 | Pre-market routine (Section 5). Bot builds the level map; Jesus does his manual checks | L52 (times are **[PROPOSED DEFAULT]**) |
| 07:30 | 8:30 ET economic releases. Flat, no entries | L52 step 1 |
| 08:30 | RTH (cash) open. Opening range starts | L23 (9:30 ET) |
| 08:30-08:45 | Opening range forms. **No entries** ("patience is key during this period") | L52 step 10; no-entry rule **[PROPOSED DEFAULT]** |
| 08:45 | 9:45 ET releases. Apply news blackout if flagged high-impact | L52 step 1 |
| 08:45-10:15 | **Entry window** | **[PROPOSED DEFAULT]** |
| 09:00 | 10:00 ET releases. Apply news blackout if flagged high-impact | L52 step 1 |
| 10:15 | Last new entry | **[PROPOSED DEFAULT]** |
| 10:30 | **Flat. Bot stops for the day.** (08:30-10:30 = the "2 hours") | **[PROPOSED DEFAULT]** |

### 2.3 No-trade conditions

1. **Scheduled news.** Check news every morning. Avoid **holding open positions** through the release times 8:30, 9:45 and 10:00 AM ET (07:30, 08:45, 09:00 CT). Know high-impact from low-impact events. [Source: L52 step 1] Don't enter right after big news; wait for the market's reaction. [Source: L29 #7]
   - **[PROPOSED DEFAULT]** For each **high-impact** release inside the window: flatten 2 minutes before it, and allow no new entries until 10 minutes after it. Low-impact releases: no action.
2. **Choppy/sideways market.** Sit out "the chop": price ping-ponging between levels, or moving averages flattening and tangling. [Source: EB ch.7 p.73-74; L33 #4; L39; L19 "flags less effective in choppy markets"]
   - **[PROPOSED DEFAULT]** Chop filter: no new entries if price has crossed the 5-min 21 EMA **4 or more times in the last 12 bars**, OR the 21 EMA slope over the last 6 bars is less than 0.1 × ATR(14).
3. **Indices disagree.** Check whether ES, NQ and YM agree on trend. [Source: L52 step 4]
   - **[PROPOSED DEFAULT]** Take a long only if ES **and** NQ are both above their session VWAP (short: both below). DXY is a manual check only (see Section 5).
4. **Daily limits hit** (Section 4): daily max loss, daily profit stop, max trades, or consecutive losses. [Source: L35 #9, L42 #5]
5. **Trader not in the right headspace** (sick, upset, off his game). This is Jesus's manual go/no-go. [Source: EB ch.13 p.117]
6. **[PROPOSED DEFAULT]** No trading on CME holiday or early-close days, on FOMC-decision days (keep the bot off), or if the bot's data feed or connection is unhealthy (flatten and halt).

---

## 3. The setups

### 3.0 Shared definitions used by every setup

| Item | Course says | Bot value |
|---|---|---|
| Signal timeframe | Prepare on 5-min, 15-min, 1-hour [L52 step 7]; "a day trader might focus on the five-minute chart" [EB ch.9 p.82] | **5-min bars** for signals; 15-min and 60-min for context **[PROPOSED DEFAULT]** |
| Trend (market structure) | Uptrend = higher highs and higher lows; downtrend = lower highs and lower lows [L11, L12; EB ch.10 p.93] | Swing high/low = 5-min fractal with 2 bars on each side. Uptrend = last 2 swing highs rising AND last 2 swing lows rising **[PROPOSED DEFAULT]** |
| VWAP | Above VWAP = longs; below = shorts [L14; EB ch.10 p.93] | Session VWAP anchored at 17:00 CT Globex open **[PROPOSED DEFAULT: anchor time]** |
| 21 EMA | 21-period EMA; price above = longs only, below = shorts only [L15 #4] | EMA(21) on close, 5-min **[PROPOSED DEFAULT: timeframe]** |
| Direction filter | Trade with the trend [L12, L29 #1; EB ch.10 p.91] | Long only if close > VWAP and close > EMA21; short is the mirror |
| Overextended | A wide gap between price and the 21 EMA = overextended; reversal possible [L15 #1] | No new entry if \|close − EMA21\| > 2.0 × ATR(14) **[PROPOSED DEFAULT]** |
| "High/strong volume" | Breakouts need a volume surge [L19, L23, L25, L29 #3; EB ch.9 p.85] | Signal-bar volume ≥ 1.5 × average volume of the prior 20 bars **[PROPOSED DEFAULT]** |
| "Strong candle" | A strong bullish/bearish candle closing beyond the level [L25 #2] | Body ≥ 60% of bar range, closes ≥ 1 tick beyond the level, and closes in the top 30% of the bar (long) **[PROPOSED DEFAULT]** |
| Pivot points | From the previous day's high, low and close; PP = average of H, L, C; S1-S3 and R1-R3 [L14] | Classic floor pivots: PP=(H+L+C)/3, R1=2PP−L, S1=2PP−H, R2=PP+(H−L), S2=PP−(H−L), R3=H+2(PP−L), S3=L−2(H−PP) **[PROPOSED DEFAULT: formulas for S/R]** |
| Previous day H/L/C | Previous day's close is the gap reference [L21, L52 step 6] | Prior RTH session 08:30-15:00 CT; close = last price at 15:00 CT **[PROPOSED DEFAULT]** |
| Overnight / pre-market H/L | Overnight highs and lows; "pre-market high" [L52 step 2; EB ch.10 p.98] | High/low from 17:00 CT (prior day) to 08:30 CT **[PROPOSED DEFAULT]** |
| Key levels list | Prior close, overnight H/L, pre-market action, pivots, VWAP, hourly highs/lows, previous highs/lows, round numbers [L52 steps 2,3,5,8; EB ch.9 p.82; EB ch.10 p.98] | Bot keeps a sorted list of all of these. Round numbers = ES/MES multiples of 25 pts; NQ/MNQ multiples of 100 pts **[PROPOSED DEFAULT]** |
| ATR | Used for volatility and stops; no settings given [L25, L32, L33] | ATR(14) on 5-min **[PROPOSED DEFAULT]** |
| MACD | Patrick uses it only to confirm trend, never for entries [EB ch.10 p.98-99] | MACD(12,26,9) on 5-min. Optional filter, **off** by default **[PROPOSED DEFAULT]** |
| RSI | RSI > 70 overbought, < 30 oversold [L19, L29 #5, L30 #7] | RSI(14). Not used for entries; optional exit warning only **[PROPOSED DEFAULT]** |
| LuxAlgo "Price Action Concepts" | One of the author's 3 indicators [L14] | Paid, closed-source, can't be coded exactly. Replaced by the fractal swing-high/low logic above **[PROPOSED DEFAULT]** |
| Stop buffer | "Just" outside / "just" below [L23, L25, L33] | 2 ticks beyond the level **[PROPOSED DEFAULT]** |
| Minimum reward:risk | At least 2:1 [L29 #8, L32, L35 #3; EB ch.11 p.104] | **Entry filter:** distance from entry to the first target must be ≥ 2R, otherwise skip the trade (R = entry-to-stop distance) |
| Every trade has a stop | Placed the moment you enter; never moved further away [L30 #1, #6; EB ch.11 p.105-106] | Bracket order (stop + target) sent with the entry, always |
| Stop location logic | At the level that invalidates the idea; if that's too much money, trade fewer contracts instead of using a tighter stop [EB ch.11 p.106] | Size from the stop (Section 4); never tighten the stop just to fit the risk |

### 3.0.1 Shared trade management (all setups)

1. **Partial profit:** close **half** at the first target (the next key level, or the setup's measured target), and let the rest run with a trailing stop. [Source: L30 #4, L32 "close half ... remainder ... trailing stop"; EB ch.10 p.98 "partial profits at the next significant resistance level, then trail a stop on the remainder"]
2. **Trailing stop:** the course says to trail but gives no method. [Source: L17, L25, L30 #3, L32]
   - **[PROPOSED DEFAULT]** After the first target fills, move the stop to entry price (breakeven). Then trail it to 2 ticks below the most recent confirmed 5-min swing low (long) or above the swing high (short). Exit the rest on a 5-min close back across the 21 EMA (this exit signal is from L15 #3), or at the next key level beyond the first target.
   - Note: the course never says "move to breakeven". That is my default. See also the Topstep note about auto-breakeven in Section 7.
3. **Momentum-fade exit:** get out when momentum fades: falling volume, failure to make new highs (lows), or a break of key support (resistance). [Source: EB ch.10 p.92, p.94; L25 #6]
   - **[PROPOSED DEFAULT]** Exit the remaining position if 3 straight 5-min bars fail to make a new high (long) AND their average volume is under the 20-bar average.
4. **Time exit:** the course says a time-based exit is valid but gives no time. [Source: L32 #4]
   - **[PROPOSED DEFAULT]** Exit if the first target isn't hit within 45 minutes of entry. Always flat at 10:30 CT.
5. **Volatility exit:** exit if volatility suddenly jumps (ATR). [Source: L32 #6]
   - **[PROPOSED DEFAULT]** Exit at market if a single 5-min bar's range is more than 3 × ATR(14) against the position.
6. **Never** widen a stop, average down, or add to a loser. [Source: L30 #6; EB ch.7 p.72-73]
7. **Adding on a retest** is mentioned as optional. [Source: L25 #6] **[PROPOSED DEFAULT]** Off in version 1.

### 3.0.2 The "Little Yellow Line" (L31): what it means and how the bot uses it

- **Course meaning:** a horizontal line drawn at the price where a **notable market event** happened: a breakout from consolidation, a break of support or resistance, or a big reversal. It is a fixed, objective marker of "where the market made a decisive move". It tells you whether the market is **respecting or deviating from** that level and acts as the reference for entries and exits. Its purpose is to stop emotional decisions like chasing or holding a loser. [Source: L31]
- **It is not** a trailing stop, an indicator, or a breakeven rule. The text gives no color logic and no numbers beyond "draw a line at the event price". The video may add detail I can't see.
- **Bot translation [PROPOSED DEFAULT]:**
  1. When a setup's breakout fires, store `LYL = the broken level` (OR high/low, the key level, the flag trendline value at the break, or the box boundary).
  2. **Retest entry reference:** in retest mode, entries are only allowed when price comes back to within 2 ticks of the LYL and holds it (see Setup B).
  3. **Invalidation:** if a 5-min bar **closes back through the LYL** by more than 25% of R, exit the position. "Market deviated from the decisive level" = the idea failed, even if the hard stop hasn't been hit.
  4. **No-chase rule:** no new entry if price is already more than 1R beyond the LYL (don't chase).
  5. Draw the LYL on Jesus's monitoring chart so he can watch the bot (yellow, as the name says).

---

### Setup A: 15-Minute Opening Range Breakout (ORB)

**Source:** L22, L23, L52 step 10.

1. **Define the range:** high and low of **08:30:00-08:44:59 CT** (9:30-9:45 ET), i.e. the first 15-min candle. Draw both as horizontal lines. [L23]
2. **Long trigger (course):** price breaks above the OR high, with high volume. **Short:** breaks below the OR low. [L23] The course example enters on a breakout at 9:50 ET (08:50 CT) with high volume. [L23 Example 1]
   - **[PROPOSED DEFAULT]** "Break" = a 5-min bar **closes** ≥ 1 tick beyond the OR high/low and passes the volume and strong-candle tests. Only the **first** valid break in each direction per day counts.
3. **Fakeout filter (course):** a break that quickly reverses with a reversal candle is a fakeout. Low breakout volume = possible fakeout. Don't enter; wait for a better signal. [L23 Fakeouts, Example 2]
   - **[PROPOSED DEFAULT]** If price closes back inside the range within 2 bars after the break, cancel the setup for that side.
4. **Entry modes:**
   - **A1 "classic" (L23):** enter at market on the open of the bar after the confirming close. (L25 also allows a buy stop just above the level.)
   - **A2 "retest" (Patrick's own entry, EB ch.10 p.98):** after the break, wait for a pullback to the OR high (the LYL) that **holds**, then enter when price starts moving up again on increasing volume.
   - **[PROPOSED DEFAULT]** Use **A2**. Entry = buy stop 1 tick above the high of the first 5-min bar that touches the LYL (within 2 ticks) and closes back above it. If no retest happens within 6 bars (30 min) of the break, skip (no chasing).
5. **Stop:**
   - A1: just outside the **opposite side** of the range (long: OR low − 2 ticks). [L23 "Place your stop-loss just outside the opposite side of the 15-minute range"]
   - A2: just below the recent (retest) low. [EB ch.10 p.98 "My stop is simple: just below the recent low"] Bot: retest swing low − 2 ticks.
6. **Size:** smaller size when the range is wide, bigger when it's narrow. [L23 Position Sizing] This is handled automatically by the sizing formula in Section 4.
7. **Targets:** L23 gives no target, so the shared rules apply: first target = next key level beyond the OR, and it must be ≥ 2R or the trade is skipped [L29, L32, L35]. Half off at the first target, trail the rest (3.0.1).
8. **Invalidation:** close back inside the range through the LYL (3.0.2), or the fakeout rule above.
9. **Range-bound variant (buy near the OR low, sell near the high when price stays inside "for an extended period")** [L23]: **[PROPOSED DEFAULT]** **disabled**. It conflicts with the e-book's "don't trade the chop" (EB ch.7, ch.10), and the course gives no rules for it.

### Setup B: Key-level breakout and retest (Patrick's core momentum setup)

**Source:** EB ch.10 p.93-98; L24, L25; L12.

1. **The four required elements** (all must be true) [EB ch.10 p.96]:
   1. A clear trend is established (Section 3.0 trend definition plus the direction filter).
   2. Strong volume confirms the move.
   3. A key level is broken (support/resistance, VWAP, pre-market/overnight high or low, prior close, pivot).
   4. A pullback retests the broken level, and it holds.
2. **Which levels:** the key-levels list from 3.0. Patrick's example: the market opens, pushes above the **pre-market high**, pulls back to test it, holds, and moves up again on rising volume = long. [EB ch.10 p.98] He focuses on the **first hour**, especially breaks of the overnight range on strong volume. [EB ch.10 p.98]
   - **[PROPOSED DEFAULT]** Allowed between 08:45 and 09:45 CT. Levels used: overnight high/low, prior-day high/low/close, R1/S1, PP. VWAP counts only on a 5-min close across it plus a successful retest.
3. **Break:** a 5-min strong-candle close beyond the level with high volume (3.0 definitions). [L25 #2]
4. **Entry:** same as A2: buy stop 1 tick above the high of the bar that retests the level (within 2 ticks) and closes back on the breakout side. The retest must start within 6 bars of the break. **[PROPOSED DEFAULT: tolerances]**
5. **Stop:** just below the retest low (−2 ticks). [EB ch.10 p.98; EB ch.10 p.94 "a clear risk level (just below the breakout point)"]
6. **Targets:** first target = next key level ≥ 2R away (take half), trail the rest. [EB ch.10 p.98; L32]
7. **Invalidation:** close back through the LYL (3.0.2); the four elements not all met; or a weak, low-volume break. [EB ch.10 p.94]

### Setup C: Bull flag / bear flag continuation

**Source:** L18, L19 (also L26, L27, L52 step 7).

1. **Pole (course):** a strong, sharp move with high volume. [L18, L19]
   - **[PROPOSED DEFAULT]** Pole = 3-8 consecutive 5-min bars with a net move of at least 2 × ATR(14), and average pole volume ≥ 1.2 × the 20-bar average.
2. **Flag (course):** after the pole, price consolidates in a **downward-sloping or horizontal parallel channel** (bull flag) or an **upward-sloping or horizontal** one (bear flag), on **decreasing volume**. [L18, L19]
   - **[PROPOSED DEFAULT]** Flag = 3-12 bars; retraces no more than 50% of the pole; upper trendline drawn through the flag's two highest swing highs, with slope ≤ 0 (bull); average flag volume < average pole volume. For a bull flag the flag must stay above the 21 EMA (mirror for bear).
   - Note: L18 says flags last "a few days to a few weeks". That describes daily charts. The course also uses flags intraday (L52 step 7), so the bot uses the bar counts above.
3. **Context:** flags work better in trending markets and worse in choppy ones. [L19 Monitor Market Conditions] The bot applies the direction filter and chop filter.
4. **Entry (course):** long when price breaks **above the upper trendline** with increased/high volume. Short when it breaks below the lower trendline. [L18, L19]
   - **[PROPOSED DEFAULT]** A 5-min close ≥ 1 tick above the trendline value at that bar, plus the volume test. Enter at market on the next bar's open.
5. **Stop (course):** bull: below the flag (the flag's low); bear: above the flag's high. [L19 Stop-Loss Orders, Examples] Bot: flag low − 2 ticks.
6. **Target (course):** measure the pole (from the start of the pole to its peak or trough) and project that same distance from the breakout point. [L19 Profit Targets]
   - Bot: first target (half) = the nearer of (next key level, measured move), and the measured move must be ≥ 2R or skip. The rest trails toward the measured move.
7. **Optional confirmation:** RSI / MACD / moving averages. [L19] **[PROPOSED DEFAULT]** Off.
8. **Invalidation:** the flag retraces more than 50% of the pole, a bar closes back below the trendline/LYL after the break, or flag volume rises above pole volume. **[PROPOSED DEFAULT]**

### Setup D: Gap fill

**Source:** L20, L21, L52 step 6.

1. **Gap (course):** a big difference between the previous session's close and the current open. Up gap = open above the previous close; down gap = open below it. It must be "noticeable and not just a minor fluctuation". [L20, L21]
   - Bot: `gap = price at 08:30 CT − prior RTH close (15:00 CT)`.
   - **[PROPOSED DEFAULT]** Trade it only if 0.25 × ATR(14, daily) ≤ \|gap\| ≤ 1.0 × ATR(14, daily).
2. **Context (course):** gaps in **strong trends might not fill**. Higher volume at the gap = more likely to fill. [L21 step 2]
   - **[PROPOSED DEFAULT]** Skip if the gap is in the same direction as the last 3 daily closes (strong trend). Skip if the opening range breaks out **in the gap's direction** with high volume first (that is a Setup A trade instead).
3. **Direction:** up gap → **short** toward the previous close; down gap → **long**. [L21 steps 4-6, Example Scenario]
   - **Contradiction in the course:** L20 says "long position for an up gap or a short position for a down gap". That contradicts its own target (the previous close) and L21. The bot follows **L21**.
4. **Confirmation and entry (course):** wait for signs of reversal or consolidation around the gap (weakness for an up gap, strength for a down gap). Enter when price starts moving toward the fill on increased volume. [L20, L21 step 3-4]
   - **[PROPOSED DEFAULT]** Up gap: after 08:45 CT, a 5-min strong-candle close **below the OR low** (or below VWAP if VWAP is above the OR low) with high volume → short at the next bar's open. Down gap is the mirror. Valid until 09:45 CT.
5. **Stop (course):** "just outside the gap area"; for an up gap, "a stop-loss above the gap". [L21 step 5, Examples] **[PROPOSED DEFAULT]** Up gap: high of the day (since 08:30) + 2 ticks.
6. **Target (course):** the **previous close** (gap origin) is the main target. Use trailing stops as price moves toward the fill. [L20, L21 step 6]
   - Bot: requires ≥ 2R to the previous close, otherwise skip. Half off at the gap midpoint or the first key level in between, whichever is nearer; the rest at the previous close; trail per 3.0.1.
7. **Invalidation:** price moves further away from the fill and makes a new high (up gap) or low (down gap) for the day, which hits the stop; or the opening range breaks out in the gap's direction.

### Setup E: 21 EMA trend pullback (momentum continuation)

**Source:** L15, L12, L29.

1. **Trend (course):** price consistently above the 21 EMA = uptrend, focus on longs; below = downtrend, shorts. Price staying close to the EMA = healthy trend. [L15 #1, #4]
   - **[PROPOSED DEFAULT]** At least 6 of the last 8 5-min closes above EMA21, EMA21 rising (now > 3 bars ago), price above VWAP, and the market-structure uptrend (3.0) all true.
2. **Pullback (course):** in an uptrend the 21 EMA acts as dynamic support. Look for price to **approach the EMA and bounce**. Enter at the end of the corrective wave, as the next impulse begins. [L15 #2; L12 #2]
   - **[PROPOSED DEFAULT]** Pullback = the bar's low comes within 0.25 × ATR of EMA21 and no 5-min close is more than 0.25 × ATR below EMA21. Bounce bar = bullish bar (close > open, close in the top half) closing above EMA21.
3. **Entry [PROPOSED DEFAULT]:** buy stop 1 tick above the bounce bar's high. The bar must have volume above the average of the pullback bars (L29 #3 volume confirmation).
4. **Stop (course):** below the most recent higher low. [L12 #4] Bot: pullback swing low − 2 ticks.
5. **Targets:** first target = next key level (e.g. the prior swing high or a pivot) at ≥ 2R, otherwise skip. Half off there; trail the rest.
6. **Exit signal (course):** price crossing below the 21 EMA in an uptrend (above it in a downtrend) = exit. [L15 #3] Bot: a 5-min close across EMA21 exits the remainder.
7. **Do not enter** when price is overextended from the EMA (3.0). [L15 #1]
8. **EMA crossover** ("a faster EMA crosses the 21 EMA"): the course mentions it but gives no fast period. [L15 #3] **[PROPOSED DEFAULT]** Not used as an entry. Optional 9 EMA filter, off.

### Setup F: Breakout from consolidation (range / rectangle / triangle)

**Source:** L24, L25, L26, L27, L33.

1. **Consolidation (course):** price moves sideways between horizontal support and resistance that it touches without breaking; the range narrows; ATR is low; volume falls or stays flat; moving averages converge and go sideways; Bollinger Bands narrow. Shapes: rectangles and triangles. [L33 #1-5]
   - **[PROPOSED DEFAULT]** Box = at least 6 5-min bars (30 min) where the box height is ≤ 1.5 × ATR(14, measured before the box). At least 2 touches (within 2 ticks) of the top and 2 of the bottom. ATR(14) now is lower than 10 bars ago, and average box volume is below the 20-bar average before the box. Triangles are not used in version 1.
2. **Breakout confirmation (course):** a surge in volume, plus a strong candle that **closes beyond** the level. [L25 #2] Elevated volume = real breakout; watch for false breakouts. [L24, L33]
3. **Entry (course):** a buy stop just above resistance (sell stop just below support), OR a market order after strong confirmation. [L25 #3]
   - **[PROPOSED DEFAULT]** Market order on the next bar's open after a confirming 5-min close. Retest mode (as in A2) is optional.
4. **Stop:** the course gives two answers. "Just below the breakout level" [L25 #4] vs "just outside the consolidation range" [L33 "Tight Stop-Loss Placement"], plus "ATR-based" as an option [L25 #4].
   - **[PROPOSED DEFAULT]** Opposite side of the box ± 2 ticks (that is where the idea is invalidated, per EB ch.11 p.106). If that is more than 2 × ATR, use the box midpoint instead.
5. **Target (course):** measured move: the box height projected from the breakout point; or the next support/resistance. [L25 #5, L27 #4] Bot: requires the measured move ≥ 2R; first target = nearer of (key level, measured move); trail the rest.
6. **Management:** if volume drops after the break, the breakout is losing steam (L25 #6). Bot: momentum-fade exit (3.0.1 #3).
7. **Invalidation:** close back inside the box through the LYL.
8. **Not automated (mentioned only):** trading inside the range (buy support / sell resistance) [L33], "fading the breakout" [EB ch.9 p.83], and reversal patterns such as head and shoulders and double tops/bottoms [L26, L27]. The course gives no exact rules for these, and the e-book says reversal trading is "a sucker's game" [EB ch.10 p.91]. **[PROPOSED DEFAULT]** Disabled.

### 3.7 Setup priority and conflicts [PROPOSED DEFAULT]

1. Hold **one position at a time**, in one instrument. No new setup while a trade is open.
2. If two setups trigger on the same bar, take them in this order: A (ORB) > B (key-level retest) > C (flag) > F (consolidation) > E (21 EMA) > D (gap fill).
3. A gap fill (D) is only allowed in the direction of the first ORB break, or before any ORB break.
4. After a full stop-out, no new trade in the same direction for 2 bars (10 min). This guards against revenge trading [L40; EB ch.7 p.71].

---

## 4. Risk and sizing

### 4.1 What the course actually says

1. Risk a small percentage per trade, **1-2% of the account**. [L35 #2, L42 #4] Keep the same risk percentage as the account grows (e.g. 1%). [L36 #3]
2. Reward-to-risk of at least **2:1** (1:2 or 1:3 in L35). [L29 #8, L32, L35 #3, L44 #5; EB ch.11 p.104]
3. Have a **daily loss limit**. Stop trading for the day when you hit it. [L17, L35 #9, L42 #5] E-book example: on a $5,000 account the max daily loss is **$500 (10%), "and trust me, that's pushing it"**, chosen so that "even if you hit three red days in a row, you're still in the game". [EB ch.11 p.104]
4. **Daily profit goal:** "If you're risking $500 on those red days, you better be gunning for $1,000 profit on the green ones" (2:1 at the daily level). [EB ch.11 p.104] The course has **no rule to stop trading** after hitting a profit target.
5. **Contract sizing:** start small, e.g. **one contract**, and move to two "after a series of profitable trades". Start with micros. [L36 #1; EB ch.5 p.55] A wider stop means fewer contracts; never a tighter stop. [L23; EB ch.11 p.106]
6. **Max trades per day:** the course gives **no number**. It only says avoid overtrading; quality over quantity. [L35 #8, L39, EB ch.7 p.71]

### 4.2 Why the course's 1-2% needs adapting for a Topstep Combine

On a $50K Combine, 1-2% of the nominal balance is $500-$1,000 per trade. But the account fails at a **$2,000 loss** (the Maximum Loss Limit, see Section 7). At that size, 2-4 losing trades end the Combine. The real "risk capital" is the MLL, not the $50K. So the defaults below scale from the MLL. That follows the e-book's logic (survive several red days in a row) instead of the literal 1-2%.

### 4.3 Bot risk rules [PROPOSED DEFAULT unless cited]

| Rule | 50K Combine | 100K Combine | 150K Combine | Basis |
|---|---|---|---|---|
| Topstep Maximum Loss Limit (trailing) | $2,000 | $3,000 | $4,500 | **[TOPSTEP]** |
| Topstep optional Daily Loss Limit (if chosen at purchase) | $1,000 | $2,000 | $3,000 | **[TOPSTEP]** |
| Topstep max position (minis / micros) | 5 / 50 | 10 / 100 | 15 / 150 | **[TOPSTEP]** |
| Bot risk per trade (10% of MLL) | $200 | $300 | $450 | PROPOSED |
| Bot daily max loss (2 full losses, 20% of MLL; realized + open P&L) | $400 | $600 | $900 | PROPOSED (e-book: survive many red days) |
| Bot daily profit stop (2 × daily max loss) | $800 | $1,200 | $1,800 | PROPOSED, from EB ch.11 p.104's 2:1 day; also keeps the best day under Topstep's 55%-of-Profit-Target consistency line (check against your Combine's target) |
| Max trades per day | 3 | 3 | 3 | PROPOSED |
| Stop after consecutive losses | 2 | 2 | 2 | PROPOSED (L38/L40 revenge-trading warning) |
| Starting contract cap | 2 micros | 2 micros | 2 micros | PROPOSED (2 = the smallest size that allows the course's "close half"; L36 says start with 1) |
| Scale-up | +1 micro after 20 trades with profit factor ≥ 1.3; −1 after 5 losing days in 10 | same | same | PROPOSED version of L36 "after a series of profitable trades" |
| MLL buffer | If equity is within 3 × risk-per-trade of the MLL, halve risk; within 1 × risk, stop trading | same | same | PROPOSED (Topstep advises leaving a buffer) |

### 4.4 Contract sizing formula

```
stop_ticks   = |entry − stop| / tick_size
cost_per_ct  = stop_ticks × tick_value + slippage_ticks × tick_value + round_turn_fees
contracts    = floor(risk_per_trade / cost_per_ct)
contracts    = min(contracts, contract_cap, Topstep max position)
if contracts < 1 → skip trade (stop too wide for the risk budget)
```

- **[PROPOSED DEFAULT]** slippage_ticks = 1. Set round_turn_fees to Jesus's actual TopstepX fees per contract (not given in the course; I didn't verify them).
- With 1 contract (no partial possible), exit everything at the first target.
- **Daily-loss guard:** before each entry, `realized_PnL_today + open_risk + new_trade_risk` must stay above −(bot daily max loss). Otherwise skip.

### 4.5 Testing before risking a Combine (course)

1. Backtest on historical minute data with precise rules, and include commissions, slippage and spreads. Measure win rate, average P/L, max drawdown, and reward:risk. Avoid overfitting. [L44]
2. Forward test in a demo/sim account with live data, then small size. [L43, L44 #7]
3. Paper trade the strategy for **at least 100 trades** before going live. [EB ch.10 p.99] Commit to **30 days** of strict rule-following. [EB ch.13 p.122]
4. Keep a journal of every trade: setup, reason for entry and exit, outcome. [L42 #8; EB ch.10 p.99] The bot logs this automatically (Section 5).

---

## 5. Pre-market routine (10-Step Morning Game Plan, L52) → bot vs Jesus

| # | Course step [L52] | Bot does automatically | Jesus checks by hand |
|---|---|---|---|
| 1 | Morning news check. Release times 8:30, 9:45, 10:00 ET (07:30, 08:45, 09:00 CT); know high- vs low-impact | Loads today's blackout times from a config file; enforces flatten and no-entry windows (2.3) | Opens an economic calendar each morning, marks high-impact events (and FOMC/CPI/NFP days) in the config. **The ProjectX API does not provide an economic calendar** |
| 2 | Overnight moves (Asia from 2 am ET, London 3 am ET = 01:00/02:00 CT); overnight high/low | Computes overnight high/low (17:00-08:30 CT), the London-session high/low from 02:00 CT, and the overnight range size | Looks for unusual overnight news or a big move |
| 3 | Daily chart: trend, inside the previous day's range?, last 2-3 days' highs/lows | Computes prior-day H/L/C, inside-day flag, 3-day highs/lows, daily trend (HH/HL) | Sanity-checks the daily picture |
| 4 | Check ES, NQ, YM, DXY: do they agree? | Pulls ES/NQ/YM bars (if available on his TopstepX data) and sets an "agreement" flag | Checks **DXY** by hand (it's an ICE index, likely not in TopstepX data) |
| 5 | Pivot points and VWAP on the 15-min chart | Computes PP/R1-3/S1-3 and live VWAP | — |
| 6 | Gaps vs the previous day's close | Computes the gap at 08:30 CT and its size vs daily ATR, and enables/disables Setup D | — |
| 7 | 5-min, 15-min, 1-hour charts; spot emerging bull/bear flags | Computes trend state on all three timeframes; scans for flags | Glances at the charts |
| 8 | Strength or weakness; resistance/support from pivots, hourly highs/lows, overnight high/low | Builds the sorted key-level map (3.0) used for targets | Reviews the level map |
| 9 | Prepare a bullish AND a bearish scenario | Prints a one-page plan: "Above X → long setups A/B toward Y; below Z → short setups toward W" | **Approves the plan** (bot stays disarmed until he clicks "arm") **[PROPOSED DEFAULT]** |
| 10 | First 15-min candle range | Records OR high/low at 08:45 CT; arms Setup A | — |
| + | Not in L52 but in the course/e-book: mindset and "know when not to trade" [EB ch.13 p.117] | — | Go/no-go on personal state; confirms the right account and size; keeps the kill switch in reach and **stays at the PC during the window** |

**Bot logs** (journal, L42 #8): for every signal, taken or skipped, record the timestamp, setup, levels, volume ratio, R, size, exit reason, P&L, and which filter blocked it.

---

## 6. Gaps: where the course is vague, discretionary, or video-only

Every value below is **MY PROPOSED DEFAULT**. None of it is from the course.

| # | Gap in the course | Where | Proposed default |
|---|---|---|---|
| 1 | Exact "2-hour" window not defined | L1, EB cover | 08:30-10:30 CT; entries 08:45-10:15 |
| 2 | Can you trade before the opening range finishes? | L52 #10 ("patience") | No entries before 08:45 CT |
| 3 | Which instrument to trade | L23, L52 #4 | MES only; ES/NQ as filters |
| 4 | Signal timeframe for each setup | L15, L19, L25 | 5-min bars |
| 5 | "High volume" has no number | L19, L23, L25, L29 | ≥ 1.5 × 20-bar average volume |
| 6 | "Strong candle" has no number | L25 | Body ≥ 60% of range, closes ≥ 1 tick beyond the level, in the top 30% of the bar |
| 7 | Breakout = touch or close? | L23, L25 | 5-min close beyond the level |
| 8 | ORB stop (opposite side) vs Patrick's retest stop | L23 vs EB ch.10 | Retest mode (A2) by default |
| 9 | Retest tolerance and timeout | EB ch.10 | Within 2 ticks; within 6 bars |
| 10 | Stop buffer ("just" beyond) | L23, L25, L33 | 2 ticks |
| 11 | Breakout stop: "below the breakout level" vs "outside the range" | L25 vs L33 | Opposite side of the box; box midpoint if > 2 ATR |
| 12 | Flag pole and flag measurements | L18, L19 | Pole ≥ 2 ATR in 3-8 bars; flag 3-12 bars, ≤ 50% retrace |
| 13 | Flags described as lasting "days to weeks" | L18 | Intraday bar counts as in #12 |
| 14 | "Significant" gap size | L20, L21 | 0.25-1.0 × daily ATR(14) |
| 15 | What "previous close" is for 24-hour futures | L21 | Last price at 15:00 CT |
| 16 | Gap direction contradiction | L20 vs L21 | Follow L21 (fade the gap toward the previous close) |
| 17 | Gap "confirmation" undefined | L21 | 5-min close through the OR low/high or VWAP, with volume |
| 18 | "Strong trend" (gap may not fill) undefined | L21 | Last 3 daily closes in the gap direction |
| 19 | 21 EMA timeframe; "close to the EMA"; "overextended" | L15 | 5-min; within 0.25 ATR; > 2 ATR = overextended |
| 20 | Fast EMA for the crossover | L15 | 9 EMA, off |
| 21 | Consolidation length and tightness | L33 | ≥ 6 bars, height ≤ 1.5 ATR, 2+ touches each side |
| 22 | Pivot S/R formulas | L14 | Classic floor pivots |
| 23 | VWAP anchor time | L14 | 17:00 CT session start |
| 24 | LuxAlgo Price Action Concepts can't be coded | L14 | 2-bar fractal swing highs/lows |
| 25 | RSI/MACD/ATR/Bollinger settings | L19, L25, L33, EB ch.10 | RSI 14, MACD 12/26/9, ATR 14, BB 20/2 |
| 26 | Profit target for ORB/breakout/EMA trades | L23, L25, L15 | Next key level, must be ≥ 2R |
| 27 | Partial size and level | L30, L32 | 50% at the first target |
| 28 | Trailing stop method | L17, L30, L32 | Breakeven after T1, then trail behind 5-min swing lows; exit on a close across EMA21 |
| 29 | Move to breakeven? (never mentioned) | — | Yes, only after the first target fills |
| 30 | Time-based exit duration | L32 | 45 min without T1 → exit; flat at 10:30 |
| 31 | ATR "volatility surge" exit | L32 | Bar range > 3 ATR against you |
| 32 | Momentum-fade exit | EB ch.10 | 3 bars with no new high + below-average volume |
| 33 | Little Yellow Line: exact use (text only; the video may add detail) | L31 | Stored break level: retest reference, invalidation on a close back through it, no-chase rule |
| 34 | News blackout length; which events count | L52, L29 | Flatten 2 min before, no entries until 10 min after high-impact releases |
| 35 | Chop definition | EB ch.7, L33 | ≥ 4 EMA21 crosses in 12 bars, or flat EMA slope |
| 36 | Index agreement rule | L52 #4 | ES and NQ on the same side of VWAP |
| 37 | Per-trade $ risk in a prop account | L35 (1-2%) | 10% of MLL (Section 4.3) |
| 38 | Daily max loss amount | L35, EB ch.11 | 20% of MLL |
| 39 | Daily profit stop (none in the course) | EB ch.11 | 2 × daily max loss |
| 40 | Max trades per day (none) | L35, L39 | 3; stop after 2 losses in a row |
| 41 | Starting size / when to scale | L36 | 2 micros; +1 after 20 trades with PF ≥ 1.3 |
| 42 | Range-bound trading, fading breakouts, reversal patterns | L23, L33, EB ch.9, L27 | Disabled |
| 43 | Setup conflicts and priority | — | Section 3.7 |
| 44 | Holidays, FOMC days, roll week, data outages | — | No trading; flatten and halt on data loss |
| 45 | Ch.10 live trade breakdowns are video-only | L45-L51 | Not used. Jesus should watch them and note any rule they contradict |

### Top 5 questions for Jesus to finalize the rules

1. **Which Combine size and instrument?** 50K/100K/150K, and MES or MNQ (or minis)? Did you add Topstep's Daily Loss Limit at purchase? Every dollar number in Section 4 depends on this.
2. **How much risk per trade and per day?** Are you OK with my $200/trade and $400/day defaults (for 50K), or do you want the course's literal 1-2% ($500-$1,000 per trade), which is much more aggressive against a $2,000 MLL?
3. **Which setups go in version 1?** I suggest starting with A (ORB, retest mode) and B (key-level retest) only, and adding C/D/E/F after each passes its own backtest. Do you agree, and which entry mode do you prefer: classic breakout or retest?
4. **Exact window and news handling:** is 08:30-10:30 CT right for your schedule? On CPI/NFP/FOMC days, do you want the bot off, trading only after the opening range, or trading normally?
5. **Exits:** should the bot take 50% at the first target and trail the rest (needs 2+ contracts), or take everything at a single 2R target? And do you want the breakeven move after T1?

---

## 7. Fitting Topstep (checked Oct 7, 2026 on help.topstep.com)

1. **Bots are allowed through the TopstepX/ProjectX API** in eligible (simulated) accounts, but **high-frequency trading is prohibited**. You own the bot and are solely responsible for its behavior. [TopstepX API Access: https://help.topstep.com/en/articles/11187768-topstepx-api-access]
2. **No VPS, VPN or remote servers for trading.** All trading must originate from **your personal device**. A private server may only store data, backtest, log, or show a read-only dashboard; it may never place, modify or cancel orders. [same page]
3. **Live Funded Accounts cannot trade through the ProjectX API.** The API gateway is built for the simulated environment and isn't available on Live, so this bot is for the Practice account, the Trading Combine, and (per the same rules) the Express Funded Account only. [same page; Live Funded Account Parameters: https://help.topstep.com/en/articles/10657969-live-funded-account-parameters]
4. **Supervise the bot.** Topstep "won't help set up or troubleshoot automated strategies, and no exceptions are made for errant trades or malfunctions". Test on the **Practice account** first (there is no API sandbox). [Trading Combine Parameters: https://help.topstep.com/en/articles/8284197-trading-combine-parameters; API page FAQ] Jesus should be at the PC during the whole 08:30-10:30 CT window with a kill switch (flatten + cancel all).
5. **Maximum Loss Limit (trailing, real-time, includes unrealized P&L):** $2,000 (50K), $3,000 (100K), $4,500 (150K). It trails up with your end-of-day balance and never moves down. Touching it liquidates immediately. [https://help.topstep.com/en/articles/8284204-what-is-the-maximum-loss-limit] The bot's daily max loss must always sit well inside this.
6. **Daily Loss Limit** is optional in the Combine: $1,000 / $2,000 / $3,000 if chosen at purchase. You can also set a Personal Daily Loss Limit in Risk Settings. Hitting it flattens you for the rest of the session. **[PROPOSED DEFAULT]** Also set a TopstepX Personal DLL equal to the bot's daily max loss ("Liquidate and Block") as a backstop if the bot fails. [https://help.topstep.com/en/articles/10490293-daily-loss-limit-in-the-trading-combine-and-express-funded-account]
7. **Max position size:** 5/10/15 minis (50/100/150 micros) for 50K/100K/150K. **Consistency target:** keep your best day below 55% of your Profit Target. The **trading day** runs 5:00 PM CT to 3:10 PM CT. [Trading Combine Parameters page]
8. **Prohibited strategies that matter for a bot:** trading your full max position into a major scheduled news event; using software or ultra-high-speed systems for an unfair advantage; exploiting SIM fills (hundreds of rapid trades, scalping algos, or "using tight brackets or auto-breakeven to take advantage of favorable SIM fills"). [https://help.topstep.com/en/articles/10305426-prohibited-trading-strategies-at-topstep] This rulebook's ≤ 3 trades/day, multi-minute holds, structure-based stops, and news blackouts are designed to stay well clear of these. The breakeven move after T1 is ordinary trade management, not a tight scalping bracket, but Jesus should be aware of the wording.
9. Topstep rules change. Re-check these pages before each new account stage.

*Educational material only, not financial advice.*
