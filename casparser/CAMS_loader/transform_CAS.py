from decimal import Decimal
import pandas as pd

def get_tx(scheme, amc):
    tx_obj = {}
    tx_array = []

    for tx in scheme['transactions']:
        tx_obj['amc'] = amc
        tx_obj['registrar'] = scheme['rta']
        tx_obj['schemeCode'] = scheme['scheme']
        tx_obj['isin'] = scheme['isin']
        tx_obj['amfiCode'] = scheme['amfi']
        tx_obj['amount'] = Decimal(tx['amount'])
        tx_obj['closingUnits'] = 0.0 if pd.isnull(tx['units']) else Decimal(tx['units'])
        tx_obj['balance'] = 0.0 if pd.isnull(tx['balance']) else Decimal(tx['balance'])
        tx_obj['type'] = tx['type']
        tx_obj['nav'] = 0.0 if pd.isnull(tx['nav']) else Decimal(tx['nav'])
        tx_obj['executionDate'] = tx['date']
        tx_obj['narration'] = tx['description']

        tx_array.append(tx_obj)
    
    return tx_array


def transform_CAS(json_obj):
    df_array=[]

    for folio in json_obj['folios']:
        df_obj={}
        df_obj['from'] = json_obj['statement_period']['from']
        df_obj['to'] = json_obj['statement_period']['to']
        df_obj['folioNumber'] = folio['folio']
        df_obj['amc'] = folio['amc']
        for scheme in folio['schemes']:
            if float(scheme['close']) == 0:
                break
            df_obj['scheme'] = scheme['scheme']
            df_obj['advisor'] = scheme['advisor']
            df_obj['rta'] = scheme['rta']
            df_obj['isin'] = scheme['isin']
            df_obj['amfi'] = scheme['amfi']    
            df_obj['closingUnits'] = scheme['close']
            df_obj['valueDate'] = scheme['valuation']['date']
            df_obj['value'] = Decimal(scheme['valuation']['value'])
            df_obj['nav'] = scheme['valuation']['nav']
            df_obj['transactions'] = get_tx(scheme, folio['amc'])
            df_array.append(df_obj)
    
    df= pd.DataFrame(data=df_array)
    return df
    