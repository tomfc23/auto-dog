import streamlit as st
import json
import requests
import pandas as pd
import os

# --- Configuration & Caching ---
st.set_page_config(page_title="DOTD EV Calculator", layout="wide")

@st.cache_data
def load_config(file_name):
    if not os.path.exists(file_name):
        return {}
    with open(file_name, 'r') as f:
        return json.load(f)

@st.cache_data(ttl=600)
def fetch_poll_data(sport):
    try:
        id_urls = 'https://dotd-ids.tomfconreal.workers.dev/'
        r = requests.get(id_urls, timeout=5)
        if r.status_code != 200:
            return None, "Failed to fetch sport IDs."
        
        data = r.json()
        sport_id = data.get(sport)
        if not sport_id:
            return None, f"No ID found for sport: {sport}"
        
        poll_worker = f'https://dotd.tomfconreal.workers.dev/?url=https://api.real.vg/polls/{sport_id}'
        r = requests.get(poll_worker, timeout=5)
        if r.status_code != 200:
            return None, "Failed to fetch poll data."

        return r.json(), None
    except Exception as e:
        return None, str(e)

# --- Logic & Computation ---

def american_to_prob(odds):
    if odds > 0:
        return 100 / (odds + 100)
    else:
        return abs(odds) / (abs(odds) + 100)

def prob_to_american(prob):
    if prob <= 0: return 0
    if prob >= 1: return -10000
    if prob > 0.5:
        return int(-((prob * 100) / (1 - prob)))
    else:
        return int(((1 - prob) * 100) / prob)

def calc_fair_prob_from_two_sides(odds1, odds2):
    p1 = american_to_prob(odds1)
    p2 = american_to_prob(odds2)
    return p1 / (p1 + p2)

def calculate_payout(rank, real_odds):
    """
    Fixed Payout Logic: 
    If odds are negative (e.g. -120), payout is (100 / 120) * 100 = 83.33.
    If odds are positive (e.g. 120), payout is 120.
    Final = (20 * Rank) + Odds_Payout.
    """
    rank_bonus = 20 * rank
    if real_odds < 0:
        # $100 applied to odds logic
        odds_payout = (100 / abs(real_odds)) * 100
    else:
        odds_payout = real_odds
    return rank_bonus + odds_payout

def process_poll_data(raw_data, team_config):
    if not raw_data or 'poll' not in raw_data: return {}
    poll = raw_data['poll']
    options = poll.get('options', [])
    abbr_to_id = {v['abbrevation']: k for k, v in team_config.items() if v.get('league_id') == 6}
    dog_snapshot = {}
    total_votes = 0
    for option in options:
        name = option['label']
        votes = option['count']
        total_votes += votes
        try:
            odds_val = int(str(option['odds']).replace('+', ''))
        except:
            odds_val = 0
        dog_snapshot[name] = {'name': name, 'odds': odds_val, 'votes': votes, 'team_id': abbr_to_id.get(name)}
    
    sorted_keys = sorted(dog_snapshot, key=lambda k: dog_snapshot[k]['votes'], reverse=True)
    for rank, key in enumerate(sorted_keys, 1):
        dog_snapshot[key]['rank'] = rank
    return dog_snapshot

def get_fair_probs_from_file(dog_data, market_config, sport='nhl'):
    if not os.path.exists('odds.json'): return {}, {}
    with open('odds.json', 'r') as f:
        odds_data = json.load(f)
    sport_data = odds_data.get(sport, {})
    if not sport_data: return {}, {}
    
    averaged_probs = {}
    detailed_odds = {}
    
    for event_id, event in sport_data.items():
        keys = list(event.keys())
        if len(keys) < 5: continue
        t1_id, t2_id = keys[3], keys[4]
        
        book_pairs = []
        for src_id, data in event[t1_id].items():
            if src_id in event[t2_id]:
                o1 = data['odds']
                o2 = event[t2_id][src_id]['odds']
                
                # Rule: Remove if both sides are -110
                if o1 == -110 and o2 == -110:
                    continue
                
                fair_p1 = calc_fair_prob_from_two_sides(o1, o2)
                book_name = market_config.get(str(src_id), f"Book {src_id}")
                book_pairs.append({"Book": book_name, "Team Odds": o1, "Opponent Odds": o2, "FairProb": fair_p1})

        if book_pairs:
            avg_p1 = sum(b['FairProb'] for b in book_pairs) / len(book_pairs)
            averaged_probs[str(t1_id)] = avg_p1
            averaged_probs[str(t2_id)] = 1 - avg_p1
            
            detailed_odds[str(t1_id)] = book_pairs
            detailed_odds[str(t2_id)] = [
                {"Book": b['Book'], "Team Odds": b['Opponent Odds'], "Opponent Odds": b['Team Odds'], "FairProb": 1-b['FairProb']} 
                for b in book_pairs
            ]

    return averaged_probs, detailed_odds

# --- Main App ---

st.title("Automatic DOTD EV Calculator")

# Load Configs
team_config = load_config('team_config.json')
market_config = load_config('market_config.json')

if "manual_probs" not in st.session_state:
    st.session_state.manual_probs = {}

# --- Logic Processing ---
raw_poll, err = fetch_poll_data(sport="nhl")
if err or not team_config:
    st.error("Missing configuration or API error.")
    st.stop()

dog_data = process_poll_data(raw_poll, team_config)
file_probs, all_detailed_odds = get_fair_probs_from_file(dog_data, market_config, sport='nhl')
final_probs = {**file_probs, **st.session_state.manual_probs}

results_list = []
for name, data in dog_data.items():
    tid = str(data.get('team_id'))
    rank = data['rank']
    real_odds = data['odds']
    fair_prob = final_probs.get(tid)
    payout = calculate_payout(rank, real_odds)
    ev_val = round(payout * fair_prob, 2) if fair_prob else 0
    
    results_list.append({
        "Team": name, "TeamID": tid, "Rank": rank, 
        "Real Odds": real_odds, "Calc Payout": round(payout, 2),
        "Fair Prob": fair_prob, "EV": ev_val, "Missing": fair_prob is None
    })

df_display = pd.DataFrame(results_list)

# --- Sidebar: Manual Input & Settings ---
with st.sidebar:
    st.header("🛠 Manual Odds Entry")
    missing = df_display[df_display['Missing']]
    
    if not missing.empty:
        st.warning(f"{len(missing)} teams missing market odds.")
        for _, row in missing.iterrows():
            with st.expander(f"Entry: {row['Team']}"):
                s1 = st.number_input(f"{row['Team']} Odds", key=f"s1_{row['TeamID']}", step=1)
                s2 = st.number_input("Opponent Odds", key=f"s2_{row['TeamID']}", step=1)
                if st.button("Save Fair Odds", key=f"btn_{row['TeamID']}"):
                    if s1 != 0 and s2 != 0:
                        st.session_state.manual_probs[row['TeamID']] = calc_fair_prob_from_two_sides(s1, s2)
                        st.rerun()
    else:
        st.success("All teams have market odds.")

    if st.session_state.manual_probs:
        if st.button("Clear Manual Inputs"):
            st.session_state.manual_probs = {}
            st.rerun()
            
    st.divider()
    if st.button("🔄 Refresh Data"):
        st.cache_data.clear()
        st.rerun()

# --- Main Table ---
st.subheader("📊 Results")
st.info("Click a row to view the detailed bookmaker breakdown and fair averages below.")

valid_df = df_display[~df_display['Missing']].sort_values("EV", ascending=False).reset_index(drop=True)
selection = st.dataframe(
    valid_df[['Team', 'Rank', 'Real Odds', 'Calc Payout', 'Fair Prob', 'EV']],
    use_container_width=True,
    hide_index=True,
    on_select="rerun",
    selection_mode="single-row"
)

# --- Detailed Odds Breakdown ---
selected_rows = selection.get("selection", {}).get("rows", [])
if selected_rows:
    idx = selected_rows[0]
    row_data = valid_df.iloc[idx]
    tid = row_data['TeamID']
    
    st.divider()
    st.subheader(f"🔍 Market Breakdown: {row_data['Team']}")
    
    # Averaged No-Vig Highlight
    avg_american = prob_to_american(row_data['Fair Prob'])
    st.metric("Averaged No-Vig Odds", f"{avg_american:+d}", 
              help="The fair market price after stripping vig from all available books.")
    
    if tid in all_detailed_odds:
        details = pd.DataFrame(all_detailed_odds[tid])
        details['No-Vig American'] = details['FairProb'].apply(prob_to_american)
        st.table(details[['Book', 'Team Odds', 'Opponent Odds', 'No-Vig American']])
    else:
        st.info("No individual book data available; using manual override probability.")
