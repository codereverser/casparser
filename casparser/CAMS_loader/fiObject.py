from transaction import populate_transaction
from defaults import fiObject
from summary import populate_summary

def populate_fiObject(type, df):
    fiObject = {}
    fiObject_array= []

    fiObject['type'] = type
    fiObject['maskedAccNumber']=''
    fiObject['Summary'] = populate_summary(df)
    fiObject['Transactions'] = populate_transaction(df[['transactions']])
    fiObject_array.append(fiObject)

    return fiObject_array
    