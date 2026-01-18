import streamlit as st
import json
import requests
import pandas as pd
import os
import re

# --- Configuration ---
st.set_page_config(page_title="DOTD Live EV Optimizer", layout="wide")

# --- Helper Functions ---

def load_config(file_name):
    """Loads metadata files like team_config and market_config."""
    if not os.path.exists(file_name):
        return {}
    try:
        with open(file_name, 'r') as f:
            return json.load(f)
    except:
        return {}

def fetch_live_market_data(market_config):
    """
    Fetches the 'v' parameter and market odds directly from Unabated API.
    Returns: (averaged_probs, detailed_odds, debug_info)
    """
    debug_info = {"v_param": None, "api_url": None, "error": None}
    
    try:
        session = requests.Session()
        # 1. Get the 'v' parameter (usually found in the main odds page JS)
        main_page = session.get("https://www.unabated.com/odds/nhl", timeout=10).text
        v_match = re.search(r'"v":"(.*?)"', main_page)
        v_param = v_match.group(1) if v_match else None
        debug_info["v_param"] = v_param
        
        if not v_param:
            debug_info["error"] = "Could not find 'v' parameter in page"
            return {}, {}, debug_info

        # 2. Fetch the live event data (League 6 = NHL)
        api_url = f"https://api.unabated.com/api/v2/events?league_id=6&v={v_param}"
        debug_info["api_url"] = api_url
        response = session.get(api_url, timeout=10).json()
        
        averaged_probs = {}
        detailed_odds = {}

        for event in response.get('events', []):
            # Unabated structure: event -> teams -> markets
            teams = event.get('teams', [])
            if len(teams) < 2: continue
            
            t1, t2 = teams[0], teams[1]
            t1_id, t2_id = str(t1['id']), str(t2['id'])
            
            # Extract Moneyline markets (Market Type 1)
            t1_markets = t1.get('markets', {}).get('1', {})
            t2_markets = t2.get('markets', {}).get('1', {})
            
            book_pairs = []
            for book_id, t1_data in t1_markets.items():
                if book_id in t2_markets:
                    o1 = t1_data.get('odds')
                    o2 = t2_markets[book_id].get('odds')
                    
                    if o1 is None or o2 is None or (o1 == -110 and o2 == -110):
                        continue
                    
                    fair_p1 = calc_fair_prob_from_two_sides(o1, o2)
                    book_name = market_config.get(str(book_id), f"Book {book_id}")
                    book_pairs.append({
                        "Book": book_name, "Team Odds": o1, 
                        "Opponent Odds": o2, "FairProb": fair_p1
                    })

            if book_pairs:
                avg_p1 = sum(b['FairProb'] for b in book_pairs) / len(book_pairs)
                averaged_probs[t1_id] = avg_p1
                averaged_probs[t2_id] = 1 - avg_p1
                
                detailed_odds[t1_id] = book_pairs
                detailed_odds[t2_id] = [
                    {"Book": b['Book'], "Team Odds": b['Opponent Odds'], 
                     "Opponent Odds": b['Team Odds'], "FairProb": 1-b['FairProb']} 
                    for b in book_pairs
                ]

        return averaged_probs, detailed_odds, debug_info
    except Exception as e:
        debug_info["error"] = str(e)
        st.error(f"Live Fetch Error: {e}")
        return {}, {}, debug_info

def fetch_poll_data(sport):
    """Fetches the current DOTD poll options and votes."""
    try:
        id_urls = 'https://dotd-ids.tomfconreal.workers.dev/'
        r = requests.get(id_urls, timeout=5)
        data = r.json()
        sport_id = data.get(sport)
        
        poll_worker = f'https://dotd.tomfconreal.workers.dev/?url=https://api.real.vg/polls/{sport_id}'
        r = requests.get(poll_worker, timeout=5)
        return r.json(), None
    except Exception as e:
        return None, str(e)

def american_to_prob(odds):
    return 100 / (odds + 100) if odds > 0 else abs(odds) / (abs(odds) + 100)

def prob_to_american(prob):
    if prob <= 0: return 0
    return int(-((prob * 100) / (1 - prob))) if prob > 0.5 else int(((1 - prob) * 100) / prob)

def calc_fair_prob_from_two_sides(odds1, odds2):
    p1, p2 = american_to_prob(odds1), american_to_prob(odds2)
    return p1 / (p1 + p2)

def calculate_payout(rank, real_odds):
    rank_bonus = 20 * rank
    odds_payout = (100 / abs(real_odds)) * 100 if real_odds < 0 else real_odds
    return rank_bonus + odds_payout

def process_poll_data(raw_data, team_config):
    if not raw_data or 'poll' not in raw_data: return {}
    options = raw_data['poll'].get('options', [])
    # Filter for NHL (League 6) abbreviations
    abbr_to_id = {v['abbrevation']: k for k, v in team_config.items() if v.get('league_id') == 6}
    
    dog_snapshot = {}
    for opt in options:
        name = opt['label']
        try:
            odds_val = int(str(opt['odds']).replace('+', ''))
        except:
            odds_val = 0
        dog_snapshot[name] = {'name': name, 'odds': odds_val, 'votes': opt['count'], 'team_id': abbr_to_id.get(name)}
    
    sorted_keys = sorted(dog_snapshot, key=lambda k: dog_snapshot[k]['votes'], reverse=True)
    for rank, key in enumerate(sorted_keys, 1):
        dog_snapshot[key]['rank'] = rank
    return dog_snapshot

# --- Initialize Session State ---
if "data_loaded" not in st.session_state:
    st.session_state.data_loaded = False
    st.session_state.manual_probs = {}
    st.session_state.live_probs = {}
    st.session_state.all_detailed_odds = {}
    st.session_state.dog_data = {}
    st.session_state.team_config = {}
    st.session_state.market_config = {}
    st.session_state.api_debug = {}

# --- Load Data on First Run ---
if not st.session_state.data_loaded:
    status_placeholder = st.empty()
    progress_bar = st.progress(0)
    
    try:
        # Step 1: Load configs
        status_placeholder.info("📂 Loading configuration files...")
        progress_bar.progress(20)
        st.session_state.team_config = load_config('team_config.json')
        st.session_state.market_config = load_config('market_config.json')
        
        if st.session_state.team_config:
            status_placeholder.success(f"✓ Loaded {len(st.session_state.team_config)} teams from config")
        else:
            status_placeholder.warning("⚠ No team config found")
        
        # Step 2: Fetch poll data
        status_placeholder.info("🗳️ Fetching DOTD poll data...")
        progress_bar.progress(40)
        raw_poll, err = fetch_poll_data(sport="nhl")
        if err:
            status_placeholder.error(f"Poll fetch error: {err}")
        else:
            st.session_state.dog_data = process_poll_data(raw_poll, st.session_state.team_config)
            status_placeholder.success(f"✓ Loaded {len(st.session_state.dog_data)} poll options")
        
        # Step 3: Fetch live market data
        status_placeholder.info("📊 Fetching live odds from Unabated API...")
        progress_bar.progress(60)
        live_probs, all_detailed_odds, debug_info = fetch_live_market_data(st.session_state.market_config)
        st.session_state.api_debug = debug_info
        
        status_placeholder.info("🔢 Processing market data...")
        progress_bar.progress(80)
        st.session_state.live_probs = live_probs
        st.session_state.all_detailed_odds = all_detailed_odds
        
        if live_probs:
            num_books = len(set(
                book['Book'] 
                for odds_list in all_detailed_odds.values() 
                for book in odds_list
            ))
            status_placeholder.success(f"✓ Loaded odds for {len(live_probs)} teams from {num_books} books")
        else:
            status_placeholder.warning("⚠ No live market data retrieved - check API or enter odds manually")
        
        # Complete
        progress_bar.progress(100)
        st.session_state.data_loaded = True
        
        status_placeholder.success("✅ All data loaded successfully!")
        import time
        time.sleep(1)
        status_placeholder.empty()
        progress_bar.empty()
        
    except Exception as e:
        status_placeholder.error(f"❌ Error during data load: {e}")
        progress_bar.empty()

# --- Main App ---
st.title("🏒 DOTD Live Optimizer")

# Check for config errors
if not st.session_state.team_config:
    st.error("Setup Error: Ensure team_config.json is in your directory.")
    st.stop()

# Combine live and manual probabilities
final_probs = {**st.session_state.live_probs, **st.session_state.manual_probs}

# Calculate results
results_list = []
for name, data in st.session_state.dog_data.items():
    tid = str(data.get('team_id'))
    fair_prob = final_probs.get(tid)
    payout = calculate_payout(data['rank'], data['odds'])
    ev_val = round(payout * fair_prob, 2) if fair_prob else 0
    
    results_list.append({
        "Team": name, "TeamID": tid, "Rank": data['rank'], 
        "Real Odds": data['odds'], "Calc Payout": round(payout, 2),
        "Fair Prob": fair_prob, "EV": ev_val, "Missing": fair_prob is None
    })

df_display = pd.DataFrame(results_list)

# --- Sidebar: Manual Overrides ---
with st.sidebar:
    st.header("🛠 Live Data Settings")
    if st.button("🔄 Refresh All Data"):
        st.session_state.data_loaded = False
        st.rerun()
    
    st.divider()
    
    # API Debug Info
    with st.expander("🔍 API Debug Info"):
        if st.session_state.api_debug:
            st.code(f"v_param: {st.session_state.api_debug.get('v_param', 'Not found')}")
            if st.session_state.api_debug.get('api_url'):
                st.code(f"API URL: {st.session_state.api_debug['api_url']}", language="text")
            if st.session_state.api_debug.get('error'):
                st.error(f"Error: {st.session_state.api_debug['error']}")
            st.caption(f"Teams fetched: {len(st.session_state.live_probs)}")
    
    st.divider()
    st.subheader("Manual Odds Entry")
    missing = df_display[df_display['Missing']]
    if not missing.empty:
        for _, row in missing.iterrows():
            with st.expander(f"Entry: {row['Team']}"):
                s1 = st.number_input(f"{row['Team']} Odds", key=f"s1_{row['TeamID']}", step=1)
                s2 = st.number_input("Opponent Odds", key=f"s2_{row['TeamID']}", step=1)
                if st.button("Save", key=f"btn_{row['TeamID']}"):
                    if s1 != 0 and s2 != 0:
                        st.session_state.manual_probs[row['TeamID']] = calc_fair_prob_from_two_sides(s1, s2)
                        st.rerun()
    else:
        st.success("All teams have live market data.")

# --- Main Table ---
st.subheader("📊 Profitability Report")
valid_df = df_display[~df_display['Missing']].sort_values("EV", ascending=False).reset_index(drop=True)

if not valid_df.empty:
    selection = st.dataframe(
        valid_df[['Team', 'Rank', 'Real Odds', 'Calc Payout', 'Fair Prob', 'EV']],
        use_container_width=True, hide_index=True, on_select="rerun", selection_mode="single-row"
    )

    selected_rows = selection.get("selection", {}).get("rows", [])
    if selected_rows:
        row_data = valid_df.iloc[selected_rows[0]]
        tid = row_data['TeamID']
        st.divider()
        st.subheader(f"🔍 Market Breakdown: {row_data['Team']}")
        
        avg_no_vig = prob_to_american(row_data['Fair Prob'])
        st.metric("Fair Market Price", f"{avg_no_vig:+d}")
        
        if tid in st.session_state.all_detailed_odds:
            details = pd.DataFrame(st.session_state.all_detailed_odds[tid])
            details['No-Vig American'] = details['FairProb'].apply(prob_to_american)
            st.table(details[['Book', 'Team Odds', 'Opponent Odds', 'No-Vig American']])
else:
    st.warning(f"No valid EV data available. {len(df_display[df_display['Missing']])} teams missing odds. Use the sidebar to enter odds manually.")
