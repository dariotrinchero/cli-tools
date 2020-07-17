#!/usr/bin/env python3

from sys import argv
from os import system
from requests import get
from datetime import datetime, timedelta
from dateutil.parser import parse

#---------------------------------------------------------------------------------------------------
# Overview
#---------------------------------------------------------------------------------------------------
#
# Retrieve loadshedding times for zones in City of Cape Town municipality, & schedule shutdown
# 10min prior to each cut. 
#
# The first (optional) argument to the script will override current loadshedding stage.
#
#---------------------------------------------------------------------------------------------------

#---------------------------------------------------------------------------------------------------
# Global Configuration
#---------------------------------------------------------------------------------------------------

# Zone number for City of Cape Town municipality
zone = 3 # Somerset West

# Command to be scheduled for 10min before power cut
notify_cmd = "notify-send Loadshedding 'Shutting down in 5min'"
shutdown_cmd = f'XDG_RUNTIME_DIR=/run/user/$(id -u) {notify_cmd}; shutdown +5'

#---------------------------------------------------------------------------------------------------

def ewn_api(api_func, resource_name):
    ''' Call EWN loadshedding API to fetch resource using given API function;
        handle errors, & return response. '''
    print(f'[rescom] Fetching City of Cape Town loadshedding {resource_name}')
    response = get(f'https://ewn.co.za/assets/loadshedding/api/{api_func}')
    if not response:
        exit(f'[rescom] Lookup of {resource_name} gave status code {response.status_code}')
    return response

if __name__ == '__main__':
    # Get current loadshedding stage
    if len(argv) > 1: stage = argv[1]
    else: stage = ewn_api('status', 'stage').text

    if stage in ('Not Load Shedding', '0'):
        print('[rescom] There is currently no loadshedding')
        exit()
    else: print(f'[rescom] Using schedule for stage {stage} loadshedding')

    # Get power cut schedule
    schedule = ewn_api('schedulesctfeb2015', 'schedule').json()
    now = datetime.now()
    updated = parse(schedule['LastModified'], fuzzy=True).date()
    if updated != now.date():
        print(f'[rescom] Schedule was last updated on {updated}')

    for cut in schedule['Schedules']:
        # Skip cuts outside next 24h, or in other stages / zones
        if stage != cut['Stage'] or zone not in cut['Zones']: continue
        shutdown = datetime.combine(parse(cut['DateShort']).date(),
                parse(cut['StartTime']).time())
        if not now < shutdown < now + timedelta(days=1): continue

        # Schedule shutdown
        shutdown -= timedelta(minutes=10)
        if shutdown < now: exit(system("shutdown now")) # emergency shutdown

        print(f'[rescom] Scheduling shutdown for {shutdown}')
        time_str = f'{shutdown.time().isoformat("minutes")} {shutdown.date()}'
        if system(f'echo "{shutdown_cmd}" | at -M {time_str}'):
            print(f'[rescom] Failed to schedule shutdown for {shutdown}')

