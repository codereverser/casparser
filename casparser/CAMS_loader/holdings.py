from defaults import holdings

def populate_holdings(df):
    holding = {}
    holdings_array = []

    array = df.to_dict('records')
    
    for record in array:
        holding['amc'] = record['amc']
        holding['registrar'] = record['rta']
        holding['schemeCode'] = record['scheme']
        holding['isin'] = record['isin']
        holding['folioNo'] = record['folioNumber']
        holding['closingUnits'] = record['closingUnits']
        holding['nav'] = record['nav']
        holding['amfiCode'] = record['amfi']

        holdings_array.append(holding)

    return holdings_array