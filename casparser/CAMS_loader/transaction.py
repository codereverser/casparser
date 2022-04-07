from decimal import Decimal
from defaults import tx_obj

def populate_transaction(df):
    transaction = {}
    transaction_array = []

    array = df.to_dict('records')
    
    for tx in array:
        for record in tx['transactions']:
            transaction_array.append(record)

    return transaction_array