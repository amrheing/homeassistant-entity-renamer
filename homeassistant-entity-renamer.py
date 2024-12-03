#!/usr/bin/env python3

import argparse
import config
import csv
import json
import re
import requests
import tabulate
import ssl
import websocket
import sys

tabulate.PRESERVE_WHITESPACE = True

# Determine the protocol based on TLS configuration
TLS_S = 's' if config.TLS else ''

# Header containing the access token
headers = {
    'Authorization': f'Bearer {config.ACCESS_TOKEN}',
    'Content-Type': 'application/json'
}

def align_strings(table):
    alignment_char = "."

    if len(table) == 0:
        return
    
    for column in range(len(table[0])):
        # Get the column data from the table
        column_data = [row[column] for row in table]

        # Find the maximum length of the first part of the split strings
        strings_to_align = [s for s in column_data if alignment_char in s]
        if len(strings_to_align) == 0:
            continue
        
        max_length = max([len(s.split(alignment_char)[0]) for s in strings_to_align])

        def align_string(s):
            s_split = s.split(alignment_char, maxsplit=1)
            if len(s_split) == 1:
                return s
            else:
                return f"{s_split[0]:>{max_length}}.{s_split[1]}"

        # Create the modified table by replacing the column with aligned strings
        table = [
            tuple(align_string(value) if i == column else value for i, value in enumerate(row))
            for row in table
        ]

    return table

def list_entities(regex=None):
    # API endpoint for retrieving all entities
    api_endpoint = f'http{TLS_S}://{config.HOST}/api/states'

    # Send GET request to the API endpoint
    response = requests.get(api_endpoint, headers=headers, verify=config.SSL_VERIFY)

    # Check if the request was successful
    if response.status_code == 200:
        # Parse the JSON response
        data = json.loads(response.text)

        # Extract entity IDs and friendly names
        entity_data = [(entity['attributes'].get('friendly_name', ''), entity['entity_id']) for entity in data]

        # Filter the entity data if regex argument is provided
        if regex:
            filtered_entity_data = [(friendly_name, entity_id) for friendly_name, entity_id in entity_data if
                                    re.search(regex, entity_id)]
            entity_data = filtered_entity_data

        # Sort the entity data by friendly name
        entity_data = sorted(entity_data, key=lambda x: x[0])

        # Output the entity data
        return entity_data

    else:
        print(f'Error: {response.status_code} - {response.text}')
        return []

def create_rename_data(entity_data, search_regex, replace_regex=None):
    rename_data = []
    if replace_regex is not None:
        for friendly_name, entity_id in entity_data:
            new_entity_id = re.sub(search_regex, replace_regex, entity_id)
            rename_data.append((friendly_name, entity_id, new_entity_id))
    else:
        rename_data = [(friendly_name, entity_id, "") for friendly_name, entity_id in entity_data]
    return rename_data


def process_entities(entity_data, search_regex, replace_regex=None, output_csv=None, input=None):
    rename_data = create_rename_data(entity_data, search_regex, replace_regex)

    # Print the table with friendly name and entity ID
    table = [("Friendly Name", "Current Entity ID", "New Entity ID")] + align_strings(rename_data)
    print(tabulate.tabulate(table, headers="firstrow", tablefmt="github"))

    # Same table, but without whitespace for alignment
    table = [("Friendly Name", "Current Entity ID", "New Entity ID")] + rename_data
    if output_csv:
        with open(output_csv, 'w', newline='') as csvfile:
            csv_writer = csv.writer(csvfile)
            csv_writer.writerows(table)
            print(f"(Table written to {output_csv})")

    # Ask user for confirmation if replace_regex is provided
    if replace_regex is None:
        return
    
    if input != None:
        answer = input("\nDo you want to proceed with renaming the entities? (y/N): ")
        if answer.lower() not in ["y", "yes"]:
            print("Renaming process aborted.")
            return

    rename_entities(rename_data)
    
def rename_entities(rename_data, test=False):
    websocket_url = f'ws{TLS_S}://{config.HOST}/api/websocket'
    sslopt = {"cert_reqs": ssl.CERT_NONE} if not config.SSL_VERIFY else {}
    ws = websocket.WebSocket(sslopt=sslopt)
    ws.connect(websocket_url)

    auth_req = ws.recv()

    # Authenticate with Home Assistant
    auth_msg = json.dumps({"type": "auth", "access_token": config.ACCESS_TOKEN})
    ws.send(auth_msg)
    auth_result = ws.recv()
    auth_result = json.loads(auth_result)
    if auth_result["type"] != "auth_ok":
        print("Authentication failed. Check your access token.")
        return

    # Rename the entities
    for index, (_, entity_id, new_entity_id) in enumerate(rename_data, start=1):
        entity_registry_update_msg = json.dumps({
            "id": index,
            "type": "config/entity_registry/update",
            "entity_id": entity_id,
            "new_entity_id": new_entity_id
        })
        if not test:
            print(f"try to rename {entity_id}")
            ws.send(entity_registry_update_msg)
            update_result = ws.recv()
            update_result = json.loads(update_result)
            if update_result["success"]:
                print(f"Entity '{entity_id}' renamed to '{new_entity_id}' successfully!")
            else:
                print(f"Failed to rename entity '{entity_id}': {update_result['error']['message']}")
        else:
            print(f"Testmode: {entity_registry_update_msg}")

    ws.close()
    
def process_file(input=None, test=False, counter=0 ):
    with open(input, newline='') as csvfile:
        data = csv.reader(csvfile, delimiter=';')
        n: int = 0
        count: int = int(counter)
        for row in data:
            if count != 0 and count == n:
                sys.exit(0)
            old = row[1]
            new = row[2]
            entity_data = list_entities(old)
            rename_data = create_rename_data(entity_data, old, new)
            n = n + 1
            if old != new:
                entity_data = list_entities(old)
                if entity_data:
                    rename_data = create_rename_data(entity_data, old, new)
                    rename_entities(rename_data, args.test)
                else:
                    print(f"No entities found for: {old}")
            else:
                print("No diff between old and new entity_id")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="HomeAssistant Entity Renamer")
    parser.add_argument('--search', dest='search_regex', help='Regular expression for search. Note: Only searches entity IDs.')
    parser.add_argument('--replace', dest='replace_regex', help='Regular expression for replace')
    parser.add_argument('--output-csv', dest='output_csv', help='Output preview table to CSV.')
    parser.add_argument('--input', dest="input", default=None, help='Input file for renaming.')
    parser.add_argument('-t', '--test', default=False, action='store_true', help='Testmode for input file mode')
    parser.add_argument('-c', '--count', default=0,  help='Counter for filemode')
    args = parser.parse_args()
    
    print(args)
    
    if args.input != None:
        process_file(args.input, args.test, args.count)
        
    elif args.search_regex:
        entity_data = list_entities(args.search_regex)

        if entity_data:
            process_entities(entity_data, args.search_regex, args.replace_regex, args.output_csv, args.input)
        else:
            print("No entities found matching the search regex.")
            sys.exit(99)
    else:
        parser.print_help()
