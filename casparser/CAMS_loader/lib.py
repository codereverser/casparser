from decimal import Decimal, Context
import numpy
from constants import advisor_map


def change_datatypes(obj):
    ctx = Context(prec=2)

    if isinstance(obj, list):
        for i in range(len(obj)):
            obj[i] = change_datatypes(obj[i])
        return obj
    elif isinstance(obj, dict):
        for k in obj.keys():
            obj[k] = change_datatypes(obj[k])
        return obj
    elif type(obj) == type(True) or type(obj) == Decimal:
        return obj
    elif type(obj) == type(True) or type(obj) == int:
        return obj
    elif type(obj) == type(True) or type(obj) == float or type(obj) == numpy.float64:
        return ctx.create_decimal_from_float(obj)
    elif obj.isnumeric():
        return int(obj)
    else:
        try:
            float(obj)
            return Decimal(obj)
        except ValueError:
            return obj

def lookup_advisor(code):
    for advisor in advisor_map:
        if advisor['code'] == code:
            return advisor['name']

    return None