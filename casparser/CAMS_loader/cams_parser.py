import casparser
import pandas as pd
from constants import path, fileNameCAMS, passwordCAMS

def parse_CAMS():
    fileName = fileNameCAMS
    password = passwordCAMS
    sourceFile = path+fileName

    # Get data in json format
    json_str = casparser.read_cas_pdf(sourceFile, password, output="json")

    return json_str
