"""Cash-session dates for CME equity hours. Research only."""

from __future__ import annotations

from datetime import date, timedelta

# Train through 2023. Validation is 2024-01-01 through 2025-06-30.
# Holdout 2025-07-01 through the end of the file is not used for selection.
TRAIN_END = date(2023, 12, 31)
VAL_START = date(2024, 1, 1)
VAL_END = date(2025, 6, 30)
HOLDOUT_START = date(2025, 7, 1)

# Descriptive regimes. These are labels, not strategy parameters.
REGIMES = (
    ("2020 covid", date(2020, 2, 1), date(2020, 6, 30)),
    ("2022 bear", date(2022, 1, 1), date(2022, 12, 31)),
    ("2023-2026", date(2023, 1, 1), date(2026, 12, 31)),
)


def cash_session(year: int, month: int, day: int, minute: int) -> date:
    """Map a Central-time bar to the cash session it belongs to.

    A bar at or after 17:00 belongs to the next cash date. Saturday and
    Sunday sessions roll forward to Monday, so Friday evening and Sunday
    evening are Monday's overnight.
    """
    session = date(year, month, day)
    if minute >= 17 * 60:
        session = session + timedelta(days=1)
    while session.weekday() >= 5:
        session = session + timedelta(days=1)
    return session


def split_name(session: date) -> str:
    if session <= TRAIN_END:
        return "train"
    if session <= VAL_END:
        return "validation"
    return "holdout"


def split_days(days):
    """Train, validation, and holdout. Holdout is returned so the caller can keep it sealed."""
    train, validation, holdout = [], [], []
    for day in days:
        label = split_name(day.session)
        if label == "train":
            train.append(day)
        elif label == "validation":
            validation.append(day)
        else:
            holdout.append(day)
    return train, validation, holdout
