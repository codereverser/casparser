from defaults import summary
from holdings import populate_holdings

def populate_summary(df):
    summary = {}

    start_date = df['from'].unique()
    end_date= df['to'].unique()

    summary['currentValue'] = df['value'].sum()
    # TO DO in figures calculation
    summary['investedValue'] = 0.0
    summary['holdings'] = populate_holdings(df)

    return summary