"""Check every tracked company's CIK and filer type against EDGAR. Rerunnable.

Writes data/company_check.json and prints one line per company.
"""

import json

from goldirham_filings.companies import TRACKED_COMPANIES
from goldirham_filings.config import settings
from goldirham_filings.edgar import EdgarClient


def main() -> None:
    cfg = settings()
    client = EdgarClient(cfg.sec_user_agent, cfg.sec_requests_per_second)
    sec_ciks = client.company_tickers()
    rows = []
    for company in TRACKED_COMPANIES:
        sec_cik = sec_ciks.get(company.ticker)
        forms = client.filer_forms(company.cik_int)
        filer_type = (
            "domestic" if {"10-K", "10-Q"} & forms else "foreign" if "20-F" in forms else "none"
        )
        row = {
            "ticker": company.ticker,
            "cik": company.cik_int,
            "sec_cik": sec_cik,
            "cik_ok": sec_cik == company.cik_int,
            "filer_type": filer_type,
            "periodic_forms": sorted(forms & {"10-K", "10-Q", "20-F", "40-F", "6-K"}),
        }
        rows.append(row)
        print(json.dumps(row))
    cfg.data_dir.mkdir(exist_ok=True)
    (cfg.data_dir / "company_check.json").write_text(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
