"""Decide whether an account is allowed to receive orders.

ProjectX API trading is not allowed on Topstep Live Funded Accounts.
Practice, Trading Combine, and Express Funded accounts are simulated.
The REST account-search example does not include a `simulated` flag; the
SignalR account payload does. When the flag is missing, the account name has
to match the kind in config (practice, combine, or express).
"""

from __future__ import annotations

from topstepbot.broker.base import AccountRejected
from topstepbot.config import LIVE_FUNDED_OVERRIDE, BotConfig
from topstepbot.models import AccountInfo


def looks_live_funded(account: AccountInfo) -> bool:
    if account.simulated is False:
        return True
    name = account.name.upper()
    if "LIVE" in name and "PRACTICE" not in name and "PRAC" not in name:
        return True
    return False


def name_matches_kind(name: str, kind: str) -> bool:
    upper = name.upper()
    if kind == "practice":
        return any(token in upper for token in ("PRAC", "PRACTICE", "DEMO", "SIM"))
    if kind == "combine":
        return any(token in upper for token in ("COMBINE", "TC"))
    if kind == "express":
        return "EXPRESS" in upper
    return False


def assert_account_allowed(account: AccountInfo, config: BotConfig) -> str:
    """Return a short reason the account may be traded, or raise AccountRejected."""
    if account.can_trade is False:
        raise AccountRejected(f"Account {account.id} ({account.name}) is not allowed to trade (canTrade is false).")

    override = config.compliance.allow_live_funded_account_override
    if looks_live_funded(account):
        if override == LIVE_FUNDED_OVERRIDE:
            return "live-funded override is set; you are responsible for this order"
        raise AccountRejected(
            f"Account {account.id} ({account.name}) looks like a Topstep Live Funded account. "
            "ProjectX API trading is not allowed on Live Funded Accounts. "
            "Use a Practice, Combine, or Express Funded account. "
            "The only override is the exact phrase in config "
            "compliance.allow_live_funded_account_override, and setting it does not make live API trading allowed by Topstep."
        )

    if account.simulated is True:
        return "account is marked simulated"

    # TODO-VERIFY: Account/search example payload has id, name, canTrade, isVisible
    # and does not show `simulated`. GatewayUserAccount on the user hub does.
    if name_matches_kind(account.name, config.account.kind):
        return (
            f"simulated flag was not in the account payload; name matches configured kind "
            f"{config.account.kind}"
        )

    raise AccountRejected(
        f"Account {account.id} ({account.name}) did not include a simulated flag, and its name "
        f"does not look like a {config.account.kind} account. Refusing to trade. "
        "Pin the right account with account.account_id after you confirm it in TopstepX, "
        "or set account.kind to practice, combine, or express so the name check matches."
    )


def choose_account(accounts: list[AccountInfo], config: BotConfig) -> AccountInfo:
    matches = list(accounts)
    if config.account.account_id is not None:
        matches = [account for account in matches if account.id == config.account.account_id]
    needle = config.account.account_name_contains.strip().lower()
    if needle:
        matches = [account for account in matches if needle in account.name.lower()]
    if len(matches) != 1:
        names = ", ".join(f"{account.id}:{account.name}" for account in accounts) or "none"
        raise AccountRejected(
            f"Need exactly one account to trade, found {len(matches)}. "
            f"Accounts returned: {names}. Set account.account_id in config/settings.yaml."
        )
    assert_account_allowed(matches[0], config)
    return matches[0]
