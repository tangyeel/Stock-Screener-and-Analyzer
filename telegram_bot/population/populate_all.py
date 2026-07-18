"""
Run all population scripts to build the instrument_master table.

Usage:
    python -m telegram_bot.population.populate_all
"""

from db.database import init_db
from telegram_bot.population.indices import populate_indices
from telegram_bot.population.equities import populate_equities
from telegram_bot.population.amfi_funds import populate_funds


def populate_all():
    init_db()
    print("=" * 50)
    print("Populating instrument_master...")
    print("=" * 50)
    populate_indices()
    populate_equities()
    try:
        populate_funds()
    except Exception as e:
        print(f"AMFI fund population skipped: {e}")
    print("=" * 50)
    print("Done.")


if __name__ == "__main__":
    populate_all()
