"""Google Cloud Billing API helpers."""
import logging
from datetime import date, timedelta

import requests

from common.google_auth import get_credentials

logger = logging.getLogger(__name__)

_BILLING_BASE = "https://cloudbilling.googleapis.com/v1"
_BUDGET_BASE = "https://billingbudgets.googleapis.com/v1"


def _headers() -> dict:
    creds = get_credentials()
    return {"Authorization": f"Bearer {creds.token}"}


def get_billing_accounts() -> list[dict]:
    r = requests.get(f"{_BILLING_BASE}/billingAccounts", headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json().get("billingAccounts", [])


def get_budgets(billing_account_name: str) -> list[dict]:
    """Return budgets for a billing account. Each budget includes currentSpend if available."""
    url = f"{_BUDGET_BASE}/{billing_account_name}/budgets"
    r = requests.get(url, headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json().get("budgets", [])


def billing_summary() -> dict:
    """
    Return a summary dict for the dashboard:
      accounts: list of {name, displayName, open}
      budgets:  list of {displayName, amount, currencyCode, currentSpend}
      error:    str if something failed
    """
    try:
        accounts = get_billing_accounts()
        budgets = []
        for acc in accounts:
            try:
                for b in get_budgets(acc["name"]):
                    amount_obj = b.get("amount", {})
                    # budgetAmount can be a fixed amount or lastPeriodAmount
                    fixed = amount_obj.get("specifiedAmount", {})
                    budget_amount = fixed.get("units") or fixed.get("nanos")
                    currency = fixed.get("currencyCode", "")

                    spend_obj = b.get("currentSpend", {})
                    current_spend = spend_obj.get("units") or "0"
                    spend_currency = spend_obj.get("currencyCode", currency)

                    budgets.append({
                        "display_name": b.get("displayName", acc.get("displayName", "")),
                        "budget_amount": budget_amount,
                        "currency": spend_currency or currency,
                        "current_spend": current_spend,
                    })
            except Exception:
                logger.exception("Could not fetch budgets for %s", acc.get("name"))

        return {"accounts": accounts, "budgets": budgets, "error": None}
    except Exception as e:
        logger.exception("Billing summary failed")
        return {"accounts": [], "budgets": [], "error": str(e)[:120]}
