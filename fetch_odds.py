import requests
import json
import os
from datetime import datetime, date
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright
from urllib.parse import urlparse, parse_qs

# Global constant for easy access when imported
LEAGUE_IDS = {
    "nfl": 1,
    "cfb": 2,
    "nba": 3,
    "cbb": 4,
    "mlb": 5,
    "nhl": 6,
    "wnba": 7,
    "pga": 8
}

def get_market_ids():
    url = 'https://content.unabated.com/markets/game-odds/b_gameodds.json'
    r = requests.get(url)
    if r.status_code != 200:
        print(r.status_code)
        return False

    data = r.json()
    markets = data.get('marketSources')
    market_data = {}
    for market in markets:
        id = market['id']
        name = market['name']
        market_data[id] = name

    with open('market_config.json', 'w') as f:
        json.dump(market_data, f, indent=4)
    
    return True

def get_team_data():
    url = 'https://content.unabated.com/markets/game-odds/b_gameodds.json'
    r = requests.get(url)
    if r.status_code != 200:
        print(r.status_code)
        return False

    data = r.json()
    teams = data.get('teams')
    team_data = {}
    for team in teams:
        new_team = teams[team]
        name = new_team['name']
        abbr = new_team['abbreviation']
        event_id = new_team['eventId']
        team_id = new_team['id']
        league_id = new_team['leagueId']
        team_data[team_id] = {
            'name': name,
            'abbrevation': abbr,
            'event_id': event_id,
            'team_id': team_id,
            'league_id': league_id
        }
    
    with open('team_config.json', 'w') as f:
        json.dump(team_data,f,indent=4)

def get_market_name(id):
    # Check if file exists, if not, fetch it first
    if not os.path.exists('market_config.json'):
        print("Market config not found, fetching...")
        get_market_ids()

    try:
        with open('market_config.json', 'r') as f:
            data = json.load(f)
        return data.get(str(id), "Unknown Market")
    except FileNotFoundError:
        return "Unknown Market"

def get_event_data(league_id, v_value):
    url = f'https://content.unabated.com/markets/game-odds/b_gameodds.json?v={v_value}'
    r = requests.get(url)
    if r.status_code != 200:
        print(r.status_code)
        return False
    
    data = r.json()
    events_data = data.get('gameOddsEvents')
    
    # Key construction based on league ID
    league_key = f'lg{league_id}:pt1:pregame'
    
    # Safety check if the league key exists in response
    if league_key not in events_data:
        print(f"No data found for league {league_id}")
        return {}

    league_data = events_data[league_key]
    all_events_data = {}

    for event in league_data:
        event_id = event['eventId']
        utc_string = event['eventStart']
        utc_dt = datetime.fromisoformat(utc_string).replace(tzinfo=ZoneInfo("UTC"))
        est_dt = utc_dt.astimezone(ZoneInfo("America/New_York"))
        est_date = est_dt.date()
        today = date.today()
        
        # Filter for today's games (based on EST)
        if str(est_date) != str(today): 
            break

        event_teams = event.get('eventTeams')
        team_ids = [event_teams['0']['id'], event_teams['1']['id']]
        
        game_name = event['name']
        odds_sources = event.get('gameOddsMarketSourcesLines')
        
        side1_data = {}
        side0_data = {}

        for source in odds_sources:
            # Determine bet type based on league (NHL uses different type)
            if league_id != 6:
                bet_type = 2
            else:
                bet_type = 1

            if source[:3] == 'si1':
                ml_source = odds_sources[source].get(f"bt{bet_type}", None)
                if ml_source is None:
                    continue

                market_id = ml_source['marketSourceId']
                american_odds = ml_source['americanPrice']
                line = ml_source['points'] if bet_type == 2 else None
                modified_timestamp = ml_source['modifiedOn']
                market_name = get_market_name(market_id)
                
                side1_data[market_id] = {
                    'odds': american_odds,
                    'timestamp': modified_timestamp,
                    'market_name' : market_name,
                    'line': line
                }
            elif source[:3] == 'si0':
                ml_source = odds_sources[source].get(f"bt{bet_type}", None)
                if ml_source is None:
                    continue
                
                american_odds = ml_source['americanPrice']
                modified_timestamp = ml_source['modifiedOn']
                market_id = ml_source['marketSourceId']
                market_name = get_market_name(market_id)
                line = ml_source['points'] if bet_type == 2 else None
                
                side0_data[market_id] = {
                    'odds': american_odds,
                    'timestamp': modified_timestamp,
                    'market_name': market_name,
                    'line': line
                }
            else:
                continue
        
        all_events_data[event_id] = {
            "start_time" : utc_string,
            'name' : game_name,
            "timestamp": str(datetime.now()),
            team_ids[0] : side0_data,
            team_ids[1] : side1_data,
        }
    
    return all_events_data

def write_to_odds(all_data):
    with open('odds.json', "w") as f:
        json.dump(all_data,f,indent=4)
    return True

def get_unabated_v_parameter():
    v_param = None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        try:
            with page.expect_request(lambda request: "b_gameodds.json" in request.url, timeout=60000) as request_info:
                page.goto("https://unabated.com/nba/odds", wait_until="domcontentloaded")
                captured_url = request_info.value.url
                parsed_url = urlparse(captured_url)
                params = parse_qs(parsed_url.query)
                v_param = params.get('v', [None])[0]
        except Exception as e:
            print(f"Error fetching v parameter: {e}")
        finally:
            browser.close()
            
    return v_param

# This block ensures the code below only runs if you run this file directly.
# It will NOT run if you import this file into another script.
if __name__ == "__main__":
    all_data = {}
    v_value = get_unabated_v_parameter()
    
    if v_value:
        print(f"Got V parameter: {v_value}")
        # Example: Fetching NHL data
        nhl_events_data = get_event_data(LEAGUE_IDS['nhl'], v_value)
        all_data['nhl'] = nhl_events_data
        
        if write_to_odds(all_data):
            print("Successfully wrote to odds.json")
    else:
        print("Failed to retrieve V parameter.")