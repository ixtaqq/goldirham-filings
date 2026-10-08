"""Tracked companies from ai-infra-digest/src/collector/sec.ts, with CIKs and filer types
verified against EDGAR by scripts/check_companies.py on 2026-10-08.

Foreign private issuers file 20-F and 6-K instead of 10-K and 10-Q; they are listed but not
ingested in the first release. ANSS was acquired by Synopsys in 2025; its historical filings remain.
"""

from dataclasses import dataclass
from typing import Literal

Tier = Literal[1, 2]
FilerType = Literal["domestic", "foreign"]


@dataclass(frozen=True)
class Company:
    ticker: str
    name: str
    cik: str
    tier: Tier
    filer_type: FilerType = "domestic"

    @property
    def cik_int(self) -> int:
        return int(self.cik)


TRACKED_COMPANIES: tuple[Company, ...] = (
    Company("NVDA", "NVIDIA Corporation", "0001045810", 1),
    Company("AMD", "Advanced Micro Devices", "0000002488", 1),
    Company("AVGO", "Broadcom Inc.", "0001730168", 1),
    Company("MSFT", "Microsoft Corporation", "0000789019", 1),
    Company("AMZN", "Amazon.com Inc.", "0001018724", 1),
    Company("GOOGL", "Alphabet Inc.", "0001652044", 1),
    Company("META", "Meta Platforms Inc.", "0001326801", 1),
    Company("TSM", "Taiwan Semiconductor (ADR)", "0001046179", 1, "foreign"),
    Company("INTC", "Intel Corporation", "0000050863", 1),
    Company("QCOM", "Qualcomm Inc.", "0000804328", 1),
    Company("ORCL", "Oracle Corporation", "0001341439", 1),
    Company("IBM", "International Business Machines", "0000051143", 1),
    Company("MU", "Micron Technology Inc.", "0000723125", 1),
    Company("ASML", "ASML Holding N.V.", "0000937966", 1, "foreign"),
    Company("SMCI", "Super Micro Computer Inc.", "0001375365", 1),
    Company("DELL", "Dell Technologies Inc.", "0001571996", 1),
    Company("AMAT", "Applied Materials Inc.", "0000006951", 1),
    Company("LRCX", "Lam Research Corporation", "0000707549", 1),
    Company("KLAC", "KLA Corporation", "0000319201", 1),
    Company("CSCO", "Cisco Systems Inc.", "0000858877", 1),
    Company("MRVL", "Marvell Technology Inc.", "0001835632", 2),
    Company("ANET", "Arista Networks Inc.", "0001596532", 2),
    Company("DLR", "Digital Realty Trust Inc.", "0001297996", 2),
    Company("EQIX", "Equinix Inc.", "0001101239", 2),
    Company("CEG", "Constellation Energy Corp.", "0001868275", 2),
    Company("VST", "Vistra Corp.", "0001692819", 2),
    Company("GEV", "GE Vernova Inc.", "0001996810", 2),
    Company("VRT", "Vertiv Holdings Co.", "0001674101", 2),
    Company("ETN", "Eaton Corporation PLC", "0001551182", 2),
    Company("ARM", "Arm Holdings PLC (ADR)", "0001973239", 2, "foreign"),
    Company("WDC", "Western Digital Corp.", "0000106040", 2),
    Company("ON", "ON Semiconductor Corp.", "0001097864", 2),
    Company("STX", "Seagate Technology Holdings", "0001137789", 2),
    Company("ANSS", "Ansys Inc.", "0001013462", 2),
    Company("SNPS", "Synopsys Inc.", "0000883241", 2),
)

BY_TICKER = {c.ticker: c for c in TRACKED_COMPANIES}
