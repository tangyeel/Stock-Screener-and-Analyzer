"""
Populate instrument_master with major Indian indices.
"""

import uuid

INDICES = [
    {
        "ticker": "^NSEI",
        "name": "Nifty 50",
        "aliases": ["nifty", "nifty50", "nifty 50"],
        "exchange": "INDEX",
    },
    {
        "ticker": "^BSESN",
        "name": "BSE Sensex",
        "aliases": ["sensex", "bse sensex", "bse 30", "s&p bse sensex"],
        "exchange": "INDEX",
    },
    {
        "ticker": "^NSEBANK",
        "name": "Nifty Bank",
        "aliases": ["bank nifty", "nifty bank", "banknifty"],
        "exchange": "INDEX",
    },
    {
        "ticker": "^CNXIT",
        "name": "Nifty IT",
        "aliases": ["nifty it", "it index"],
        "exchange": "INDEX",
    },
    {
        "ticker": "^CNXPHARMA",
        "name": "Nifty Pharma",
        "aliases": ["nifty pharma", "pharma index"],
        "exchange": "INDEX",
    },
    {
        "ticker": "^CNXAUTO",
        "name": "Nifty Auto",
        "aliases": ["nifty auto", "auto index"],
        "exchange": "INDEX",
    },
    {
        "ticker": "^CNXFMCG",
        "name": "Nifty FMCG",
        "aliases": ["nifty fmcg", "fmcg index"],
        "exchange": "INDEX",
    },
    {
        "ticker": "^CNXMETAL",
        "name": "Nifty Metal",
        "aliases": ["nifty metal", "metal index"],
        "exchange": "INDEX",
    },
    {
        "ticker": "^CNXENERGY",
        "name": "Nifty Energy",
        "aliases": ["nifty energy", "energy index"],
        "exchange": "INDEX",
    },
    {
        "ticker": "^CNX100",
        "name": "Nifty 100",
        "aliases": ["nifty 100", "nifty100"],
        "exchange": "INDEX",
    },
    {
        "ticker": "^CNX200",
        "name": "Nifty 200",
        "aliases": ["nifty 200", "nifty200"],
        "exchange": "INDEX",
    },
    {
        "ticker": "^CNXMID",
        "name": "Nifty Midcap 100",
        "aliases": ["nifty midcap", "midcap 100", "nifty midcap 100"],
        "exchange": "INDEX",
    },
    {
        "ticker": "^CNXSMALL",
        "name": "Nifty Smallcap 100",
        "aliases": ["nifty smallcap", "smallcap 100"],
        "exchange": "INDEX",
    },
    {
        "ticker": "^INDIAVIX",
        "name": "India VIX",
        "aliases": ["india vix", "vix", "fear index"],
        "exchange": "INDEX",
    },
    {
        "ticker": "^CRSLDX",
        "name": "Nifty Realty",
        "aliases": ["nifty realty", "realty index"],
        "exchange": "INDEX",
    },
    {
        "ticker": "^CNXCONSUMER",
        "name": "Nifty Consumer Durables",
        "aliases": ["nifty consumer durables", "consumer durables"],
        "exchange": "INDEX",
    },
    {
        "ticker": "^CNXMEDIA",
        "name": "Nifty Media",
        "aliases": ["nifty media", "media index"],
        "exchange": "INDEX",
    },
    {
        "ticker": "^CNXSERVICE",
        "name": "Nifty Services Sector",
        "aliases": ["nifty services", "services index"],
        "exchange": "INDEX",
    },
    {
        "ticker": "^CNXFINANCE",
        "name": "Nifty Financial Services",
        "aliases": ["nifty financial services", "financial services"],
        "exchange": "INDEX",
    },
    {
        "ticker": "^CNXDIVOP",
        "name": "Nifty Dividend Opportunities",
        "aliases": ["nifty dividend", "dividend opportunities"],
        "exchange": "INDEX",
    },
]


def populate_indices():
    from db.database import get_connection
    with get_connection() as conn:
        for idx in INDICES:
            aliases_str = ",".join(idx["aliases"])
            conn.execute(
                """INSERT OR IGNORE INTO instrument_master
                   (id, instrument_type, ticker, primary_name, aliases, sector, exchange, is_active)
                   VALUES (?, 'index', ?, ?, ?, NULL, ?, 1)""",
                (str(uuid.uuid4()), idx["ticker"], idx["name"], aliases_str, idx["exchange"]),
            )
    print(f"Populated {len(INDICES)} indices")


if __name__ == "__main__":
    populate_indices()
