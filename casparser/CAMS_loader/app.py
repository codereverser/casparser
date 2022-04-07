from enum import unique
import json
from datetime import datetime as dt
import boto3
from lib import change_datatypes
from cams_parser import parse_CAMS
from constants import path, fileNameCAMS, passwordCAMS
from fipObject import populate_fipObject
from transform_CAS import transform_CAS

def lambda_handler(event, context):
    # get userId
    if 'queryStringParameters' not in event or 'userId' not in event['queryStringParameters']:
        return {
            "statusCode": 400,
            "body": {
                "error": "Invalid User ID"
            }
        }

    user_id = event.get('queryStringParameters').get('userId')

    dynamodb_client = boto3.resource('dynamodb')
    try:
        users_table = dynamodb_client.Table('User')
        user_item = users_table.get_item(Key={
            'Id': user_id
        })
    except:
        return {
            "statusCode": 400,
            "body": {
                "error": "User or Table not found"
            }
        }

    fipObjects_array = user_item['Item']['fipObjects']

    cas_json = parse_CAMS()
    json_obj = json.loads(cas_json)
    df = transform_CAS(json_obj)

    unique_advisors = df['advisor'].unique()

    for advisor_code in unique_advisors:
         fipObject = populate_fipObject(advisor_code, df[df.advisor == advisor_code])
         fipObjects_array.append(fipObject)
    
    fipObjects_array = change_datatypes(fipObjects_array)
    user_item['Item']['fipObjects'] = fipObjects_array

    users_table.update_item(
        Key={
            'Id': '12',
        },
        UpdateExpression="set fipObjects=:f",
        ExpressionAttributeValues={
            ':f': user_item['Item']['fipObjects'],
        }
    )

    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "message": "Completed post processing",
            }
        ),
    }
