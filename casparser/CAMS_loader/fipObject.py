from fiObject import populate_fiObject
from transaction import populate_transaction
from defaults import fipObject
from summary import populate_summary
from lib import lookup_advisor

def populate_fipObject(advisor_code, df):
    fipObject = {}

    fipObject['fipId'] = advisor_code
    fipObject['fipName'] = lookup_advisor(advisor_code)
    fiAccountInfo ={}
    fiAccountInfo['accountRefNo'] = ''
    fipObject['fiAccountInfo'] = fiAccountInfo
    fipObject['fiObjects'] = populate_fiObject('mutualFunds', df)

    return fipObject
    