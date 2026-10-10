# MES research: when it moves, and which simple rules survive

Do not trade it, and do not change the live bot. `fh_15bp_1.5R` is the only rule that cleared the accounting gates. It made $1.07 per trade in train (715 trades, net $765.06), $12.95 in validation (171 trades, net $2,213.66), and $16.93 on the one holdout pass (135 trades, net $2,285.07, win rate 54.1%). The validation bootstrap p-value is 0.151 before the penalty and 1.000 after multiplying by the 63 tests. The cutoff written down before the run was an adjusted p-value of 0.10. A positive dollar total that does not clear that bar is not an edge. The train result is about one dollar a trade, which is smaller than one tick, and the later windows are a jump rather than the same result repeating.

This is a research note, not a recommendation to place a live order and not financial advice. The live bot's settings were not changed.

## Costs, hours, and the Combine

TopstepX lists the Micro E-mini S&P 500 round turn at **$1.22** ($0.50 commission, $0.70 exchange, $0.02 NFA), read from https://help.topstep.com/en/articles/8284213-topstepx-commissions-and-fees on 2026-10-10. The October 2026 CME increase on that page is MCL and MNG, not MES. A market or stop order also pays 1 tick against the trader on the way in and, if it exits by stop or by a flatten, 1 tick on the way out. That is **$3.72** all-in for a market round turn on one MES. A limit that is filled only when price trades through it pays no entry slippage. The live config still says $1.40. This study does not edit that file.

The account rules used here are the 50K Combine: $3,000 profit target, $1,000 daily loss limit, $2,000 end-of-day trailing max loss, at most 50 MES, and flat by 3:10 PM CT. Passing also requires the best day to be under half of the profits. The later payout path uses 40% instead of 50%; that stricter test is reported beside the Combine result, and it is not what decides a pass. Size in this study risks $200 a trade, skips the trade if one contract cannot fit, takes at most two trades a day, and stops opening new trades once the day is down $400. MNQ and M2K were not tested. There is no second data file, and adding one would not have been a small side check.

## The rules were written down first

The families, the parameter lists, the train/validation/holdout dates, and the pass rules live in `research/strategies.py`. The same text is saved to `docs/research/predeclared_grid.txt` at the start of the run, before the bars are scored. There are **63** eligible configurations and **66** rows once the three benchmarks are included. The 100 extra random seeds are a null distribution. They are not 100 more chances to crown a winner.

```text
Predeclared MES research grid. Written from research/strategies.py before any backtest result is used.

Splits: train is 2019-05-05 through 2023-12-31. Validation is 2024-01-01 through 2025-06-30. Holdout is 2025-07-01 through the end of the file. The holdout is scored only for rules that already qualified on train and validation, and it is not used to pick a replacement.

Costs: TopstepX MES round turn is $1.22 (commission $0.50 + exchange $0.70 + NFA $0.02). Market and stop orders pay 1 tick ($1.25) of adverse slippage per side. Limit orders pay none, and only if price trades through the limit. A market entry sized off a stop therefore includes 2 ticks plus the fee. A limit entry includes 1 tick (the stop or the flatten) plus the fee. The live bot's $1.40 placeholder is not changed.

Risk used only in this study: $200 per trade, at most 50 MES, at most 2 trades a day, no new trade after the day's closed P&L or worst mark is -$400 or worse, flat by 15:10 CT, one position at a time, cash session only. Overnight drift is described and not traded.

Selection, fixed before the run: train needs at least 100 trades, positive expectancy, and positive net. Validation needs at least 40 trades, positive expectancy, positive net, a win rate within 15 percentage points of train, and expectancy at least 25% of train. A neighbor is the same family with exactly one parameter changed to the next value on that parameter's list. If more than half of the existing neighbors have validation expectancy at or below zero, the rule is dropped. Remaining rules are ranked by validation expectancy times the square root of validation trades. Keep up to 3 with pairwise validation daily correlation under 0.5. Benchmarks cannot be finalists.

Multiple testing: N is the number of eligible configurations, not the benchmarks and not the 100 random seeds. Adjusted p = min(1, raw bootstrap p × N). The bootstrap resamples validation daily P&L 2000 times with seed 12345. A sign-flip check uses the same seed on its own generator. The random null uses seeds 1000 through 1099, train and validation only, and is not a menu to pick from. A finalist with adjusted bootstrap p above 0.10 is not a trading recommendation.

Combine, scored separately inside each split so later years do not leak backward: start $50,000, pass at +$3,000 if the best day is under half of profits, fail on a $1,000 intraday daily loss or an end-of-day trailing $2,000 max loss. The floor is min($50,000, high-water mark − $2,000) and only moves up. Attempts start on the first session of each month. Unfinished attempts count as not passed. The 40% consistency figure is the later payout path, not the Combine pass test.

Eligible configurations: 63. Total including 3 benchmarks: 66.

| name | family | parameters | eligible |
|---|---|---|---|
| orb_5_opposite_1R | orb | or_minutes=5, stop=opposite, target=1R | True |
| orb_5_opposite_2R | orb | or_minutes=5, stop=opposite, target=2R | True |
| orb_5_opposite_time | orb | or_minutes=5, stop=opposite, target=time | True |
| orb_5_mid_1R | orb | or_minutes=5, stop=mid, target=1R | True |
| orb_5_mid_2R | orb | or_minutes=5, stop=mid, target=2R | True |
| orb_5_mid_time | orb | or_minutes=5, stop=mid, target=time | True |
| orb_5_atr_1R | orb | or_minutes=5, stop=atr, target=1R | True |
| orb_5_atr_2R | orb | or_minutes=5, stop=atr, target=2R | True |
| orb_5_atr_time | orb | or_minutes=5, stop=atr, target=time | True |
| orb_15_opposite_1R | orb | or_minutes=15, stop=opposite, target=1R | True |
| orb_15_opposite_2R | orb | or_minutes=15, stop=opposite, target=2R | True |
| orb_15_opposite_time | orb | or_minutes=15, stop=opposite, target=time | True |
| orb_15_mid_1R | orb | or_minutes=15, stop=mid, target=1R | True |
| orb_15_mid_2R | orb | or_minutes=15, stop=mid, target=2R | True |
| orb_15_mid_time | orb | or_minutes=15, stop=mid, target=time | True |
| orb_15_atr_1R | orb | or_minutes=15, stop=atr, target=1R | True |
| orb_15_atr_2R | orb | or_minutes=15, stop=atr, target=2R | True |
| orb_15_atr_time | orb | or_minutes=15, stop=atr, target=time | True |
| orb_30_opposite_1R | orb | or_minutes=30, stop=opposite, target=1R | True |
| orb_30_opposite_2R | orb | or_minutes=30, stop=opposite, target=2R | True |
| orb_30_opposite_time | orb | or_minutes=30, stop=opposite, target=time | True |
| orb_30_mid_1R | orb | or_minutes=30, stop=mid, target=1R | True |
| orb_30_mid_2R | orb | or_minutes=30, stop=mid, target=2R | True |
| orb_30_mid_time | orb | or_minutes=30, stop=mid, target=time | True |
| orb_30_atr_1R | orb | or_minutes=30, stop=atr, target=1R | True |
| orb_30_atr_2R | orb | or_minutes=30, stop=atr, target=2R | True |
| orb_30_atr_time | orb | or_minutes=30, stop=atr, target=time | True |
| gap_fade_10bp | gap | mode=fade, threshold=0.001 | True |
| gap_fade_25bp | gap | mode=fade, threshold=0.0025 | True |
| gap_go_10bp | gap | mode=go, threshold=0.001 | True |
| gap_go_25bp | gap | mode=go, threshold=0.0025 | True |
| vwap_mr_0830_1030_1 | vwap_mr | window=(510, 630), band=1.0 | True |
| vwap_mr_0830_1030_1.5 | vwap_mr | window=(510, 630), band=1.5 | True |
| vwap_mr_1030_1200_1 | vwap_mr | window=(630, 720), band=1.0 | True |
| vwap_mr_1030_1200_1.5 | vwap_mr | window=(630, 720), band=1.5 | True |
| vwap_mr_1200_1400_1 | vwap_mr | window=(720, 840), band=1.0 | True |
| vwap_mr_1200_1400_1.5 | vwap_mr | window=(720, 840), band=1.5 | True |
| vwap_pb_0845_1100_1.5R | vwap_pb | window=(525, 660), multiple=1.5 | True |
| vwap_pb_0845_1100_2R | vwap_pb | window=(525, 660), multiple=2.0 | True |
| vwap_pb_0930_1200_1.5R | vwap_pb | window=(570, 720), multiple=1.5 | True |
| vwap_pb_0930_1200_2R | vwap_pb | window=(570, 720), multiple=2.0 | True |
| pd_fade | prior | variant=fade | True |
| pd_break_1R | prior | variant=break_1R | True |
| pd_break_2R | prior | variant=break_2R | True |
| on_reclaim_mid | overnight | target=mid | True |
| on_reclaim_1.5R | overnight | target=1.5R | True |
| fh_15bp_1.5R | first_hour | threshold=0.0015, exit=1.5R | True |
| fh_15bp_time | first_hour | threshold=0.0015, exit=time | True |
| fh_25bp_1.5R | first_hour | threshold=0.0025, exit=1.5R | True |
| fh_25bp_time | first_hour | threshold=0.0025, exit=time | True |
| late_1R | late | exit=1R | True |
| late_time | late | exit=time | True |
| tod_0830_0930_long | tod | window=(510, 570), side=long | True |
| tod_0830_0930_short | tod | window=(510, 570), side=short | True |
| tod_0930_1030_long | tod | window=(570, 630), side=long | True |
| tod_0930_1030_short | tod | window=(570, 630), side=short | True |
| tod_1000_1130_long | tod | window=(600, 690), side=long | True |
| tod_1000_1130_short | tod | window=(600, 690), side=short | True |
| tod_1330_1500_long | tod | window=(810, 900), side=long | True |
| tod_1330_1500_short | tod | window=(810, 900), side=short | True |
| tod_1400_1500_long | tod | window=(840, 900), side=long | True |
| tod_1400_1500_short | tod | window=(840, 900), side=short | True |
| trend_vwap_ema | trend | rule=vwap_ema_20 | True |
| bench_bh | bench | kind=bh | False |
| bench_bh_stop | bench | kind=bh_stop | False |
| bench_random | bench | kind=random, seed=7 | False |
```

## The sample

The file is unadjusted Databento `MES.v.0` 1-minute bars, 2019-05-06 through 2026-09-30: 2,610,341 bars, 1,912 cash sessions that contain a bar between 8:30 and 3:10 CT, 30 contract changes, 31 outright labels. Abbreviated sessions with no cash-hours bar are left out. A roll is a real gap, so each cash session is cut to the outright that is trading at 8:30 CT. Prior-day high, low, and close carry forward only when yesterday's cash close was the same outright. Friday evening and Sunday evening both belong to Monday's overnight. Prices are not back-adjusted.

| split | sessions | from | through |
|---|---:|---|---|
| train | 1203 | 2019-05-06 | 2023-12-29 |
| validation | 385 | 2024-01-02 | 2025-06-30 |
| holdout | 324 | 2025-07-01 | 2026-09-30 |

Train is for discovery. Validation has to confirm the same kind of result. The holdout was not used to choose the grid, to drop a neighbor, or to pick the finalists.

## What time of day MES actually moves

The useful clock is 8:30–10:00 CT. That is when the range is widest (about 15–17 points in a typical half hour). The worst clock is 11:30–1:00 CT, when the median range falls to about 9–10 points and the average move is about zero. A variance ratio near 1.5 at 2:00 PM CT says the next half hour tends to extend the last one, and Monday's cash session averages about 5 points up. Neither of those is a strategy. The predeclared "be long" and "be short" rules for those clocks lost money after costs. The only rule that kept a positive average, first-hour momentum, is discussed below and is not significant.

Across the whole file, the cash session from 8:30 to 15:00 CT has a mean move of 0.78 pts ($4 on 1 MES) and a median of 2.50 pts ($12 on 1 MES). The median absolute move in the first hour is 10.00 pts ($50 on 1 MES), against 15.50 pts ($78 on 1 MES) for the rest of the session. The median range is 23.00 pts ($115 on 1 MES) in the first hour and 36.50 pts ($182 on 1 MES) after 9:30. The first hour is the noisy part of the day. The rest of the session is usually a smaller net move inside a still-wide range, which is a bad trade if the stop has to sit outside that range.

Among 30-minute cash-session buckets with at least 200 days, the widest median range is **08:30** at 16.75 pts ($84 on 1 MES). The quietest is **12:30** at 9.00 pts ($45 on 1 MES). The typical bucket's median range is about 10.00 pts ($50 on 1 MES). A round turn costs about 0.74 points, so the fee is not what makes a 30-minute bar untradable. A quiet bucket is still the worst place to trade, because a stop wide enough to survive the noise is a large piece of a small move, and a tight stop gets tagged by that noise.

Where the range is at least average, **14:00** is the bucket whose next half-hour behaves most like a continuation (variance ratio 1.52). **10:00** is the one that behaves most like a fade (variance ratio 0.85). Pooled across adjacent cash-session buckets, the lag-1 autocorrelation is 0.008 and the variance ratio is 1.00. A variance ratio above 1 says the next half-hour tends to extend this one. Below 1, it tends to give the move back. These are descriptions of the path, not a license to trade the single best bucket. The time-of-day rules in the grid were listed before this picture was drawn, including both directions, so the clock could not be picked after the fact.

Scheduled US data is usually 8:30 ET, which is **7:30 CT**, half an hour before the cash open. 8:30 CT is the stock open (9:30 ET), not the data print. 9:00 CT is 10:00 ET. 1:00 PM CT is 2:00 PM ET. 07:30 median range 8.50 pts ($42 on 1 MES), median absolute move 3.50 pts ($18 on 1 MES), mean signed move 0.10 pts ($1 on 1 MES). 08:00 median range 7.50 pts ($38 on 1 MES), median absolute move 2.75 pts ($14 on 1 MES), mean signed move 0.02 pts ($0 on 1 MES). 08:30 median range 16.75 pts ($84 on 1 MES), median absolute move 7.25 pts ($36 on 1 MES), mean signed move 0.37 pts ($2 on 1 MES). 09:00 median range 15.00 pts ($75 on 1 MES), median absolute move 6.25 pts ($31 on 1 MES), mean signed move -0.02 pts ($0 on 1 MES). 13:00 median range 9.75 pts ($49 on 1 MES), median absolute move 4.25 pts ($21 on 1 MES), mean signed move -0.07 pts ($0 on 1 MES).

The same open looks different across regimes.

| regime | days | mean cash-session move | median first-hour absolute move | median 8:30 bucket range |
|---|---:|---:|---:|---:|
| all | 1912 | 0.78 pts ($4 on 1 MES) | 10.00 pts ($50 on 1 MES) | 16.75 pts ($84 on 1 MES) |
| 2020 covid | 106 | -0.14 pts ($-1 on 1 MES) | 12.25 pts ($61 on 1 MES) | 21.75 pts ($109 on 1 MES) |
| 2022 bear | 258 | -0.90 pts ($-4 on 1 MES) | 15.38 pts ($77 on 1 MES) | 27.00 pts ($135 on 1 MES) |
| 2023-2026 | 966 | 1.31 pts ($7 on 1 MES) | 10.75 pts ($54 on 1 MES) | 17.75 pts ($89 on 1 MES) |

Covid and the 2022 bear market were wider. A rule fit to those ranges will look busy and then go quiet, which is why a 2024–2025 confirmation window exists.

The overnight gap, from the prior 15:00 close to the 8:30 open on the same outright, averages 1.66 pts ($8 on 1 MES) (median 2.75 pts ($14 on 1 MES), 1879 days). The cash-session mean is 0.78 pts ($4 on 1 MES), about half of that. A Topstep day account cannot hold the overnight piece, so a strategy that needs it is not available here. After a gap up, the next 30 minutes average -0.04 pts ($0 on 1 MES) and continue higher 50.4% of the time (1029 days). After a gap down, the next 30 minutes average 0.84 pts ($4 on 1 MES) and continue lower 44.7% of the time (839 days). A continuation rate near 50% means the open does not reliably follow the gap. Gap-fade made money only in the later window and lost money in train, so it was rejected.

![Median 30-minute range from 7:30 to 15:00 CT](research/time_of_day.png)

Thirty-minute buckets with at least 100 days. A bucket is dropped inside a day when it has fewer than 20 one-minute bars, which removes partial prints around a roll. Range is the high minus the low. The variance ratio uses that bucket and the next one: variance of the two-bucket sum, divided by twice the variance of the first bucket.

| clock | phase | days | mean move | median abs move | median range | autocorr | variance ratio |
|---|---|---:|---:|---:|---:|---:|---:|
| 17:00 | overnight | 1881 | -0.00 | 2.50 | 6.50 | — | — |
| 17:30 | overnight | 1881 | 0.13 | 1.50 | 4.00 | — | — |
| 18:00 | overnight | 1884 | -0.05 | 1.75 | 4.50 | — | — |
| 18:30 | overnight | 1887 | 0.04 | 1.75 | 4.25 | — | — |
| 19:00 | overnight | 1911 | 0.11 | 2.25 | 5.75 | — | — |
| 19:30 | overnight | 1911 | 0.01 | 1.75 | 4.75 | — | — |
| 20:00 | overnight | 1911 | -0.03 | 1.75 | 4.75 | — | — |
| 20:30 | overnight | 1910 | 0.14 | 2.00 | 4.75 | — | — |
| 21:00 | overnight | 1910 | -0.08 | 1.50 | 4.00 | — | — |
| 21:30 | overnight | 1909 | -0.12 | 1.25 | 3.50 | — | — |
| 22:00 | overnight | 1901 | 0.23 | 1.25 | 3.50 | — | — |
| 22:30 | overnight | 1908 | -0.11 | 1.25 | 3.00 | — | — |
| 23:00 | overnight | 1904 | -0.06 | 1.25 | 3.00 | — | — |
| 23:30 | overnight | 1905 | 0.13 | 1.25 | 3.25 | — | — |
| 00:00 | overnight | 1904 | -0.03 | 1.50 | 4.00 | — | — |
| 00:30 | overnight | 1905 | 0.14 | 1.75 | 4.25 | — | — |
| 01:00 | overnight | 1908 | 0.18 | 2.25 | 5.50 | — | — |
| 01:30 | overnight | 1908 | 0.03 | 2.00 | 4.75 | — | — |
| 02:00 | overnight | 1907 | 0.07 | 3.50 | 7.75 | — | — |
| 02:30 | overnight | 1906 | 0.10 | 2.75 | 6.50 | — | — |
| 03:00 | overnight | 1907 | 0.16 | 3.00 | 7.00 | — | — |
| 03:30 | overnight | 1907 | -0.06 | 2.25 | 5.75 | — | — |
| 04:00 | overnight | 1908 | 0.02 | 2.25 | 5.50 | — | — |
| 04:30 | overnight | 1907 | 0.07 | 2.00 | 5.25 | — | — |
| 05:00 | overnight | 1907 | -0.11 | 2.00 | 5.50 | — | — |
| 05:30 | overnight | 1905 | 0.28 | 2.25 | 5.50 | — | — |
| 06:00 | overnight | 1907 | 0.10 | 2.50 | 6.00 | — | — |
| 06:30 | overnight | 1908 | -0.23 | 2.25 | 5.75 | — | — |
| 07:00 | overnight | 1907 | 0.00 | 2.50 | 6.75 | — | — |
| 07:30 | overnight | 1908 | 0.10 | 3.50 | 8.50 | — | — |
| 08:00 | overnight | 1907 | 0.02 | 2.75 | 7.50 | — | — |
| 08:30 | rth | 1909 | 0.37 | 7.25 | 16.75 | -0.05 | 0.87 |
| 09:00 | rth | 1912 | -0.02 | 6.25 | 15.00 | 0.02 | 0.90 |
| 09:30 | rth | 1912 | -0.22 | 5.25 | 13.00 | -0.00 | 0.95 |
| 10:00 | rth | 1912 | 0.14 | 4.75 | 11.50 | -0.03 | 0.85 |
| 10:30 | rth | 1912 | 0.22 | 4.25 | 10.75 | 0.03 | 0.96 |
| 11:00 | rth | 1912 | -0.01 | 4.00 | 9.75 | -0.03 | 0.95 |
| 11:30 | rth | 1912 | 0.06 | 3.75 | 9.25 | 0.03 | 1.29 |
| 12:00 | rth | 1846 | -0.00 | 3.75 | 10.00 | 0.16 | 1.01 |
| 12:30 | rth | 1847 | 0.22 | 3.50 | 9.00 | -0.04 | 1.02 |
| 13:00 | rth | 1847 | -0.07 | 4.25 | 9.75 | -0.03 | 0.96 |
| 13:30 | rth | 1847 | 0.29 | 4.00 | 9.75 | -0.01 | 1.00 |
| 14:00 | rth | 1847 | -0.16 | 3.75 | 10.00 | 0.07 | 1.52 |
| 14:30 | rth | 1847 | -0.15 | 5.75 | 13.25 | — | — |
| 15:00 | after | 1311 | 0.17 | 3.00 | 7.25 | — | — |
| 15:30 | after | 1847 | 0.01 | 1.50 | 3.75 | — | — |

Cash-session move by weekday, 8:30 open to the last print before 15:00, same outright.

| day | sessions | mean | median |
|---|---:|---:|---:|
| Mon | 383 | 4.95 pts ($25 on 1 MES) | 4.25 pts ($21 on 1 MES) |
| Tue | 387 | -0.67 pts ($-3 on 1 MES) | -0.25 pts ($-1 on 1 MES) |
| Wed | 383 | 0.03 pts ($0 on 1 MES) | 2.25 pts ($11 on 1 MES) |
| Thu | 383 | -1.12 pts ($-6 on 1 MES) | 1.00 pts ($5 on 1 MES) |
| Fri | 376 | 0.74 pts ($4 on 1 MES) | 3.38 pts ($17 on 1 MES) |

Weekday differences of a point or two are smaller than the range inside the day. They are not a strategy.

## Every configuration, after costs

Expectancy is average dollars per trade after the fee and the slippage. A day with no fill is not a trade. Benchmarks are in the table and cannot be selected.

| rule | train n | train win | train $/trade | train net | val n | val win | val $/trade | val net | status |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| orb_5_opposite_1R | 1189 | 50.0% | $-8.39 | $-9,980.79 | 379 | 49.6% | $-9.45 | $-3,579.90 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| orb_5_opposite_2R | 1189 | 42.9% | $-4.89 | $-5,809.54 | 379 | 39.8% | $-14.51 | $-5,499.90 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| orb_5_opposite_time | 1189 | 42.6% | $-7.42 | $-8,819.54 | 379 | 40.4% | $-16.98 | $-6,436.15 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| orb_5_mid_1R | 1196 | 46.8% | $-27.22 | $-32,553.27 | 383 | 50.1% | $-14.46 | $-5,537.68 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| orb_5_mid_2R | 1196 | 36.0% | $-19.49 | $-23,312.02 | 383 | 36.3% | $-11.26 | $-4,311.43 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| orb_5_mid_time | 1196 | 32.6% | $-18.56 | $-22,192.02 | 383 | 30.5% | $-18.03 | $-6,906.43 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| orb_5_atr_1R | 1201 | 45.5% | $-42.03 | $-50,473.69 | 385 | 48.3% | $-28.95 | $-11,146.68 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| orb_5_atr_2R | 1201 | 29.6% | $-47.36 | $-56,882.44 | 385 | 32.5% | $-29.11 | $-11,207.93 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| orb_5_atr_time | 1201 | 19.6% | $-40.69 | $-48,866.19 | 385 | 20.8% | $-27.75 | $-10,685.43 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| orb_15_opposite_1R | 1157 | 49.9% | $-5.31 | $-6,141.97 | 365 | 52.3% | $3.38 | $1,235.29 | train expectancy is not positive; train net is not positive |
| orb_15_opposite_2R | 1157 | 47.1% | $-0.28 | $-329.47 | 365 | 48.5% | $4.15 | $1,514.04 | train expectancy is not positive; train net is not positive |
| orb_15_opposite_time | 1157 | 47.1% | $-6.05 | $-7,003.22 | 365 | 48.5% | $0.63 | $229.04 | train expectancy is not positive; train net is not positive |
| orb_15_mid_1R | 1191 | 47.8% | $-20.46 | $-24,368.55 | 382 | 54.2% | $2.94 | $1,123.64 | train expectancy is not positive; train net is not positive |
| orb_15_mid_2R | 1191 | 39.4% | $-13.99 | $-16,657.30 | 382 | 42.7% | $9.30 | $3,552.39 | train expectancy is not positive; train net is not positive |
| orb_15_mid_time | 1191 | 38.5% | $-19.04 | $-22,674.80 | 382 | 40.6% | $2.74 | $1,046.14 | train expectancy is not positive; train net is not positive |
| orb_15_atr_1R | 1198 | 47.0% | $-34.98 | $-41,906.39 | 385 | 52.2% | $-13.16 | $-5,067.55 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| orb_15_atr_2R | 1198 | 30.9% | $-40.47 | $-48,480.14 | 385 | 36.4% | $-7.95 | $-3,060.05 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| orb_15_atr_time | 1198 | 23.8% | $-30.33 | $-36,330.14 | 385 | 29.4% | $6.59 | $2,537.45 | train expectancy is not positive; train net is not positive |
| orb_30_opposite_1R | 1094 | 49.4% | $-3.86 | $-4,225.97 | 336 | 50.3% | $-2.41 | $-809.07 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| orb_30_opposite_2R | 1094 | 48.3% | $-0.89 | $-973.47 | 336 | 49.7% | $0.55 | $184.68 | train expectancy is not positive; train net is not positive |
| orb_30_opposite_time | 1094 | 47.9% | $-5.41 | $-5,919.72 | 336 | 47.0% | $-4.16 | $-1,399.07 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| orb_30_mid_1R | 1176 | 49.2% | $-13.62 | $-16,022.50 | 377 | 51.7% | $-6.52 | $-2,458.04 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| orb_30_mid_2R | 1176 | 43.0% | $-10.72 | $-12,601.25 | 377 | 44.0% | $-8.34 | $-3,143.04 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| orb_30_mid_time | 1176 | 43.3% | $-12.30 | $-14,463.75 | 377 | 41.1% | $-12.68 | $-4,779.29 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| orb_30_atr_1R | 1188 | 46.7% | $-35.01 | $-41,597.07 | 382 | 53.9% | $-6.61 | $-2,526.35 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| orb_30_atr_2R | 1188 | 31.4% | $-39.32 | $-46,707.07 | 382 | 36.4% | $-8.65 | $-3,305.10 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| orb_30_atr_time | 1188 | 26.3% | $-29.74 | $-35,329.57 | 382 | 28.5% | $-18.46 | $-7,050.10 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| gap_fade_10bp | 873 | 50.6% | $-3.18 | $-2,772.36 | 219 | 53.9% | $6.75 | $1,477.56 | train expectancy is not positive; train net is not positive |
| gap_fade_25bp | 601 | 48.9% | $-4.32 | $-2,598.20 | 131 | 52.7% | $7.19 | $941.80 | train expectancy is not positive; train net is not positive |
| gap_go_10bp | 873 | 46.3% | $-18.26 | $-15,937.36 | 219 | 43.4% | $-23.64 | $-5,176.19 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| gap_go_25bp | 601 | 48.8% | $-8.14 | $-4,893.20 | 131 | 44.3% | $-17.91 | $-2,345.70 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| vwap_mr_0830_1030_1 | 719 | 70.0% | $-16.79 | $-12,074.00 | 238 | 73.1% | $-9.50 | $-2,261.52 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| vwap_mr_0830_1030_1.5 | 860 | 63.8% | $-11.51 | $-9,897.52 | 277 | 65.3% | $-10.14 | $-2,808.43 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| vwap_mr_1030_1200_1 | 558 | 57.0% | $-29.40 | $-16,407.37 | 192 | 63.0% | $-16.93 | $-3,250.75 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| vwap_mr_1030_1200_1.5 | 570 | 48.9% | $-23.89 | $-13,614.77 | 191 | 50.8% | $-16.74 | $-3,197.49 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| vwap_mr_1200_1400_1 | 524 | 65.8% | $-14.84 | $-7,775.38 | 175 | 62.3% | $-18.41 | $-3,221.52 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| vwap_mr_1200_1400_1.5 | 597 | 53.9% | $-22.93 | $-13,688.63 | 192 | 52.6% | $-28.76 | $-5,522.52 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| vwap_pb_0845_1100_1.5R | 739 | 39.1% | $-21.84 | $-16,136.22 | 258 | 38.8% | $-16.23 | $-4,186.79 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| vwap_pb_0845_1100_2R | 739 | 32.3% | $-27.53 | $-20,346.22 | 258 | 34.1% | $-12.71 | $-3,278.04 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| vwap_pb_0930_1200_1.5R | 664 | 36.9% | $-29.20 | $-19,387.80 | 215 | 43.7% | $-0.05 | $-10.86 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| vwap_pb_0930_1200_2R | 664 | 30.1% | $-39.45 | $-26,195.30 | 215 | 37.7% | $-5.02 | $-1,079.61 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| pd_fade | 489 | 36.8% | $-27.15 | $-13,274.05 | 173 | 41.0% | $-16.91 | $-2,925.97 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| pd_break_1R | 728 | 48.1% | $-15.25 | $-11,105.37 | 205 | 48.3% | $-15.60 | $-3,197.89 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| pd_break_2R | 728 | 42.9% | $-18.84 | $-13,716.62 | 205 | 42.4% | $-11.67 | $-2,392.89 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| on_reclaim_mid | 777 | 31.0% | $-15.08 | $-11,717.61 | 252 | 28.2% | $-55.26 | $-13,926.08 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| on_reclaim_1.5R | 782 | 37.5% | $-34.64 | $-27,090.91 | 254 | 40.9% | $-20.03 | $-5,088.37 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| fh_15bp_1.5R | 715 | 46.6% | $1.07 | $765.06 | 171 | 51.5% | $12.95 | $2,213.66 | finalist |
| fh_15bp_time | 715 | 44.9% | $4.12 | $2,947.56 | 171 | 48.5% | $8.58 | $1,467.41 | passed the gates but lost the rank or the correlation cutoff |
| fh_25bp_1.5R | 489 | 46.8% | $4.78 | $2,339.46 | 103 | 53.4% | $7.45 | $767.35 | passed the gates but lost the rank or the correlation cutoff |
| fh_25bp_time | 489 | 45.4% | $6.59 | $3,220.71 | 103 | 52.4% | $12.98 | $1,337.35 | passed the gates but lost the rank or the correlation cutoff |
| late_1R | 888 | 48.9% | $-31.19 | $-27,697.80 | 288 | 48.6% | $-26.43 | $-7,610.70 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| late_time | 888 | 26.2% | $-8.83 | $-7,844.05 | 288 | 20.8% | $-53.81 | $-15,498.20 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| tod_0830_0930_long | 1200 | 30.7% | $-15.26 | $-18,310.53 | 382 | 25.7% | $-46.98 | $-17,947.36 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| tod_0830_0930_short | 1200 | 26.8% | $-29.66 | $-35,595.53 | 382 | 28.0% | $-13.25 | $-5,059.86 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| tod_0930_1030_long | 1199 | 46.8% | $-13.60 | $-16,308.53 | 379 | 46.4% | $-15.20 | $-5,760.31 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| tod_0930_1030_short | 1199 | 41.8% | $-14.99 | $-17,973.53 | 379 | 42.2% | $-16.28 | $-6,170.31 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| tod_1000_1130_long | 1199 | 46.8% | $-12.03 | $-14,426.97 | 379 | 42.7% | $-15.96 | $-6,049.78 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| tod_1000_1130_short | 1199 | 41.4% | $-18.06 | $-21,658.22 | 379 | 41.2% | $-16.85 | $-6,386.03 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| tod_1330_1500_long | 1161 | 39.9% | $-11.68 | $-13,561.20 | 366 | 43.4% | $-8.35 | $-3,054.55 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| tod_1330_1500_short | 1161 | 35.9% | $-17.25 | $-20,022.45 | 366 | 37.4% | $-22.83 | $-8,355.80 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| tod_1400_1500_long | 1156 | 38.3% | $-26.77 | $-30,949.86 | 367 | 43.1% | $-8.88 | $-3,257.91 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| tod_1400_1500_short | 1156 | 41.0% | $-8.97 | $-10,363.61 | 367 | 42.0% | $-13.67 | $-5,016.66 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| trend_vwap_ema | 1203 | 33.3% | $-30.78 | $-37,022.52 | 385 | 35.8% | $-10.42 | $-4,010.16 | train expectancy is not positive; train net is not positive; validation expectancy is not positive; validation net is not positive |
| bench_bh | 1203 | 54.0% | $0.35 | $418.59 | 385 | 53.0% | $2.12 | $816.55 | benchmark, not eligible |
| bench_bh_stop | 1203 | 42.4% | $-3.23 | $-3,882.66 | 385 | 39.0% | $-1.04 | $-400.95 | benchmark, not eligible |
| bench_random | 1203 | 50.7% | $-7.37 | $-8,860.64 | 385 | 49.1% | $-13.82 | $-5,318.80 | benchmark, not eligible |

## Selection

N for the multiple-testing penalty is 63. Benchmarks are excluded from that count.

Finalists are the rules that passed the train gate, the validation gate, and the neighbor gate, then ranked high enough and were not too correlated with a higher-ranked finalist. Adjusted p-values are reported beside them. A bad p-value does not hide the holdout result, and it does block a recommendation to trade.

| rule | why it is here | train $/trade | val $/trade | val trades | neighbors with val expectancy ≤ 0 |
|---|---|---:|---:|---:|---|
| fh_15bp_1.5R | passed train, validation, and the neighbor rule | $1.07 | $12.95 | 171 | 0 of 2 (none) |
| fh_25bp_time | passed the gates but lost the rank or the correlation cutoff | $6.59 | $12.98 | 103 | 0 of 2 (none) |
| orb_15_mid_2R | train expectancy is not positive; train net is not positive | $-13.99 | $9.30 | 382 | 3 of 6 (orb_5_mid_2R, orb_15_atr_2R, orb_30_mid_2R) |
| fh_15bp_time | passed the gates but lost the rank or the correlation cutoff | $4.12 | $8.58 | 171 | 0 of 2 (none) |
| fh_25bp_1.5R | passed the gates but lost the rank or the correlation cutoff | $4.78 | $7.45 | 103 | 0 of 2 (none) |
| gap_fade_25bp | train expectancy is not positive; train net is not positive | $-4.32 | $7.19 | 131 | 1 of 2 (gap_go_25bp) |

## Finalists, or the best rules that still failed

Holdout numbers below were computed once, after the names were locked. They are reported whether or not they made money.

| rule | split | trades | win rate | avg win | avg loss | $/trade | net | max drawdown | worst day | best day | avg qty |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| fh_15bp_1.5R | train | 715 | 46.6% | $152.85 | $-131.24 | $1.07 | $765.06 | $3,997.62 | $-199.97 | $291.28 | 1.9 |
| fh_15bp_1.5R | validation | 171 | 51.5% | $151.21 | $-133.65 | $12.95 | $2,213.66 | $1,503.23 | $-199.94 | $277.56 | 1.4 |
| fh_15bp_1.5R | holdout | 135 | 54.1% | $129.49 | $-115.61 | $16.93 | $2,285.07 | $1,206.78 | $-197.47 | $282.56 | 1.3 |

| rule | bootstrap p | adjusted bootstrap p | sign-flip p | adjusted sign-flip p | share of random nulls at least this high |
|---|---:|---:|---:|---:|---:|
| fh_15bp_1.5R | 0.151 | 1.000 | 0.135 | 1.000 | 0.0% |

The bootstrap asks how often a resample of this rule's own validation days has a mean at or below zero. It uses every validation session, including days with no trade, because a Combine lives through those days too. The sign-flip asks how often scrambling the sign of those days still looks as good as the real result. The last column compares the rule with the random-entry null (an 8-point stop, an 8-point target, and a 10:30 exit). Adjusted values multiply the raw p-value by the 63 eligible tests and cap the result at 1.

The other three first-hour settings also cleared the train and validation gates. They are the same idea with a higher threshold or a time exit, so their validation days move together and only the top score was held out. They did not get a second look at 2025-07 through 2026-09.

![Cumulative P&L after costs](research/equity.png)

## Combine attempts

Each month inside a split starts a fresh $50,000 attempt. The attempt cannot see the next split. Pass means +$3,000 before a $1,000 intraday daily loss or a touch of the trailing $2,000 floor, and the best closed day is under half of profits. Resolved pass rate ignores attempts that ran out of calendar. The all-starts rate counts those unfinished attempts as failures, which is the number that matters if you buy a Combine on a random month.

| book | split | starts | passed | failed | censored | resolved pass rate | all-starts pass rate | median days to pass | passes that also met 40% |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| fh_15bp_1.5R | train | 56 | 4 | 39 | 13 | 9.3% | 7.1% | 164.0 | 4 |
| fh_15bp_1.5R | validation | 18 | 6 | 0 | 12 | 100.0% | 33.3% | 210.0 | 6 |
| fh_15bp_1.5R | holdout | 15 | 2 | 0 | 13 | 100.0% | 13.3% | 232.5 | 2 |

Read the all-starts column, not the resolved one. A random month passed in 4 of 56 train starts (7%), 6 of 18 validation starts (33%), and 2 of 15 holdout starts (13%). The passes that happened took a median of 164, 210, and 232 sessions, on the order of eight to eleven months. In the long train window, 39 of 56 starts died on the trailing $2,000 loss and none died on the $1,000 daily limit. In the shorter later windows the book usually had neither passed nor failed when the data ran out. Every pass that did occur also had its best day under 40% of profits, so the consistency rule was not what stopped it. A one-dollar train edge does not pay for a Combine. It bleeds into the trailing floor.

![Combine pass rate by monthly start](research/combine_pass_rate.png)

## What no edge looks like

Two buy-and-hold benchmarks buy one direction at 8:30 and flatten at 15:00. One has no stop and is always 1 MES. The other uses a 20-point stop and the $200 sizer. The random benchmark flips a coin from the session date (seed 7), enters at 9:00 with an 8-point stop and an 8-point target, and flattens at 10:30. One hundred more seeds, 1000 through 1099, were run on train and validation only.

Random-seed validation expectancy: mean $-16.30, median $-16.30, 5th percentile $-18.79, 95th percentile $-13.82, share positive 0.0%.
Train: mean $-15.92, median $-15.92, share positive 0.0%.

Those 100 seeds are only two books. Adding an even number or an odd number to the date flips every day the same way, so half the seeds are one coin-flip and half are its mirror. Both mirrors lost money, about $7 to $24 per trade. Beating that null is a low bar: a coin-flip with an 8-point stop and an 8-point target loses to costs. It shows what no edge looks like. It does not prove that a rule which lost less is a real edge. The bootstrap on the rule's own days is the check that matters. Buying 1 MES at 8:30 and holding to 15:00 with no stop made $0.35 per day in train and $2.12 in validation. The same buy with a 20-point stop lost money in both windows. The cash-session drift is real and too small to pay for a stop.


## Recommendation for the live bot

Do not change the live bot. Do not switch the morning to first-hour momentum, and do not loosen the chop filter or the 2-tick retest to force more trades. `fh_15bp_1.5R` does not clear the predeclared adjusted p-value cutoff of 0.10. Counting all 63 tests, the result is indistinguishable from luck, even though the holdout dollars happened to stay positive. The earlier seven-year replay of the live opening-range retest was already about 102 trades, a 38% win rate, and a loss of roughly $7 a trade. This broader search does not rescue it and does not offer a substitute. Leave the live settings where they are. A new Combine attempt is not justified by this file.

## What this study did not change

Live defaults are untouched: the 2-tick retest, the $1.40 fee placeholder, the $200 risk, the 2-contract cap, the morning window, and the flatten times. Research limit fills are stricter than the paper broker. The paper broker fills a target when price touches it. This study fills a limit only when price trades through it. Stops still fill on a touch, and if a stop and a target are in the same bar the stop wins. The research sizer is also separate. It can use up to 50 MES and it charges both sides of slippage up front. It does not call the live sizer, which is capped at 2 MES.

