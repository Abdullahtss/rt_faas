from app.services.banksin import fraud_indexes, load


def test_banksin_file_loads_madrid_payments():
    rows = load()
    assert len(rows) > 500000
    assert rows[0].city == "Madrid"
    assert rows[0].currency == "EUR"
    assert rows[0].scenario == "banksim"
    marked = fraud_indexes()
    assert len(marked) == 7200
    assert rows[marked[0]].scenario == "banksim-fraud"
    assert rows[marked[0]].city == "Madrid"
